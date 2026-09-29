#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import uuid
import subprocess
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

HAVE_OPENCV = False
try:
    import cv2
    HAVE_OPENCV = True
except Exception:
    HAVE_OPENCV = False

ALLOWED_PRINT_EXTS = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.webp', '.heic'}

def log_debug(msg):
    try:
        with open("/tmp/dewarp_debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(f"[PrintLog] {msg}", flush=True)

def safe_clean_edge_shadows(arr):
    try:
        h, w = arr.shape[:2]
        top_h = int(h * 0.035)
        bot_h = int(h * 0.965)
        left_w = int(w * 0.025)
        right_w = int(w * 0.975)

        for y in range(top_h):
            mask = arr[y, :] < 130
            if np.sum(mask) > 0:
                arr[y, mask] = 255.0

        for y in range(bot_h, h):
            mask = arr[y, :] < 130
            if np.sum(mask) > 0:
                arr[y, mask] = 255.0

        for x in range(left_w):
            mask = arr[:, x] < 130
            if np.sum(mask) > 0:
                arr[mask, x] = 255.0

        for x in range(right_w, w):
            mask = arr[:, x] < 130
            if np.sum(mask) > 0:
                arr[mask, x] = 255.0

        pad_y = max(1, int(h * 0.005))
        pad_x = max(1, int(w * 0.005))
        arr[0:pad_y, :] = 255.0
        arr[h-pad_y:, :] = 255.0
        arr[:, 0:pad_x] = 255.0
        arr[:, w-pad_x:] = 255.0
    except Exception:
        pass
    return arr

def fit_to_a4_safe_frame(img, fill_ratio=0.97):
    target_w, target_h = 2480, 3508
    orig_w, orig_h = img.size

    ratio = min((target_w * fill_ratio) / orig_w, (target_h * fill_ratio) / orig_h)
    new_w = int(orig_w * ratio)
    new_h = int(orig_h * ratio)

    resized_img = img.resize((new_w, new_h), Image.Resampling.BICUBIC)
    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))

    pos_x = (target_w - new_w) // 2
    pos_y = (target_h - new_h) // 2
    canvas.paste(resized_img, (pos_x, pos_y))
    return canvas

def measure_line_skew(gray_img):
    try:
        w, h = gray_img.size
        tw = 320
        th = max(10, int(h * (320.0 / w)))
        small = gray_img.resize((tw, th), Image.Resampling.BILINEAR)
        arr = np.array(small, dtype=np.float32)

        grad = np.abs(arr[2:, :] - arr[:-2, :])
        grad[grad < 30] = 0.0

        best_angle = 0.0
        max_var = 0.0
        for angle in np.linspace(-5.0, 5.0, 21):
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
        top_crop = gray.crop((0, 0, w, int(h * 0.30)))
        bot_crop = gray.crop((0, int(h * 0.70), w, h))

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

        for x in range(int(sw * 0.06)):
            if np.mean(gray[:, x]) > 130:
                left = max(0, x - 1)
                break
        for x in range(sw - 1, int(sw * 0.94), -1):
            if np.mean(gray[:, x]) > 130:
                right = min(sw, x + 1)
                break
        for y in range(sh - 1, int(sh * 0.94), -1):
            if np.mean(gray[y, :]) > 130:
                bottom = min(sh, y + 1)
                break

        scale_x = w / float(sw)
        scale_y = h / float(sh)
        crop_box = (int(left * scale_x), int(top * scale_y), int(right * scale_x), int(bottom * scale_y))

        if crop_box[2] - crop_box[0] > w * 0.88 and crop_box[3] - crop_box[1] > h * 0.88:
            cropped = img.crop(crop_box)
            return cropped.resize((w, h), Image.Resampling.BICUBIC)
    except Exception:
        pass
    return img

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
        edged = cv2.Canny(blurred, 25, 90)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        edged = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)

        cnts, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:5]

        screen_cnt = None
        for c in cnts:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.contourArea(c) > (small_w * 500 * 0.45):
                screen_cnt = approx
                break

        if screen_cnt is not None:
            pts = screen_cnt.reshape(4, 2) * ratio
            rect = order_points_cv(pts)
            (tl, tr, br, bl) = rect
            max_w = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)))
            max_h = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)))

            if max_w > 100 and max_h > 100:
                dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype="float32")
                M = cv2.getPerspectiveTransform(rect, dst)
                warped = cv2.warpPerspective(orig, M, (max_w, max_h), flags=cv2.INTER_CUBIC)
                return warped
    except Exception as e:
        log_debug(f"OpenCV 透视跳过: {e}")
    return cv_img

def process_image_for_print(input_path, output_path):
    try:
        log_debug(f"流水线启动: {input_path} (OpenCV={HAVE_OPENCV})")

        if HAVE_OPENCV:
            cv_img = cv2.imread(input_path)
            if cv_img is None:
                return False
            h, w = cv_img.shape[:2]
            if w > h:
                cv_img = cv2.rotate(cv_img, cv2.ROTATE_90_COUNTERCLOCKWISE)

            cv_img = auto_perspective_crop_cv(cv_img)

            calc_w = 2100
            calc_h = int(cv_img.shape[0] * (calc_w / float(cv_img.shape[1])))
            cv_img = cv2.resize(cv_img, (calc_w, calc_h), interpolation=cv2.INTER_AREA)

            b, g, r = cv2.split(cv_img)
            b_f, g_f, r_f = b.astype(np.float32), g.astype(np.float32), r.astype(np.float32)

            red_line_mask = (r_f - np.maximum(g_f, b_f)) > 8.0
            max_c = np.maximum(np.maximum(r_f, g_f), b_f)
            min_c = np.minimum(np.minimum(r_f, g_f), b_f)
            color_diff_mask = (max_c - min_c) > 12.0

            gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY).astype(np.float32)
            gray[color_diff_mask] = np.clip(gray[color_diff_mask] - (max_c[color_diff_mask] - min_c[color_diff_mask]) * 1.5, 0, 255)
            gray[red_line_mask] = np.clip(gray[red_line_mask] - 40.0, 0, 255)

            bg = cv2.blur(gray, (35, 35)) + 1.0
            divided = (gray / bg) * 255.0
            out = np.full_like(divided, 255.0)

            grad_x = np.abs(gray[:, 2:] - gray[:, :-2])
            grad_y = np.abs(gray[2:, :] - gray[:-2, :])
            grad_pad = np.zeros_like(gray)
            grad_pad[1:-1, 1:-1] = grad_x[1:-1, :] + grad_y[:, 1:-1]

            ink_mask = (divided < 218.0) & ((grad_pad > 11.0) | (divided < 170.0))
            
            # 自然阶调映射：45 ~ 95 之间，清秀深黑，彻底消除糊字
            ink_vals = divided[ink_mask]
            natural_ink = np.clip(ink_vals * 0.45, 45.0, 95.0)
            out[ink_mask] = natural_ink

            out = safe_clean_edge_shadows(out)

            res_uint8 = np.clip(out, 0, 255).astype(np.uint8)
            sharp_pil = Image.fromarray(res_uint8).convert("RGB")
            final_canvas = fit_to_a4_safe_frame(sharp_pil, fill_ratio=0.97)
            final_canvas.save(output_path, format="JPEG", quality=95, dpi=(300, 300))

        else:
            with Image.open(input_path) as disk_img:
                img = ImageOps.exif_transpose(disk_img.convert("RGB"))

            if img.width > img.height:
                img = img.rotate(270, expand=True)

            img = auto_crop_paper_boundaries_pil(img)
            img = adaptive_perspective_flatten_pil(img)

            w, h = img.size
            calc_w = 2100
            calc_h = int(h * (calc_w / float(w)))
            work_img = img.resize((calc_w, calc_h), Image.Resampling.BILINEAR)

            r, g, b = work_img.split()
            r_arr = np.array(r, dtype=np.float32)
            g_arr = np.array(g, dtype=np.float32)
            b_arr = np.array(b, dtype=np.float32)

            red_line_mask = (r_arr - np.maximum(g_arr, b_arr)) > 8.0
            max_c = np.maximum(np.maximum(r_arr, g_arr), b_arr)
            min_c = np.minimum(np.minimum(r_arr, g_arr), b_arr)
            color_diff_mask = (max_c - min_c) > 12.0

            gray_arr = 0.299 * r_arr + 0.587 * g_arr + 0.114 * b_arr
            gray_arr[color_diff_mask] = np.clip(gray_arr[color_diff_mask] - (max_c[color_diff_mask] - min_c[color_diff_mask]) * 1.5, 0, 255)
            gray_arr[red_line_mask] = np.clip(gray_arr[red_line_mask] - 40.0, 0, 255)

            contrast_img = Image.fromarray(gray_arr.astype(np.uint8))
            bg = contrast_img.filter(ImageFilter.BoxBlur(radius=35))
            bg_arr = np.array(bg, dtype=np.float32) + 1.0

            divided = (gray_arr / bg_arr) * 255.0
            out = np.full_like(divided, 255.0)

            grad_x = np.abs(gray_arr[:, 2:] - gray_arr[:, :-2])
            grad_y = np.abs(gray_arr[2:, :] - gray_arr[:-2, :])
            grad_pad = np.zeros_like(gray_arr)
            grad_pad[1:-1, 1:-1] = grad_x[1:-1, :] + grad_y[:, 1:-1]

            ink_mask = (divided < 218.0) & ((grad_pad > 11.0) | (divided < 170.0))
            
            # 自然阶调映射：45 ~ 95 之间
            ink_vals = divided[ink_mask]
            natural_ink = np.clip(ink_vals * 0.45, 45.0, 95.0)
            out[ink_mask] = natural_ink

            out = safe_clean_edge_shadows(out)

            clean_gray = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
            sharp_rgb = Image.merge("RGB", [clean_gray, clean_gray, clean_gray])

            final_canvas = fit_to_a4_safe_frame(sharp_rgb, fill_ratio=0.97)
            final_canvas.save(output_path, format="JPEG", quality=95, dpi=(300, 300))

        log_debug(f"图像流水线优化完成: {output_path}")
        return True
    except Exception as e:
        log_debug(f"图像增强异常: {e}")
        return False

def clean_old_tmp_files(directory, max_age_seconds=1800):
    try:
        now = time.time()
        for f in os.listdir(directory):
            p = os.path.join(directory, f)
            if os.path.isfile(p) and (now - os.path.getmtime(p) > max_age_seconds):
                try:
                    os.remove(p)
                except Exception:
                    pass
    except Exception:
        pass

class PrintHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            duplex = self.get_argument("duplex", "none").strip()
            color_mode = self.get_argument("color_mode", "monochrome").strip()
            media = self.get_argument("media", "A4").strip()
            enhance = self.get_argument("enhance", "true").strip().lower() == "true"
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到上传文件")
                return

            # 安全校验：防止命令参数注入
            if not printer or printer.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:+]+$', printer):
                self.write_json(False, "非法打印机设备名称")
                return

            try:
                copies_int = int(copies)
                if not (1 <= copies_int <= 99):
                    copies_int = 1
            except ValueError:
                copies_int = 1

            if media.startswith("-") or not re.match(r'^[a-zA-Z0-9_\-]+$', media):
                media = "A4"

            jobs = []
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            for f in files:
                raw_ext = os.path.splitext(f["filename"])[-1].lower()
                clean_ext = re.sub(r'[^a-zA-Z0-9.]', '', raw_ext)
                
                # 白名单拦截防护
                if clean_ext not in ALLOWED_PRINT_EXTS:
                    self.write_json(False, f"不支持的文件类型: {clean_ext}，仅支持 PDF 和常见图片")
                    return

                token = uuid.uuid4().hex[:8]
                src_path = os.path.join(UPLOAD_DIR, f"raw_{token}{clean_ext}")
                with open(src_path, "wb") as out:
                    out.write(f["body"])

                target_file = src_path
                if enhance and clean_ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
                    enhanced_path = os.path.join(UPLOAD_DIR, f"opt_{token}.jpg")
                    if process_image_for_print(src_path, enhanced_path):
                        target_file = enhanced_path

                cmd = [
                    "lp",
                    "-d", printer,
                    "-n", str(copies_int),
                    "-o", f"media={media}",
                    "-o", f"PageSize={media}",
                    "-o", "fit-to-page",
                    "-o", "position=center"
                ]

                if color_mode == "color":
                    cmd.extend(["-o", "ColorModel=RGB", "-o", "print-color-mode=color"])
                else:
                    cmd.extend(["-o", "ColorModel=K", "-o", "ColorModel=Gray", "-o", "print-color-mode=monochrome"])

                if duplex == "long":
                    cmd.extend(["-o", "sides=two-sided-long-edge"])
                elif duplex == "short":
                    cmd.extend(["-o", "sides=two-sided-short-edge"])

                cmd.append(target_file)
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            clean_old_tmp_files(UPLOAD_DIR)
            self.write_json(True, f"共 {len(files)} 个文件已送达打印队列", job=", ".join(jobs))
        except Exception as e:
            log_debug(f"打印异常: {e}")
            self.write_json(False, f"打印服务异常: {str(e)}")

# ----------------- 身份证 1:1 标准拼版处理器 -----------------
class IDCardHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            front_files = self.request.files.get("front", [])
            back_files = self.request.files.get("back", [])

            if not front_files or not back_files:
                self.write_json(False, "必须同时上传身份证正面与反面照片")
                return

            if not printer or printer.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:+]+$', printer):
                self.write_json(False, "非法打印机设备名称")
                return

            try:
                copies_int = int(copies)
                if not (1 <= copies_int <= 99):
                    copies_int = 1
            except ValueError:
                copies_int = 1

            token = uuid.uuid4().hex[:8]
            f_src = os.path.join(UPLOAD_DIR, f"idf_raw_{token}.jpg")
            b_src = os.path.join(UPLOAD_DIR, f"idb_raw_{token}.jpg")

            with open(f_src, "wb") as f:
                f.write(front_files[0]["body"])
            with open(b_src, "wb") as f:
                f.write(back_files[0]["body"])

            f_opt = os.path.join(UPLOAD_DIR, f"idf_opt_{token}.jpg")
            b_opt = os.path.join(UPLOAD_DIR, f"idb_opt_{token}.jpg")

            # 双图去灰底白底化
            if not process_image_for_print(f_src, f_opt):
                f_opt = f_src
            if not process_image_for_print(b_src, b_opt):
                b_opt = b_src

            # A4 300DPI 标准像素尺寸: 2480 x 3508
            # 身份证标准比例: 85.6mm x 54mm -> 约 1010 x 638 像素
            canvas = Image.new("RGB", (2480, 3508), (255, 255, 255))
            card_w, card_h = 1010, 638

            with Image.open(f_opt) as img_f:
                rf = img_f.resize((card_w, card_h), Image.Resampling.BICUBIC)
                canvas.paste(rf, ((2480 - card_w) // 2, 700))

            with Image.open(b_opt) as img_b:
                rb = img_b.resize((card_w, card_h), Image.Resampling.BICUBIC)
                canvas.paste(rb, ((2480 - card_w) // 2, 1900))

            merged_path = os.path.join(UPLOAD_DIR, f"idcard_final_{token}.jpg")
            canvas.save(merged_path, format="JPEG", quality=95, dpi=(300, 300))

            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            cmd = [
                "lp",
                "-d", printer,
                "-n", str(copies_int),
                "-o", "media=A4",
                "-o", "PageSize=A4",
                "-o", "fit-to-page",
                "-o", "position=center",
                "-o", "ColorModel=K",
                "-o", "print-color-mode=monochrome",
                merged_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            clean_old_tmp_files(UPLOAD_DIR)

            if res.returncode == 0:
                self.write_json(True, "身份证 1:1 标准拼版已成功送达打印机！", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"拼版打印异常: {str(e)}")
