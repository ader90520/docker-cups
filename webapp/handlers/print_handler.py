#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import uuid
import subprocess
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

# -------------------------------------------------------------
# 1. 动态感知当前设备环境（大内存开启 OpenCV，小内存纯 PIL/NumPy 运行）
# -------------------------------------------------------------
HAVE_OPENCV = False
try:
    import cv2
    HAVE_OPENCV = True
except ImportError:
    HAVE_OPENCV = False

def log_debug(msg):
    try:
        with open("/tmp/dewarp_debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(f"[PrintLog] {msg}", flush=True)

# -------------------------------------------------------------
# 2. 小内存海纳思盒子专属轻量级拉平算法（纯 PIL + NumPy，极省 RAM）
# -------------------------------------------------------------
def measure_line_skew(gray_img):
    try:
        w, h = gray_img.size
        tw = 320
        th = max(10, int(h * (320.0 / w)))
        small = gray_img.resize((tw, th), Image.Resampling.BILINEAR)
        arr = np.array(small, dtype=np.float32)

        grad = np.abs(arr[2:, :] - arr[:-2, :])
        grad[grad < 35] = 0.0

        best_angle = 0.0
        max_var = 0.0
        for angle in np.linspace(-6.0, 6.0, 25):
            rot = Image.fromarray(grad).rotate(angle, resample=Image.Resampling.NEAREST)
            var = np.var(np.sum(np.array(rot), axis=1))
            if var > max_var:
                max_var = var
                best_angle = angle
        return best_angle
    except Exception:
        return 0.0

def adaptive_perspective_flatten_pil(pil_img):
    try:
        w, h = pil_img.size
        gray = pil_img.convert("L")

        top_crop = gray.crop((0, 0, w, int(h * 0.35)))
        bot_crop = gray.crop((0, int(h * 0.65), w, h))

        deg_top = measure_line_skew(top_crop)
        deg_bot = measure_line_skew(bot_crop)

        dy_top = int(round(np.tan(np.radians(deg_top)) * w))
        dy_bot = int(round(np.tan(np.radians(deg_bot)) * w))

        tl_y = max(0, -dy_top)
        tr_y = max(0, dy_top)
        bl_y = h - max(0, dy_bot)
        br_y = h - max(0, -dy_bot)

        if abs(dy_top) >= 3 or abs(dy_bot) >= 3:
            quad = (0, tl_y, 0, bl_y, w, br_y, w, tr_y)
            pil_img = pil_img.transform((w, h), Image.Transform.QUAD, quad, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
            log_debug("PIL 模式: 曲率透视拉直完成")
    except Exception as e:
        log_debug(f"PIL 拉直跳过: {e}")
    return pil_img

def auto_crop_paper_boundaries_pil(img):
    try:
        w, h = img.size
        small = img.resize((300, int(h * (300.0 / w))), Image.Resampling.BILINEAR)
        gray = np.array(small.convert("L"), dtype=np.uint8)

        sw, sh = small.size
        left, top, right, bottom = 0, 0, sw, sh

        for x in range(int(sw * 0.08)):
            if np.mean(gray[:, x]) > 130:
                left = max(0, x - 2)
                break
        for x in range(sw - 1, int(sw * 0.90), -1):
            if np.mean(gray[:, x]) > 130:
                right = min(sw, x + 2)
                break
        for y in range(sh - 1, int(sh * 0.90), -1):
            if np.mean(gray[y, :]) > 130:
                bottom = min(sh, y + 2)
                break

        scale_x = w / float(sw)
        scale_y = h / float(sh)
        crop_box = (
            int(left * scale_x),
            int(top * scale_y),
            int(right * scale_x),
            int(bottom * scale_y)
        )

        if crop_box[2] - crop_box[0] > w * 0.85 and crop_box[3] - crop_box[1] > h * 0.85:
            cropped = img.crop(crop_box)
            return cropped.resize((w, h), Image.Resampling.BICUBIC)
    except Exception as e:
        log_debug(f"PIL 切除黑边异常: {e}")
    return img

# -------------------------------------------------------------
# 3. 大内存专属算法（仅当系统拥有 OpenCV 时调用）
# -------------------------------------------------------------
def order_points_cv(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def auto_perspective_crop_cv(cv_img):
    try:
        orig = cv_img.copy()
        h, w = cv_img.shape[:2]
        ratio = h / 500.0
        small_w = int(w / ratio)
        small = cv2.resize(cv_img, (small_w, 500), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blurred, 30, 100)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        edged = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)

        cnts, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:5]

        screen_cnt = None
        for c in cnts:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.contourArea(c) > (small_w * 500 * 0.4):
                screen_cnt = approx
                break

        if screen_cnt is not None:
            pts = screen_cnt.reshape(4, 2) * ratio
            rect = order_points_cv(pts)
            (tl, tr, br, bl) = rect
            max_w = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)))
            max_h = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)))

            dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype="float32")
            M = cv2.getPerspectiveTransform(rect, dst)
            warped = cv2.warpPerspective(orig, M, (max_w, max_h), flags=cv2.INTER_CUBIC)
            log_debug("OpenCV 模式: 四点透视矫正成功！")
            return warped
    except Exception as e:
        log_debug(f"OpenCV 透视跳过: {e}")
    return cv_img

# -------------------------------------------------------------
# 4. 全局核心流水线（大设备 OpenCV，小设备 PIL，色彩保护与加黑通用）
# -------------------------------------------------------------
def process_image_for_print(input_path, output_path):
    try:
        log_debug(f"处理图片流水线启动 (OpenCV加速={HAVE_OPENCV}): {input_path}")

        if HAVE_OPENCV:
            # ---------------- 大内存盒子通道 ----------------
            cv_img = cv2.imread(input_path)
            if cv_img is None:
                return False
            h, w = cv_img.shape[:2]
            if w > h:
                cv_img = cv2.rotate(cv_img, cv2.ROTATE_90_COUNTERCLOCKWISE)

            cv_img = auto_perspective_crop_cv(cv_img)
            cv_img = cv2.resize(cv_img, (2480, 3508), interpolation=cv2.INTER_CUBIC)

            b, g, r = cv2.split(cv_img)
            b_f, g_f, r_f = b.astype(np.float32), g.astype(np.float32), r.astype(np.float32)
            max_c = np.maximum(np.maximum(r_f, g_f), b_f)
            min_c = np.minimum(np.minimum(r_f, g_f), b_f)
            color_diff = max_c - min_c

            gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY).astype(np.float32)
            color_mask = color_diff > 18.0
            gray[color_mask] = np.clip(gray[color_mask] - color_diff[color_mask] * 1.5, 0, 255)

            bg = cv2.blur(gray, (55, 55)) + 1.0
            divided = (gray / bg) * 255.0

            out = np.full_like(divided, 255.0)
            ink_mask = divided < 210.0
            ink_vals = divided[ink_mask]
            clean_ink = np.clip((ink_vals - 10.0) * (210.0 / (210.0 - 10.0)), 0, 255)
            clean_ink = (clean_ink / 210.0) ** 1.35 * 180.0
            out[ink_mask] = clean_ink

            h_out, w_out = out.shape
            pad_x, pad_y = int(w_out * 0.01), int(h_out * 0.01)
            out[0:pad_y, :] = 255.0
            out[h_out-pad_y:, :] = 255.0
            out[:, 0:pad_x] = 255.0
            out[:, w_out-pad_x:] = 255.0

            res_uint8 = np.clip(out, 0, 255).astype(np.uint8)
            gaussian = cv2.GaussianBlur(res_uint8, (0, 0), 1.2)
            sharp = cv2.addWeighted(res_uint8, 1.4, gaussian, -0.4, 0)

            pil_res = Image.fromarray(sharp)
            pil_res.save(output_path, format="JPEG", quality=95, dpi=(300, 300))
        else:
            # ---------------- 小内存海纳思盒子通道 ----------------
            with Image.open(input_path) as disk_img:
                img = ImageOps.exif_transpose(disk_img.convert("RGB"))

            if img.width > img.height:
                img = img.rotate(270, expand=True)

            img = auto_crop_paper_boundaries_pil(img)
            img = adaptive_perspective_flatten_pil(img)
            img = img.resize((2480, 3508), Image.Resampling.BICUBIC)

            w, h = img.size
            r, g, b = img.split()
            r_arr, g_arr, b_arr = np.array(r, dtype=np.float32), np.array(g, dtype=np.float32), np.array(b, dtype=np.float32)

            # 色彩保护加黑
            max_c = np.maximum(np.maximum(r_arr, g_arr), b_arr)
            min_c = np.minimum(np.minimum(r_arr, g_arr), b_arr)
            color_diff = max_c - min_c

            gray_arr = 0.299 * r_arr + 0.587 * g_arr + 0.114 * b_arr
            color_mask = color_diff > 18.0
            gray_arr[color_mask] = np.clip(gray_arr[color_mask] - color_diff[color_mask] * 1.5, 0, 255)

            contrast_img = Image.fromarray(gray_arr.astype(np.uint8))
            bg = contrast_img.filter(ImageFilter.BoxBlur(radius=45))
            bg_arr = np.array(bg, dtype=np.float32) + 1.0

            divided = (gray_arr / bg_arr) * 255.0
            out = np.full_like(divided, 255.0)

            mask_front = divided < 210.0
            ink_vals = divided[mask_front]
            clean_ink = np.clip((ink_vals - 10.0) * (210.0 / (210.0 - 10.0)), 0, 255)
            clean_ink = (clean_ink / 210.0) ** 1.35 * 180.0
            out[mask_front] = clean_ink

            pad_x, pad_y = max(1, int(w * 0.01)), max(1, int(h * 0.01))
            out[0:pad_y, :] = 255.0
            out[h-pad_y:h, :] = 255.0
            out[:, 0:pad_x] = 255.0
            out[:, w-pad_x:w] = 255.0

            clean_gray = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
            sharp = clean_gray.filter(ImageFilter.UnsharpMask(radius=1.2, percent=140, threshold=2))
            sharp_rgb = Image.merge("RGB", [sharp, sharp, sharp])
            sharp_rgb.save(output_path, format="JPEG", quality=95, dpi=(300, 300))

        log_debug(f"图像流水线优化完成: {output_path}")
        return True
    except Exception as e:
        log_debug(f"图像流水线处理异常: {e}")
        return False

def clean_old_tmp_files(directory, max_age_seconds=1800):
    try:
        now = time.time()
        for f in os.listdir(directory):
            p = os.path.join(directory, f)
            if os.path.isfile(p) and (now - os.path.getmtime(p) > max_age_seconds):
                os.remove(p)
    except Exception:
        pass

# -------------------------------------------------------------
# 5. 打印 Handler（统一采用 fit-to-page 和 position=center，杜绝缩小）
# -------------------------------------------------------------
class PrintHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到上传文件")
                return

            jobs = []
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            for f in files:
                ext = os.path.splitext(f["filename"])[-1].lower()
                token = uuid.uuid4().hex[:8]
                src_path = os.path.join(UPLOAD_DIR, f"raw_{token}{ext}")
                with open(src_path, "wb") as out:
                    out.write(f["body"])

                target_file = src_path
                if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
                    enhanced_path = os.path.join(UPLOAD_DIR, f"opt_{token}.jpg")
                    if process_image_for_print(src_path, enhanced_path):
                        target_file = enhanced_path

                # 统一使用 fit-to-page，彻底解决缩小到左下角的问题
                cmd = [
                    "lp",
                    "-d", printer,
                    "-n", str(copies),
                    "-o", "media=A4",
                    "-o", "PageSize=A4",
                    "-o", "fit-to-page",
                    "-o", "position=center",
                    target_file
                ]

                log_debug(f"下发打印指令: {' '.join(cmd)}")
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            clean_old_tmp_files(UPLOAD_DIR)
            self.write_json(True, f"共 {len(files)} 个文件任务已派发", job=", ".join(jobs))
        except Exception as e:
            log_debug(f"打印异常: {e}")
            self.write_json(False, f"打印服务异常: {str(e)}")
