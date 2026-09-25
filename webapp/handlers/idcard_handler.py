#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def fast_detect_skew(gray_img):
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

def process_card_image(input_path, crop_factor=1.0, whiten=True):
    """处理单面身份证：去底白化、裁剪多余杂边、根据滑块视口缩放"""
    try:
        with Image.open(input_path) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            if img.width < img.height:
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
            if not whiten:
                card_img = rgb
            else:
                channels = [np.array(c, dtype=np.float32) for c in rgb.split()]
                cleaned_channels = []
                for c_arr in channels:
                    c_pil = Image.fromarray(np.clip(c_arr, 0, 255).astype(np.uint8))
                    bg = c_pil.filter(ImageFilter.BoxBlur(radius=25))
                    bg_arr = np.array(bg, dtype=np.float32) + 1.0

                    divided = (c_arr / bg_arr) * 255.0
                    out = np.zeros_like(divided)
                    out[divided >= 195] = 255.0

                    mask_ink = divided < 195
                    ink_val = np.clip((divided[mask_ink] - 40.0) * (205.0 / (195.0 - 40.0)), 0, 255)
                    ink_val = (ink_val / 205.0) ** 1.25 * 190.0
                    out[mask_ink] = ink_val
                    cleaned_channels.append(np.clip(out, 0, 255).astype(np.uint8))

                card_img = Image.merge("RGB", [Image.fromarray(c) for c in cleaned_channels])
                card_img = card_img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=2))

            # 按照身份证标准尺寸 (85.6mm x 54mm) 裁切居中
            # 200 DPI 下，身份证标准像素大小约为 674 x 425
            target_card_w, target_card_h = 674, 425
            crop_factor = float(crop_factor)
            if crop_factor <= 0:
                crop_factor = 1.0

            cur_w, cur_h = card_img.size
            req_w = cur_w / crop_factor
            req_h = cur_h / crop_factor

            crop_left = max(0, int((cur_w - req_w) / 2))
            crop_top = max(0, int((cur_h - req_h) / 2))
            crop_right = min(cur_w, int(crop_left + req_w))
            crop_bottom = min(cur_h, int(crop_top + req_h))

            cropped = card_img.crop((crop_left, crop_top, crop_right, crop_bottom))
            resized = cropped.resize((target_card_w, target_card_h), Image.Resampling.BILINEAR)
            return resized
    except Exception as e:
        print(f"[IdCardHandler] 图像处理异常: {e}")
        return None

class IdCardHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            whiten = self.get_argument("whiten", "1") == "1"
            crop_front = float(self.get_argument("crop_front", "1.0"))
            crop_back = float(self.get_argument("crop_back", "1.0"))
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到身份证文件")
                return

            front_file = files[0]["body"] if len(files) > 0 else None
            back_file = files[1]["body"] if len(files) > 1 else None

            token = uuid.uuid4().hex[:8]
            front_path = os.path.join(UPLOAD_DIR, f"card_f_{token}.jpg")
            back_path = os.path.join(UPLOAD_DIR, f"card_b_{token}.jpg")

            with open(front_path, "wb") as f:
                f.write(front_file)

            if back_file:
                with open(back_path, "wb") as f:
                    f.write(back_file)

            # 处理正面与反面
            front_processed = process_card_image(front_path, crop_front, whiten)
            back_processed = process_card_image(back_path, crop_back, whiten) if back_file else None

            # 创建 200 DPI 标准 A4 纯白底画布 (1654 x 2338)
            a4_w, a4_h = 1654, 2338
            canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))

            if front_processed:
                x = (a4_w - front_processed.width) // 2
                y = int(a4_h * 0.22)  # 上部 22% 处
                canvas.paste(front_processed, (x, y))

            if back_processed:
                x = (a4_w - back_processed.width) // 2
                y = int(a4_h * 0.56)  # 下部 56% 处
                canvas.paste(back_processed, (x, y))

            output_path = os.path.join(UPLOAD_DIR, f"idcard_a4_{token}.jpg")
            canvas.save(output_path, format="JPEG", quality=92)

            res = self.execute_lp(printer, copies, output_path)
            if res.returncode == 0:
                self.write_json(True, "身份证 A4 任务已成功派发", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS 拒绝: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"身份证服务异常: {str(e)}")
