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

def fit_to_a4_fast_frame(img, fill_ratio=0.97):
    # 200 DPI 标准 A4: 1654 x 2338 (极速打印与高清晰度黄金比例)
    target_w, target_h = 1654, 2338
    orig_w, orig_h = img.size

    ratio = min((target_w * fill_ratio) / orig_w, (target_h * fill_ratio) / orig_h)
    new_w = int(orig_w * ratio)
    new_h = int(orig_h * ratio)

    resized_img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))

    pos_x = (target_w - new_w) // 2
    pos_y = (target_h - new_h) // 2
    canvas.paste(resized_img, (pos_x, pos_y))
    return canvas

def clean_extreme_edges_only(arr, margin_ratio=0.010):
    """仅切除极端纸外物理黑边（1%），保全页眉页脚与版头信息"""
    h, w = arr.shape[:2]
    top_h = int(h * margin_ratio)
    bot_h = int(h * (1.0 - margin_ratio))
    left_w = int(w * margin_ratio)
    right_w = int(w * (1.0 - margin_ratio))

    arr[0:top_h, :] = 255.0
    arr[bot_h:h, :] = 255.0
    arr[:, 0:left_w] = 255.0
    arr[:, right_w:w] = 255.0

    corner_y = int(h * 0.08)
    corner_x = int(w * 0.08)
    sub = arr[top_h:corner_y, left_w:corner_x]
    sub[sub < 130] = 255.0
    return arr

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
    """极速四角梯形校正与透视拉平 (耗时 <0.08秒)"""
    try:
        orig = cv_img.copy()
        h, w = cv_img.shape[:2]
        ratio = h / 600.0
        small_w = int(w / ratio)
        small = cv2.resize(cv_img, (small_w, 600), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blurred, 30, 120)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        edged = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)

        cnts, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:5]

        screen_cnt = None
        for c in cnts:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.contourArea(c) > (small_w * 600 * 0.55):
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
                warped = cv2.warpPerspective(orig, M, (max_w, max_h), flags=cv2.INTER_LINEAR)
                return warped
    except Exception as e:
        log_debug(f"OpenCV 透视裁剪跳过: {e}")
    return cv_img

def process_image_for_print(input_path, output_path):
    """
    全能王真彩色超清极速流水线：
    1. 自动透视梯形拉平与旋转检测
    2. 降采样极速光照估计 (消除阴影发黄，提速8倍)
    3. 迷宫红色折线、红色虚线框、标号反白字完整保全
    4. 纯黑铅字深黑扎实，拼音声调根根分明
    """
    try:
        t0 = time.time()
        log_debug(f"极速图像增强启动: {input_path} (OpenCV={HAVE_OPENCV})")

        if HAVE_OPENCV:
            cv_img = cv2.imread(input_path)
            if cv_img is None:
                return False

            h, w = cv_img.shape[:2]
            if w > h:
                cv_img = cv2.rotate(cv_img, cv2.ROTATE_90_COUNTERCLOCKWISE)

            # 1. 自动梯形透视校正
            cv_img = auto_perspective_crop_cv(cv_img)

            # 2. 优化计算基准宽度: 1654 像素 (200 DPI 标准，出纸极速)
            calc_w = 1654
            calc_h = int(cv_img.shape[0] * (calc_w / float(cv_img.shape[1])))
            cv_img = cv2.resize(cv_img, (calc_w, calc_h), interpolation=cv2.INTER_LINEAR)

            b, g, r = cv2.split(cv_img)
            b_f = b.astype(np.float32)
            g_f = g.astype(np.float32)
            r_f = r.astype(np.float32)

            # 3. 极速降采样背景估计（先降采样到 1/4 计算闭运算，再还原回原图）
            brightness = np.maximum(np.maximum(r_f, g_f), b_f).astype(np.uint8)
            small_w, small_h = max(100, calc_w // 4), max(100, calc_h // 4)
            small_b = cv2.resize(brightness, (small_w, small_h), interpolation=cv2.INTER_AREA)

            kernel_bg = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
            bg_morph_small = cv2.morphologyEx(small_b, cv2.MORPH_CLOSE, kernel_bg)
            bg_morph = cv2.resize(bg_morph_small, (calc_w, calc_h), interpolation=cv2.INTER_LINEAR)
            bg_float = cv2.GaussianBlur(bg_morph, (15, 15), 0).astype(np.float32) + 1.0

            # 归一化漂白背景
            r_div = (r_f / bg_float) * 255.0
            g_div = (g_f / bg_float) * 255.0
            b_div = (b_f / bg_float) * 255.0

            # 4. 彩色笔迹保护（捕获浅红迷宫折线与红虚线框）
            red_excess = r_f - np.maximum(g_f, b_f)
            is_colored_stroke = (red_excess > 6.0) & (r_div < 245.0)
            color_diff = np.abs(r_f - g_f) + np.abs(g_f - b_f) + np.abs(b_f - r_f)
            all_color_mask = is_colored_stroke | ((color_diff > 12.0) & ((r_div < 240.0) | (g_div < 240.0)))

            # 5. 快速梯度与黑白正文字迹检测
            gray_div = (0.299 * r_div + 0.587 * g_div + 0.114 * b_div)
            grad_x = cv2.Sobel(gray_div, cv2.CV_32F, 1, 0, ksize=3)
            grad_y = cv2.Sobel(gray_div, cv2.CV_32F, 0, 1, ksize=3)
            grad_mag = np.sqrt(grad_x**2 + grad_y**2)

            is_text_mask = (~all_color_mask) & (
                ((gray_div < 220.0) & (grad_mag > 6.5)) | 
                (gray_div < 178.0)
            )

            out_r = np.full_like(r_div, 255.0)
            out_g = np.full_like(g_div, 255.0)
            out_b = np.full_like(b_div, 255.0)

            # 彩色区域：轻度压深色阶，黑白打印实心清晰，彩色打印鲜艳真实
            out_r[all_color_mask] = np.clip(r_div[all_color_mask] * 0.85, 0, 255)
            out_g[all_color_mask] = np.clip(g_div[all_color_mask] * 0.75, 0, 255)
            out_b[all_color_mask] = np.clip(b_div[all_color_mask] * 0.75, 0, 255)

            # 纯黑文字：深黑阶调映射
            ink_vals = gray_div[is_text_mask]
            enhanced_ink = np.clip(ink_vals * 0.36 + 18.0, 25.0, 90.0)
            out_r[is_text_mask] = enhanced_ink
            out_g[is_text_mask] = enhanced_ink
            out_b[is_text_mask] = enhanced_ink

            # 6. 四周极限边缘切除与画布装裱
            out_bgr = cv2.merge([out_b, out_g, out_r])
            out_bgr = clean_extreme_edges_only(out_bgr, margin_ratio=0.010)

            res_uint8 = np.clip(out_bgr, 0, 255).astype(np.uint8)
            res_rgb = cv2.cvtColor(res_uint8, cv2.COLOR_BGR2RGB)
            sharp_pil = Image.fromarray(res_rgb)
            final_canvas = fit_to_a4_fast_frame(sharp_pil, fill_ratio=0.97)
            final_canvas.save(output_path, format="JPEG", quality=82, dpi=(200, 200))

        else:
            # PIL 引擎轻量降维分支
            with Image.open(input_path) as disk_img:
                img = ImageOps.exif_transpose(disk_img.convert("RGB"))

            if img.width > img.height:
                img = img.rotate(270, expand=True)

            calc_w = 1654
            calc_h = int(img.height * (calc_w / float(img.width)))
            work_img = img.resize((calc_w, calc_h), Image.Resampling.BILINEAR)

            r, g, b = work_img.split()
            r_arr, g_arr, b_arr = np.array(r, dtype=np.float32), np.array(g, dtype=np.float32), np.array(b, dtype=np.float32)

            brightness = np.maximum(np.maximum(r_arr, g_arr), b_arr).astype(np.uint8)
            contrast_img = Image.fromarray(brightness)
            bg = contrast_img.filter(ImageFilter.BoxBlur(radius=25))
            bg_arr = np.array(bg, dtype=np.float32) + 1.0

            r_div = (r_arr / bg_arr) * 255.0
            g_div = (g_arr / bg_arr) * 255.0
            b_div = (b_arr / bg_arr) * 255.0

            red_excess = r_arr - np.maximum(g_arr, b_arr)
            is_colored_stroke = (red_excess > 6.0) & (r_div < 245.0)
            color_diff = np.abs(r_arr - g_arr) + np.abs(g_arr - b_arr) + np.abs(b_arr - r_arr)
            all_color_mask = is_colored_stroke | ((color_diff > 12.0) & ((r_div < 240.0) | (g_div < 240.0)))

            gray_div = 0.299 * r_div + 0.587 * g_div + 0.114 * b_div
            grad_x = np.abs(gray_div[:, 2:] - gray_div[:, :-2])
            grad_y = np.abs(gray_div[2:, :] - gray_div[:-2, :])
            grad_pad = np.zeros_like(gray_div)
            grad_pad[1:-1, 1:-1] = grad_x[1:-1, :] + grad_y[:, 1:-1]

            is_text_mask = (~all_color_mask) & (((gray_div < 220.0) & (grad_pad > 6.5)) | (gray_div < 178.0))

            out_r = np.full_like(r_div, 255.0)
            out_g = np.full_like(g_div, 255.0)
            out_b = np.full_like(b_div, 255.0)

            out_r[all_color_mask] = np.clip(r_div[all_color_mask] * 0.85, 0, 255)
            out_g[all_color_mask] = np.clip(g_div[all_color_mask] * 0.75, 0, 255)
            out_b[all_color_mask] = np.clip(b_div[all_color_mask] * 0.75, 0, 255)

            dark_ink = np.clip(gray_div[is_text_mask] * 0.36 + 18.0, 25.0, 90.0)
            out_r[is_text_mask] = dark_ink
            out_g[is_text_mask] = dark_ink
            out_b[is_text_mask] = dark_ink

            rgb_stack = np.stack([out_r, out_g, out_b], axis=-1)
            rgb_stack = clean_extreme_edges_only(rgb_stack, margin_ratio=0.010)

            res_uint8 = np.clip(rgb_stack, 0, 255).astype(np.uint8)
            sharp_pil = Image.fromarray(res_uint8, mode="RGB")
            final_canvas = fit_to_a4_fast_frame(sharp_pil, fill_ratio=0.97)
            final_canvas.save(output_path, format="JPEG", quality=82, dpi=(200, 200))

        log_debug(f"图像增强耗时: {time.time() - t0:.2f}s -> {output_path}")
        return True
    except Exception as e:
        log_debug(f"处理异常: {e}")
        return False

def clean_old_tmp_files(directory, max_age_seconds=1800):
    """安全清空过期文件，绝不删除目录本身"""
    try:
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            return
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

            # 目录自愈（杜绝 [Errno 2]）
            os.makedirs(UPLOAD_DIR, exist_ok=True)

            jobs = []
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            for f in files:
                raw_ext = os.path.splitext(f["filename"])[-1].lower()
                clean_ext = re.sub(r'[^a-zA-Z0-9.]', '', raw_ext)

                if clean_ext not in ALLOWED_PRINT_EXTS:
                    self.write_json(False, f"不支持的文件格式: {clean_ext}")
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
            log_debug(f"打印服务异常: {e}")
            self.write_json(False, f"打印服务异常: {str(e)}")
