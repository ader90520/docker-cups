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

CANVAS_SIZES_200DPI = {
    "A4": (1654, 2338),
    "A5": (1169, 1654),
    "B5": (1385, 1968),
    "A6": (827, 1169)
}

def log_debug(msg):
    try:
        with open("/tmp/dewarp_debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(f"[PrintLog] {msg}", flush=True)

def fit_to_a4_fast_frame(img, fill_ratio=0.96, media="A4"):
    media_key = media.upper() if media.upper() in CANVAS_SIZES_200DPI else "A4"
    target_w, target_h = CANVAS_SIZES_200DPI[media_key]
    orig_w, orig_h = img.size

    ratio = min((target_w * fill_ratio) / orig_w, (target_h * fill_ratio) / orig_h)
    new_w = max(1, int(orig_w * ratio))
    new_h = max(1, int(orig_h * ratio))

    resized_img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
    pos_x = (target_w - new_w) // 2
    pos_y = (target_h - new_h) // 2
    canvas.paste(resized_img, (pos_x, pos_y))
    return canvas

def merge_images_n_up(image_paths, media="A4"):
    media_key = media.upper() if media.upper() in CANVAS_SIZES_200DPI else "A4"
    page_w, page_h = CANVAS_SIZES_200DPI[media_key]
    canvas = Image.new("RGB", (page_w, page_h), (255, 255, 255))

    count = len(image_paths)
    if count == 1:
        with Image.open(image_paths[0]) as im:
            return fit_to_a4_fast_frame(ImageOps.exif_transpose(im).convert("RGB"), fill_ratio=0.96, media=media)

    if count == 2:
        cols, rows = 1, 2
    elif count in (3, 4):
        cols, rows = 2, 2
    elif count in (5, 6):
        cols, rows = 2, 3
    else:
        cols, rows = 2, 4

    cell_w = page_w // cols
    cell_h = page_h // rows
    margin = 35

    for idx, img_p in enumerate(image_paths[:cols * rows]):
        try:
            with Image.open(img_p) as raw_img:
                img = ImageOps.exif_transpose(raw_img).convert("RGB")
                ratio = min((cell_w - margin * 2) / img.width, (cell_h - margin * 2) / img.height)
                target_w = max(10, int(img.width * ratio))
                target_h = max(10, int(img.height * ratio))
                resized = img.resize((target_w, target_h), Image.Resampling.BILINEAR)

                col = idx % cols
                row = idx // cols
                pos_x = col * cell_w + (cell_w - target_w) // 2
                pos_y = row * cell_h + (cell_h - target_h) // 2
                canvas.paste(resized, (pos_x, pos_y))
        except Exception as e:
            log_debug(f"单图拼版受阻: {e}")

    return canvas

def clean_extreme_edges_only(arr, margin_ratio=0.010):
    h, w = arr.shape[:2]
    top_h = int(h * margin_ratio)
    bot_h = int(h * (1.0 - margin_ratio))
    left_w = int(w * margin_ratio)
    right_w = int(w * (1.0 - margin_ratio))

    arr[0:top_h, :] = 255.0
    arr[bot_h:h, :] = 255.0
    arr[:, 0:left_w] = 255.0
    arr[:, right_w:w] = 255.0
    return arr

def auto_perspective_crop_cv(cv_img):
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
            rect = np.zeros((4, 2), dtype="float32")
            s = pts.sum(axis=1)
            rect[0] = pts[np.argmin(s)]
            rect[2] = pts[np.argmax(s)]
            diff = np.diff(pts, axis=1)
            rect[1] = pts[np.argmin(diff)]
            rect[3] = pts[np.argmax(diff)]

            (tl, tr, br, bl) = rect
            max_w = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)))
            max_h = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)))

            if max_w > 100 and max_h > 100:
                dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype="float32")
                M = cv2.getPerspectiveTransform(rect, dst)
                return cv2.warpPerspective(orig, M, (max_w, max_h), flags=cv2.INTER_LINEAR)
    except Exception as e:
        log_debug(f"OpenCV 透视拉平跳过: {e}")
    return cv_img

def process_image_for_print(input_path, output_path, media="A4", is_color=False):
    """
    全能王级白底去黑底滤镜（兼顾极黑字体与彩色饱和还原）
    """
    try:
        t0 = time.time()
        with Image.open(input_path) as check_img:
            if check_img.width < 10 or check_img.height < 10:
                return False

        if HAVE_OPENCV:
            cv_img = cv2.imread(input_path)
            if cv_img is None:
                return False

            h, w = cv_img.shape[:2]
            if w > h:
                cv_img = cv2.rotate(cv_img, cv2.ROTATE_90_COUNTERCLOCKWISE)

            cv_img = auto_perspective_crop_cv(cv_img)
            calc_w = 1654
            calc_h = int(cv_img.shape[0] * (calc_w / float(cv_img.shape[1])))
            cv_img = cv2.resize(cv_img, (calc_w, calc_h), interpolation=cv2.INTER_LINEAR)

            b, g, r = cv2.split(cv_img)
            b_f, g_f, r_f = b.astype(np.float32), g.astype(np.float32), r.astype(np.float32)

            max_channel = np.maximum(np.maximum(r_f, g_f), b_f).astype(np.uint8)
            small_w, small_h = max(60, calc_w // 6), max(60, calc_h // 6)
            small_b = cv2.resize(max_channel, (small_w, small_h), interpolation=cv2.INTER_AREA)

            kernel_bg = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 21))
            bg_morph_small = cv2.morphologyEx(small_b, cv2.MORPH_CLOSE, kernel_bg)
            bg_morph = cv2.resize(bg_morph_small, (calc_w, calc_h), interpolation=cv2.INTER_LINEAR)
            bg_float = cv2.GaussianBlur(bg_morph, (21, 21), 0).astype(np.float32) + 1.0

            r_div = np.clip((r_f / bg_float) * 255.0, 0, 255)
            g_div = np.clip((g_f / bg_float) * 255.0, 0, 255)
            b_div = np.clip((b_f / bg_float) * 255.0, 0, 255)

            gray_div = (0.299 * r_div + 0.587 * g_div + 0.114 * b_div)
            grad_x = cv2.Sobel(gray_div, cv2.CV_32F, 1, 0, ksize=3)
            grad_y = cv2.Sobel(gray_div, cv2.CV_32F, 0, 1, ksize=3)
            grad_mag = np.sqrt(grad_x**2 + grad_y**2)

            red_excess = r_f - np.maximum(g_f, b_f)
            color_diff = np.abs(r_f - g_f) + np.abs(g_f - b_f) + np.abs(b_f - r_f)
            is_color_element = (red_excess > 5.0) | (color_diff > 10.0)

            is_text_mask = (~is_color_element) & (((gray_div < 225.0) & (grad_mag > 5.0)) | (gray_div < 185.0))

            out_r = np.full_like(r_div, 255.0)
            out_g = np.full_like(g_div, 255.0)
            out_b = np.full_like(b_div, 255.0)

            if is_color:
                out_r[is_color_element] = np.clip(r_div[is_color_element] * 0.95, 0, 255)
                out_g[is_color_element] = np.clip(g_div[is_color_element] * 0.90, 0, 255)
                out_b[is_color_element] = np.clip(b_div[is_color_element] * 0.90, 0, 255)

            dark_ink = np.clip(gray_div[is_text_mask] * 0.28, 5.0, 55.0)
            out_r[is_text_mask] = dark_ink
            out_g[is_text_mask] = dark_ink
            out_b[is_text_mask] = dark_ink

            out_bgr = cv2.merge([out_b, out_g, out_r])
            out_bgr = clean_extreme_edges_only(out_bgr, margin_ratio=0.012)

            res_uint8 = np.clip(out_bgr, 0, 255).astype(np.uint8)
            res_rgb = cv2.cvtColor(res_uint8, cv2.COLOR_BGR2RGB)
            sharp_pil = Image.fromarray(res_rgb)
            final_canvas = fit_to_a4_fast_frame(sharp_pil, fill_ratio=0.96, media=media)
            final_canvas.save(output_path, format="JPEG", quality=90, dpi=(200, 200))

        else:
            with Image.open(input_path) as disk_img:
                img = ImageOps.exif_transpose(disk_img).convert("RGB")

            if img.width > img.height:
                img = img.rotate(270, expand=True)

            calc_w = 1654
            calc_h = int(img.height * (calc_w / float(img.width)))
            work_img = img.resize((calc_w, calc_h), Image.Resampling.BILINEAR)

            r, g, b = work_img.split()
            r_arr = np.array(r, dtype=np.float32)
            g_arr = np.array(g, dtype=np.float32)
            b_arr = np.array(b, dtype=np.float32)

            max_ch = np.maximum(np.maximum(r_arr, g_arr), b_arr).astype(np.uint8)
            bg = Image.fromarray(max_ch).filter(ImageFilter.BoxBlur(radius=30))
            bg_arr = np.array(bg, dtype=np.float32) + 1.0

            r_div = np.clip((r_arr / bg_arr) * 255.0, 0, 255)
            g_div = np.clip((g_arr / bg_arr) * 255.0, 0, 255)
            b_div = np.clip((b_arr / bg_arr) * 255.0, 0, 255)

            gray_div = 0.299 * r_div + 0.587 * g_div + 0.114 * b_div
            color_diff = np.abs(r_arr - g_arr) + np.abs(g_arr - b_arr) + np.abs(b_arr - r_arr)
            is_color_element = color_diff > 10.0

            is_text_mask = (~is_color_element) & (gray_div < 210.0)

            out_r = np.full_like(r_div, 255.0)
            out_g = np.full_like(g_div, 255.0)
            out_b = np.full_like(b_div, 255.0)

            if is_color:
                out_r[is_color_element] = r_div[is_color_element]
                out_g[is_color_element] = g_div[is_color_element]
                out_b[is_color_element] = b_div[is_color_element]

            deep_ink = np.clip(gray_div[is_text_mask] * 0.28, 5.0, 55.0)
            out_r[is_text_mask] = deep_ink
            out_g[is_text_mask] = deep_ink
            out_b[is_text_mask] = deep_ink

            rgb_stack = np.stack([out_r, out_g, out_b], axis=-1)
            rgb_stack = clean_extreme_edges_only(rgb_stack, margin_ratio=0.012)
            res_uint8 = np.clip(rgb_stack, 0, 255).astype(np.uint8)
            sharp_pil = Image.fromarray(res_uint8, mode="RGB")
            final_canvas = fit_to_a4_fast_frame(sharp_pil, fill_ratio=0.96, media=media)
            final_canvas.save(output_path, format="JPEG", quality=90, dpi=(200, 200))

        log_debug(f"去黑底增强耗时: {time.time() - t0:.2f}s -> {output_path}")
        return True
    except Exception as e:
        log_debug(f"图像增强异常回退: {e}")
        return False

def clean_old_tmp_files(directory, max_age_seconds=1800):
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
            page_ranges = self.get_argument("page_ranges", "").strip()
            multi_merge = self.get_argument("multi_merge", "false").strip().lower() == "true"
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

            os.makedirs(UPLOAD_DIR, exist_ok=True)
            jobs = []
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            image_raw_paths = []
            pdf_files = []
            other_files = []

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

                if clean_ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
                    image_raw_paths.append(src_path)
                elif clean_ext == ".pdf":
                    pdf_files.append(src_path)
                else:
                    other_files.append(src_path)

            tasks = []
            is_color_task = (color_mode == "color")

            if image_raw_paths:
                if multi_merge and len(image_raw_paths) > 1:
                    merged_canvas = merge_images_n_up(image_raw_paths, media=media)
                    merged_path = os.path.join(UPLOAD_DIR, f"merged_{uuid.uuid4().hex[:8]}.jpg")
                    merged_canvas.save(merged_path, format="JPEG", quality=90, dpi=(200, 200))
                    
                    if enhance:
                        opt_path = os.path.join(UPLOAD_DIR, f"opt_merged_{uuid.uuid4().hex[:8]}.jpg")
                        if process_image_for_print(merged_path, opt_path, media=media, is_color=is_color_task):
                            tasks.append({"path": opt_path, "is_pdf": False})
                        else:
                            tasks.append({"path": merged_path, "is_pdf": False})
                    else:
                        tasks.append({"path": merged_path, "is_pdf": False})
                else:
                    for src_path in image_raw_paths:
                        target_f = src_path
                        if enhance:
                            token = uuid.uuid4().hex[:8]
                            opt_path = os.path.join(UPLOAD_DIR, f"opt_{token}.jpg")
                            if process_image_for_print(src_path, opt_path, media=media, is_color=is_color_task):
                                target_f = opt_path
                        tasks.append({"path": target_f, "is_pdf": False})

            for p_f in pdf_files:
                tasks.append({"path": p_f, "is_pdf": True})

            for o_f in other_files:
                tasks.append({"path": o_f, "is_pdf": False})

            for item in tasks:
                target_file = item["path"]
                is_pdf = item["is_pdf"]

                cmd = [
                    "lp",
                    "-d", printer,
                    "-n", str(copies_int),
                    "-o", f"media={media}",
                    "-o", f"PageSize={media}",
                    "-o", "fit-to-page",
                    "-o", "scaling=100",
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

                # 仅针对 PDF 实体文件附加 page-ranges，杜绝单页图片被拒绝
                if is_pdf and page_ranges and re.match(r'^[0-9,\-]+$', page_ranges):
                    cmd.extend(["-o", f"page-ranges={page_ranges}"])

                cmd.append(target_file)
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=45)

                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            clean_old_tmp_files(UPLOAD_DIR)
            self.write_json(True, f"共 {len(tasks)} 个文件成功送达打印队列", job=", ".join(jobs))
        except subprocess.TimeoutExpired:
            self.write_json(False, "打印任务派发超时，打印机可能处于离线、卡纸或通信受阻状态")
        except Exception as e:
            log_debug(f"打印服务异常: {e}")
            self.write_json(False, f"打印服务异常: {str(e)}")
