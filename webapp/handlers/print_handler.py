#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
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
        top_h = int(h * 0.05)
        bot_h = int(h * 0.95)
        left_w = int(w * 0.04)
        right_w = int(w * 0.96)

        for y in range(top_h):
            mask = arr[y, :] < 120
            if np.sum(mask) > 0:
                arr[y, mask] = 255.0

        for y in range(bot_h, h):
            mask = arr[y, :] < 120
            if np.sum(mask) > 0:
                arr[y, mask] = 255.0

        for x in range(left_w):
            mask = arr[:, x] < 120
            if np.sum(mask) > 0:
                arr[mask, x] = 255.0

        for x in range(right_w, w):
            mask = arr[:, x] < 120
            if np.sum(mask) > 0:
                arr[mask, x] = 255.0

        pad_y = max(1, int(h * 0.01))
        pad_x = max(1, int(w * 0.01))
        arr[0:pad_y, :] = 255.0
        arr[h-pad_y:, :] = 255.0
        arr[:, 0:pad_x] = 255.0
        arr[:, w-pad_x:] = 255.0
    except Exception:
        pass
    return arr

def paste_onto_a4_canvas(img, scale_ratio=0.90):
    """
    等比例等比缩放并内嵌在 A4 画布正中央：
    解决长宽比失真与左右上下切字问题
    """
    target_w, target_h = 2480, 3508
    orig_w, orig_h = img.size

    # 计算等比例缩放尺寸
    ratio = min((target_w * scale_ratio) / orig_w, (target_h * scale_ratio) / orig_h)
    new_w = int(orig_w * ratio)
    new_h = int(orig_h * ratio)

    resized_img = img.resize((new_w, new_h), Image.Resampling.BICUBIC)
    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))

    pos_x = (target_w - new_w) // 2
    pos_y = (target_h - new_h) // 2
    canvas.paste(resized_img, (pos_x, pos_y))
    return canvas

def process_image_for_print(input_path, output_path):
    try:
        log_debug(f"启动优化流水线: {input_path}")
        with Image.open(input_path) as disk_img:
            img = ImageOps.exif_transpose(disk_img.convert("RGB"))

        if img.width > img.height:
            img = img.rotate(270, expand=True)

        w, h = img.size
        # 限制计算尺寸，防止低配置盒子爆内存
        calc_w = 1800
        calc_h = int(h * (calc_w / float(w)))
        work_img = img.resize((calc_w, calc_h), Image.Resampling.BILINEAR)

        r, g, b = work_img.split()
        r_arr = np.array(r, dtype=np.float32)
        g_arr = np.array(g, dtype=np.float32)
        b_arr = np.array(b, dtype=np.float32)

        # 1. 色彩保护：防止浅红/粉色虚线题框丢失
        red_line_mask = (r_arr - np.maximum(g_arr, b_arr)) > 8.0
        max_c = np.maximum(np.maximum(r_arr, g_arr), b_arr)
        min_c = np.minimum(np.minimum(r_arr, g_arr), b_arr)
        color_diff_mask = (max_c - min_c) > 12.0

        gray_arr = 0.299 * r_arr + 0.587 * g_arr + 0.114 * b_arr
        gray_arr[color_diff_mask] = np.clip(gray_arr[color_diff_mask] - (max_c[color_diff_mask] - min_c[color_diff_mask]) * 1.5, 0, 255)
        gray_arr[red_line_mask] = np.clip(gray_arr[red_line_mask] - 40.0, 0, 255)

        # 2. 背景估计与归一化
        contrast_img = Image.fromarray(gray_arr.astype(np.uint8))
        bg = contrast_img.filter(ImageFilter.BoxBlur(radius=35))
        bg_arr = np.array(bg, dtype=np.float32) + 1.0

        divided = (gray_arr / bg_arr) * 255.0
        out = np.full_like(divided, 255.0)

        # 3. 彻底压制背面透墨（针对阴影、背面反光）：
        # 梯度边缘计算：前景真字迹具有高梯度边缘，背面透墨是漫反射低梯度
        grad_x = np.abs(gray_arr[:, 2:] - gray_arr[:, :-2])
        grad_y = np.abs(gray_arr[2:, :] - gray_arr[:-2, :])
        grad_pad = np.zeros_like(gray_arr)
        grad_pad[1:-1, 1:-1] = grad_x[1:-1, :] + grad_y[:, 1:-1]

        # 阈值判定：强边缘（真字迹）允许放宽，平缓灰斑（透墨）直接过滤
        ink_mask = (divided < 218.0) & ((grad_pad > 14.0) | (divided < 175.0))
        
        # 消除镂空字：字心直接赋纯实心黑（0~40），不再使用反差缩放
        out[ink_mask] = 10.0

        # 4. 消除边缘黑块
        out = safe_clean_edge_shadows(out)

        clean_gray = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
        # 适度轻微锐化，避免字心镂空
        sharp = clean_gray.filter(ImageFilter.UnsharpMask(radius=0.8, percent=100, threshold=2))
        sharp_rgb = Image.merge("RGB", [sharp, sharp, sharp])

        # 5. 等比例等比缩放并内嵌在 A4 画布正中央 (90% 比例)
        final_canvas = paste_onto_a4_canvas(sharp_rgb, scale_ratio=0.90)
        final_canvas.save(output_path, format="JPEG", quality=95, dpi=(300, 300))

        log_debug(f"优化完成: {output_path}")
        return True
    except Exception as e:
        log_debug(f"图像流水线异常: {e}")
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
                if enhance and ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
                    enhanced_path = os.path.join(UPLOAD_DIR, f"opt_{token}.jpg")
                    if process_image_for_print(src_path, enhanced_path):
                        target_file = enhanced_path

                # 由于图片内部已做 90% 物理留白居中，此处直通 A4 居中打印即可
                cmd = [
                    "lp",
                    "-d", printer,
                    "-n", str(copies),
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
