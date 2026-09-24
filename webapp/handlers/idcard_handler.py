#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image, ImageOps
from handlers.base_handler import BaseHandler, UPLOAD_DIR

class IdCardPrintHandler(BaseHandler):
    @staticmethod
    def crop_by_viewport(pil_img, zoom_factor=1.0):
        try:
            pil_img = ImageOps.exif_transpose(pil_img)
            if pil_img.height > pil_img.width:
                pil_img = pil_img.rotate(270, expand=True)

            w, h = pil_img.size
            target_ratio = 85.6 / 54.0
            cur_ratio = w / h

            if cur_ratio > target_ratio:
                box_h = h
                box_w = int(h * target_ratio)
            else:
                box_w = w
                box_h = int(w / target_ratio)

            factor = max(0.5, float(zoom_factor))
            crop_w = min(w, int(box_w / factor))
            crop_h = min(h, int(box_h / factor))

            cx, cy = w // 2, h // 2
            x0 = max(0, cx - crop_w // 2)
            y0 = max(0, cy - crop_h // 2)
            return pil_img.crop((x0, y0, min(w, x0 + crop_w), min(h, y0 + crop_h)))
        except Exception:
            return pil_img

    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            crop_front = float(self.get_argument("crop_front", "1.0"))
            crop_back = float(self.get_argument("crop_back", "1.0"))

            files = self.request.files.get("file", [])
            if not files:
                self.write_json(False, "未收到身份证图片")
                return

            saved_paths = []
            for f in files[:2]:
                p = os.path.join(UPLOAD_DIR, f"id_raw_{uuid.uuid4().hex[:8]}.jpg")
                with open(p, "wb") as out:
                    out.write(f["body"])
                saved_paths.append(p)

            a4_w, a4_h = 2480, 3508
            card_w, card_h = 1012, 638

            canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
            positions = [((a4_w - card_w) // 2, 580), ((a4_w - card_w) // 2, 1780)]
            zoom_list = [crop_front, crop_back]

            for idx, path in enumerate(saved_paths):
                try:
                    with Image.open(path) as img:
                        cropped = self.crop_by_viewport(img, zoom_list[idx] if idx < len(zoom_list) else 1.0)
                        card_ready = cropped.resize((card_w, card_h), Image.Resampling.LANCZOS)
                        canvas.paste(card_ready, positions[idx])
                except Exception as e:
                    print(f"[IdCard] 异常: {e}")

            out_file = os.path.join(UPLOAD_DIR, f"idcard_final_{uuid.uuid4().hex[:8]}.jpg")
            canvas.save(out_file, format="JPEG", quality=92)

            res = self.execute_lp(printer, copies, out_file)
            if res.returncode == 0:
                self.write_json(True, "身份证双面合成打印任务已提交", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS 拒绝: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"合成处理异常: {str(e)}")
