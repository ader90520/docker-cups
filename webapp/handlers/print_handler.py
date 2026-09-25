#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def fast_detect_skew(gray_img):
    """微采样水平倾斜角度估计（耗时 < 0.05s）"""
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

def process_camscanner_color_stream(input_path, output_path):
    """
    全能王真彩色保留去底引擎 (200 DPI RGB 流式输出):
    1. EXIF 方向修正与水平微纠偏
    2. 裁剪 3% 暗黑外边沿
    3. RGB 三通道分别做局部背景除法，纸张底色 100% 漂白为 (255, 255, 255)
    4. 完美保留红章、红线、彩图、蓝黑墨水笔迹原生色彩
    5. 居中排版至 200 DPI A4 画布，连续流畅吐纸
    """
    try:
        with Image.open(input_path) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            if img.width > img.height:
                img = img.rotate(270, expand=True)

            gray_small = img.convert("L")
            angle = fast_detect_skew(gray_small)
            if abs(angle) >= 1.0:
                img = img.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=(255, 255, 255))

            w, h = img.size
            cx, cy = int(w * 0.03), int(h * 0.03)
            img = img.crop((cx, cy, w - cx, h - cy))

            if max(img.size) > 1600:
                img.thumbnail((1600, 1600), Image.Resampling.BILINEAR)

            rgb = img.convert("RGB")
            channels = [np.array(c, dtype=np.float32) for c in rgb.split()]
            cleaned_channels = []

            # 对 R、G、B 分别进行局部背景除法白化
            for c_arr in channels:
                c_pil = Image.fromarray(np.clip(c_arr, 0, 255).astype(np.uint8))
                bg = c_pil.filter(ImageFilter.BoxBlur(radius=25))
                bg_arr = np.array(bg, dtype=np.float32) + 1.0

                divided = (c_arr / bg_arr) * 255.0

                out = np.zeros_like(divided)
                # 底色推为纯白 255
                out[divided >= 195] = 255.0

                # 字迹与彩色区域对比度拉深
                mask_ink = divided < 195
                ink_val = np.clip((divided[mask_ink] - 40.0) * (205.0 / (195.0 - 40.0)), 0, 255)
                ink_val = (ink_val / 205.0) ** 1.25 * 190.0
                out[mask_ink] = ink_val
                cleaned_channels.append(np.clip(out, 0, 255).astype(np.uint8))

            # 合成真彩色图像
            clean_rgb = Image.merge("RGB", [Image.fromarray(c) for c in cleaned_channels])
            sharp_rgb = clean_rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=2))

            # 200 DPI 标准 A4 画布 (1654 x 2338) 纯白底 RGB
            a4_w, a4_h = 1654, 2338
            canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
            margin = 35
            target_w, target_h = a4_w - margin * 2, a4_h - margin * 2

            ratio = min(target_w / sharp_rgb.width, target_h / sharp_rgb.height)
            new_w, new_h = int(sharp_rgb.width * ratio), int(sharp_rgb.height * ratio)

            resized = sharp_rgb.resize((new_w, new_h), Image.Resampling.BILINEAR)
            canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))

            canvas.save(output_path, format="JPEG", quality=90)
            return True
    except Exception as e:
        print(f"[CamScannerColor] 处理异常: {e}")
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
                    if process_camscanner_color_stream(src_path, enhanced_path):
                        target_path = enhanced_path

                res = self.execute_lp(printer, copies, target_path)
                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            self.write_json(True, f"共 {len(files)} 个文件任务已派发", job=", ".join(jobs))
        except Exception as e:
            self.write_json(False, f"打印服务异常: {str(e)}")
