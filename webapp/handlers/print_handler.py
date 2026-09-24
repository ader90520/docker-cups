#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def process_camscanner_a4(input_path, output_path):
    try:
        with Image.open(input_path) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            if img.width > img.height:
                img = img.rotate(270, expand=True)

            gray = img.convert("L")
            if max(gray.size) > 2200:
                gray.thumbnail((2200, 2200), Image.Resampling.BILINEAR)

            bg = gray.filter(ImageFilter.GaussianBlur(radius=25))
            orig_arr = np.array(gray, dtype=np.float32)
            bg_arr = np.array(bg, dtype=np.float32) + 1e-5

            divided = (orig_arr / bg_arr) * 255.0
            divided = np.clip((divided - 50.0) * (255.0 / (205.0 - 50.0)), 0, 255)
            clean_arr = divided.astype(np.uint8)
            clean_arr[clean_arr > 215] = 255

            whitened_img = Image.fromarray(clean_arr)
            a4_w, a4_h = 2480, 3508
            canvas = Image.new("L", (a4_w, a4_h), 255)

            margin = 80
            target_w, target_h = a4_w - margin * 2, a4_h - margin * 2
            ratio = min(target_w / whitened_img.width, target_h / whitened_img.height)
            new_w, new_h = int(whitened_img.width * ratio), int(whitened_img.height * ratio)

            resized = whitened_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))
            canvas.convert("RGB").save(output_path, format="JPEG", quality=92)
            return True
    except Exception as e:
        print(f"[CamScanner] 算法异常: {e}")
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

            self.write_json(True, f"共 {len(files)} 个文件任务派发成功", job=", ".join(jobs))
        except Exception as e:
            self.write_json(False, f"打印服务异常: {str(e)}")
