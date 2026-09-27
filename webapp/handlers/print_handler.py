#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import uuid
import subprocess
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def fast_detect_skew(gray_img):
    """倾斜快速检测"""
    try:
        w, h = gray_img.size
        scale = 160.0 / max(w, h)
        small = gray_img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.NEAREST)
        arr = np.array(small, dtype=np.float32)

        grad = np.abs(arr[2:, :] - arr[:-2, :])
        grad[grad < 35] = 0.0

        best_angle = 0.0
        max_var = 0.0
        for angle in [-2.0, 0.0, 2.0]:
            rot = Image.fromarray(grad).rotate(angle, resample=Image.Resampling.NEAREST)
            proj = np.sum(np.array(rot), axis=1)
            var = np.var(proj)
            if var > max_var:
                max_var = var
                best_angle = angle
        return best_angle
    except Exception:
        return 0.0

def dewarp_textlines_flatten(pil_img):
    """
    轻量级多骨架文本行展平（纯 NumPy + PIL，拉平波浪形弯曲）
    """
    try:
        w, h = pil_img.size
        target_w = 600
        scale = target_w / float(w)
        target_h = int(h * scale)

        gray = pil_img.convert("L").resize((target_w, target_h), Image.Resampling.BILINEAR)
        arr = np.array(gray, dtype=np.float32)

        bg_coarse = gray.filter(ImageFilter.BoxBlur(radius=15))
        bg_arr = np.array(bg_coarse, dtype=np.float32) + 1.0
        div = (arr / bg_arr) * 255.0
        conn_arr = div < 200

        window = 31
        kernel = np.ones(window, dtype=np.float32)
        dilated = np.zeros_like(conn_arr, dtype=bool)
        for r in range(0, target_h, 2):
            if np.any(conn_arr[r, :]):
                dilated[r, :] = np.convolve(conn_arr[r, :].astype(np.float32), kernel, mode='same') > 0.1

        num_strips = 24
        strip_w = target_w // num_strips
        row_density = np.mean(dilated, axis=1)
        peak_rows = np.where(row_density > 0.15)[0]

        if len(peak_rows) > 40:
            displacement_matrix = []
            for sec in np.array_split(peak_rows, 5):
                if len(sec) < 5:
                    continue
                line_y = []
                for s in range(num_strips):
                    sub = dilated[sec[0]:sec[-1], s * strip_w : min((s + 1) * strip_w, target_w)]
                    if np.sum(sub) > (strip_w * 2):
                        line_y.append(np.mean(np.where(sub)[0]) + sec[0])
                    else:
                        line_y.append(np.nan)

                line_y = np.array(line_y)
                valid = ~np.isnan(line_y)
                if np.sum(valid) >= 12:
                    xs = np.arange(num_strips)[valid]
                    ys = line_y[valid]
                    p = np.polyfit(xs, ys - np.mean(ys), 2)
                    displacement_matrix.append(np.polyval(p, np.arange(num_strips)))

            if len(displacement_matrix) >= 2:
                median_disp = np.median(displacement_matrix, axis=0)
                if np.ptp(median_disp) >= 1.8:
                    disp_fine = np.interp(np.arange(w), np.linspace(0, w, num_strips), median_disp) / scale
                    disp_fine -= np.mean(disp_fine)

                    full_arr = np.array(pil_img)
                    out_arr = np.full_like(full_arr, 255)
                    for x in range(w):
                        shift = int(round(disp_fine[x]))
                        if shift > 0:
                            out_arr[:-shift, x] = full_arr[shift:, x]
                        elif shift < 0:
                            out_arr[-shift:, x] = full_arr[:shift, x]
                        else:
                            out_arr[:, x] = full_arr[:, x]
                    return Image.fromarray(out_arr)
    except Exception as e:
        print(f"[Dewarp] 忽略降级: {e}", flush=True)
    return pil_img

def process_image_for_print(input_path, output_path):
    """
    零裁剪、保全边角细节、抗背面透墨与文本行展平引擎
    """
    try:
        with Image.open(input_path) as disk_img:
            img = ImageOps.exif_transpose(disk_img.convert("RGB"))

        # 竖向统一对齐
        if img.width > img.height:
            img = img.rotate(270, expand=True)

        # 1. 文本行物理波浪弯曲拉平
        img = dewarp_textlines_flatten(img)

        # 2. 修正大倾斜
        gray_small = img.convert("L")
        angle = fast_detect_skew(gray_small)
        if abs(angle) >= 1.2:
            img = img.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=(255, 255, 255))

        # 限制计算尺寸，防止低内存设备 OOM
        if max(img.size) > 2200:
            img.thumbnail((2200, 2200), Image.Resampling.BILINEAR)

        # 3. 根治透字：采用纯灰度处理（剥离彩色漫反射杂阶）
        gray = img.convert("L")
        gray_arr = np.array(gray, dtype=np.float32)

        # 4. 42 大核平滑背景
        bg = gray.filter(ImageFilter.BoxBlur(radius=42))
        bg_arr = np.array(bg, dtype=np.float32) + 1.0

        divided = (gray_arr / bg_arr) * 255.0
        out = np.full_like(divided, 255.0)

        # 5. 精确截断阈值 212：212 以上的浅灰背透字强行压白，212 以下的正面有效笔画加黑强化
        mask_front = divided < 212.0
        ink_vals = divided[mask_front]
        clean_ink = np.clip((ink_vals - 30.0) * (215.0 / (212.0 - 30.0)), 0, 255)
        clean_ink = (clean_ink / 215.0) ** 1.15 * 190.0
        out[mask_front] = clean_ink

        clean_gray = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

        # 6. 微锐化还原线稿与四线格细节
        sharp_gray = clean_gray.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=2))
        sharp_rgb = Image.merge("RGB", [sharp_gray, sharp_gray, sharp_gray])

        sharp_rgb.save(output_path, format="JPEG", quality=95, dpi=(300, 300))
        return True
    except Exception as e:
        print(f"[ProcessImage] 处理异常: {e}", flush=True)
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

class PrintHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            whiten = self.get_argument("whiten", "0").strip()
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到文件")
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

                if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                    enhanced_path = os.path.join(UPLOAD_DIR, f"opt_{token}.jpg")
                    if whiten == "1":
                        if process_image_for_print(src_path, enhanced_path):
                            target_file = enhanced_path
                    else:
                        with Image.open(src_path) as raw_img:
                            im = ImageOps.exif_transpose(raw_img.convert("RGB"))
                            im.save(enhanced_path, format="JPEG", quality=95, dpi=(300, 300))
                            target_file = enhanced_path

                # 移除 fit-to-page，使用 natural-scaling=90 居中等比缩小，避开打印机物理边缘盲区
                cmd = [
                    "lp",
                    "-d", printer,
                    "-n", str(copies),
                    "-o", "media=A4",
                    "-o", "PageSize=A4",
                    "-o", "natural-scaling=90",
                    "-o", "position=center",
                    target_file
                ]

                print(f"[Print] 执行 CUPS 打印指令: {' '.join(cmd)}", flush=True)
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            clean_old_tmp_files(UPLOAD_DIR)
            self.write_json(True, f"共 {len(files)} 个文件任务已派发", job=", ".join(jobs))
        except Exception as e:
            self.write_json(False, f"打印服务异常: {str(e)}")
