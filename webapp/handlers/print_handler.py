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

def process_camscanner_stream(input_path, output_path):
    """
    极速流畅连续走纸引擎 (200 DPI，彻底杜绝连续多页停顿卡顿):
    1. EXIF 纠正与微纠偏
    2. 裁除外沿 3% 暗边
    3. 稳定背景除法 (纯白底，字迹深黑，杜绝黑白反相)
    4. 红色通道下压加黑 (保护浅红虚线框、题号与迷宫走线)
    5. 200 DPI A4 规范居中 (数据量降低60%，消除打印机缓存等待)
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
            r, g, b = [np.array(c, dtype=np.float32) for c in rgb.split()]

            # 红色通道特征下压 (保证红线与彩色元素不发浅发虚)
            is_red = (r > (g + 15.0)) & (r > (b + 15.0))
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            lum = np.where(is_red, lum * 0.65, lum)

            gray_pil = Image.fromarray(np.clip(lum, 0, 255).astype(np.uint8))
            bg = gray_pil.filter(ImageFilter.BoxBlur(radius=25))
            bg_arr = np.array(bg, dtype=np.float32) + 1.0

            # 稳健局部背景相除
            divided = (lum / bg_arr) * 255.0

            out = np.zeros_like(divided)
            # 背景区彻底推为 255 纯白
            out[divided >= 195] = 255.0

            # 笔迹区正向非线性加深
            mask_ink = divided < 195
            ink_val = np.clip((divided[mask_ink] - 40.0) * (200.0 / (195.0 - 40.0)), 0, 255)
            ink_val = (ink_val / 200.0) ** 1.35 * 180.0
            out[mask_ink] = ink_val

            clean_img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
            sharp_img = clean_img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=130, threshold=2))

            # 200 DPI 标准 A4 画布 (1654 x 2338) 居中排版
            a4_w, a4_h = 1654, 2338
            canvas = Image.new("L", (a4_w, a4_h), 255)
            margin = 35
            target_w, target_h = a4_w - margin * 2, a4_h - margin * 2

            ratio = min(target_w / sharp_img.width, target_h / sharp_img.height)
            new_w, new_h = int(sharp_img.width * ratio), int(sharp_img.height * ratio)

            resized = sharp_img.resize((new_w, new_h), Image.Resampling.BILINEAR)
            canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))

            canvas.save(output_path, format="JPEG", quality=90)
            return True
    except Exception as e:
        print(f"[CamScannerStream] 处理异常: {e}")
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
                    if process_camscanner_stream(src_path, enhanced_path):
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
