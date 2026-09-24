#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def detect_skew_angle(gray_img):
    try:
        w, h = gray_img.size
        scale = 320.0 / max(w, h)
        small = gray_img.resize((int(w * scale), int(h * scale)), Image.Resampling.NEAREST)
        arr = np.array(small, dtype=np.float32)

        grad = np.abs(arr[2:, :] - arr[:-2, :])
        grad[grad < 30] = 0.0

        best_angle = 0.0
        max_var = 0.0
        for angle in np.arange(-4.0, 4.5, 0.5):
            rot = Image.fromarray(grad).rotate(angle, resample=Image.Resampling.NEAREST)
            proj = np.sum(np.array(rot), axis=1)
            var = np.var(proj)
            if var > max_var:
                max_var = var
                best_angle = angle
        return best_angle
    except Exception:
        return 0.0

def process_camscanner_a4(input_path, output_path):
    try:
        with Image.open(input_path) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            if img.width > img.height:
                img = img.rotate(270, expand=True)

            gray_deskew = img.convert("L")
            angle = detect_skew_angle(gray_deskew)
            if abs(angle) > 0.15:
                img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(255, 255, 255))

            w, h = img.size
            cx, cy = int(w * 0.035), int(h * 0.035)
            img = img.crop((cx, cy, w - cx, h - cy))

            if max(img.size) > 2400:
                img.thumbnail((2400, 2400), Image.Resampling.BILINEAR)

            rgb = img.convert("RGB")
            arr = np.array(rgb, dtype=np.float32)

            bg = rgb.filter(ImageFilter.GaussianBlur(radius=50))
            bg_arr = np.array(bg, dtype=np.float32) + 1e-4

            divided = (arr / bg_arr) * 255.0

            r, g, b = divided[:, :, 0], divided[:, :, 1], divided[:, :, 2]
            max_c = np.maximum(np.maximum(r, g), b)
            min_c = np.minimum(np.minimum(r, g), b)
            chroma = max_c - min_c

            is_red = (r > (g + 12.0)) & (r > (b + 12.0)) & (chroma > 15.0)
            lum = 0.299 * r + 0.587 * g + 0.114 * b

            effective_lum = np.where(is_red, lum * 0.50, lum)
            effective_lum = np.where(~is_red & (chroma > 15.0), effective_lum * 0.75, effective_lum)

            lum_pil = Image.fromarray(effective_lum.astype(np.uint8))
            min_filtered = lum_pil.filter(ImageFilter.MinFilter(size=3))
            min_arr = np.array(min_filtered, dtype=np.float32)

            line_detail = np.maximum(0.0, effective_lum - min_arr)
            enhanced_lum = effective_lum - line_detail * 0.55

            boosted = np.clip((enhanced_lum - 45.0) * (255.0 / (220.0 - 45.0)), 0, 255)
            boosted = (boosted / 255.0) ** 1.25 * 255.0

            boosted[boosted > 216] = 255
            boosted[boosted < 125] = boosted[boosted < 125] * 0.40

            clean_img = Image.fromarray(boosted.astype(np.uint8))
            sharp_img = clean_img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=180, threshold=2))

            a4_w, a4_h = 2480, 3508
            canvas = Image.new("L", (a4_w, a4_h), 255)
            margin = 50
            target_w, target_h = a4_w - margin * 2, a4_h - margin * 2

            ratio = min(target_w / sharp_img.width, target_h / sharp_img.height)
            new_w, new_h = int(sharp_img.width * ratio), int(sharp_img.height * ratio)

            resized = sharp_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))

            canvas.save(output_path, format="JPEG", quality=95)
            return True
    except Exception as e:
        print(f"[CamScannerEngine] 异常: {e}")
        return False

class PrintHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            whiten = self.get_argument("whiten", "0")
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到文件")
                return

            jobs = []
            for f in files:
                ext = os.path.splitext(f["filename"])[-1].lower()
                token = uuid.uuid4().hex[:8]
                src_path = os.path.join(UPLOAD_DIR, f"raw_{token}{ext}")
                with open(src_path, "wb") as out:
                    out.write(f["body"])

                target_path = src_path
                if whiten == "1" and ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                    enhanced_path = os.path.join(UPLOAD_DIR, f"cam_{token}.jpg")
                    if process_camscanner_a4(src_path, enhanced_path):
                        target_path = enhanced_path

                res = self.execute_lp(printer, copies, target_path)
                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            self.write_json(True, f"共 {len(files)} 个文件已按印刷级全能王标准排版打印", job=", ".join(jobs))
        except Exception as e:
            self.write_json(False, f"打印服务异常: {str(e)}")
