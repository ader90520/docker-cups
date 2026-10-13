#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import os
import re
import uuid
import subprocess
from PIL import Image
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import clean_old_tmp_files

def crop_to_idcard_ratio(img):
    w, h = img.size
    target_ratio = 85.6 / 54.0
    current_ratio = w / float(h)
    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))

class IDCardHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            front_files = self.request.files.get("front", [])
            back_files = self.request.files.get("back", [])

            if not front_files or not back_files:
                self.write_json(False, "必须同时包含正面与反面照片")
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

            token = uuid.uuid4().hex[:8]
            total_persons = min(len(front_files), len(back_files), 3)

            canvas = Image.new("RGB", (2480, 3508), (255, 255, 255))
            card_w, card_h = 1010, 638

            if total_persons == 1:
                img_f = crop_to_idcard_ratio(Image.open(io.BytesIO(front_files[0]["body"])).convert("RGB"))
                rf = img_f.resize((card_w, card_h), Image.Resampling.BICUBIC)
                canvas.paste(rf, ((2480 - card_w) // 2, 700))

                img_b = crop_to_idcard_ratio(Image.open(io.BytesIO(back_files[0]["body"])).convert("RGB"))
                rb = img_b.resize((card_w, card_h), Image.Resampling.BICUBIC)
                canvas.paste(rb, ((2480 - card_w) // 2, 1900))
            else:
                gap_y = 3508 // (total_persons + 1)
                for idx in range(total_persons):
                    img_f = crop_to_idcard_ratio(Image.open(io.BytesIO(front_files[idx]["body"])).convert("RGB"))
                    rf = img_f.resize((960, 606), Image.Resampling.BICUBIC)
                    img_b = crop_to_idcard_ratio(Image.open(io.BytesIO(back_files[idx]["body"])).convert("RGB"))
                    rb = img_b.resize((960, 606), Image.Resampling.BICUBIC)
                    pos_y = int((idx + 0.5) * gap_y)
                    canvas.paste(rf, (180, pos_y))
                    canvas.paste(rb, (1340, pos_y))

            merged_path = os.path.join(UPLOAD_DIR, f"idcard_{token}.jpg")
            canvas.save(merged_path, format="JPEG", quality=95, dpi=(300, 300))

            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            cmd = [
                "lp",
                "-d", printer,
                "-n", str(copies_int),
                "-o", "media=A4",
                "-o", "PageSize=A4",
                "-o", "fit-to-page",
                "-o", "position=center",
                "-o", "ColorModel=K",
                "-o", "print-color-mode=monochrome",
                merged_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            clean_old_tmp_files(UPLOAD_DIR)

            if res.returncode == 0:
                self.write_json(True, f"已将 {total_persons} 人身份证拼版下发打印！", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS 拒绝: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"拼版异常: {str(e)}")
