#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import uuid
import subprocess
from PIL import Image
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import process_image_for_print, clean_old_tmp_files

class IDCardHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            front_files = self.request.files.get("front", [])
            back_files = self.request.files.get("back", [])

            if not front_files or not back_files:
                self.write_json(False, "必须同时上传身份证正面与反面照片")
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
            # 计算总人数，最多允许 3 人拼版在同一张 A4
            total_persons = min(len(front_files), len(back_files), 3)

            # A4 300DPI 标准像素尺寸: 2480 x 3508
            canvas = Image.new("RGB", (2480, 3508), (255, 255, 255))
            # 身份证标准 1:1 规格: 85.6mm x 54mm -> 300DPI 下为 1010 x 638 像素
            card_w, card_h = 1010, 638

            if total_persons == 1:
                # ================= 1 人标准排版 (上下垂直居中) =================
                f_src = os.path.join(UPLOAD_DIR, f"idf_raw_{token}_0.jpg")
                b_src = os.path.join(UPLOAD_DIR, f"idb_raw_{token}_0.jpg")
                with open(f_src, "wb") as f:
                    f.write(front_files[0]["body"])
                with open(b_src, "wb") as f:
                    f.write(back_files[0]["body"])

                f_opt = os.path.join(UPLOAD_DIR, f"idf_opt_{token}_0.jpg")
                b_opt = os.path.join(UPLOAD_DIR, f"idb_opt_{token}_0.jpg")

                if not process_image_for_print(f_src, f_opt):
                    f_opt = f_src
                if not process_image_for_print(b_src, b_opt):
                    b_opt = b_src

                with Image.open(f_opt) as img_f:
                    rf = img_f.resize((card_w, card_h), Image.Resampling.BICUBIC)
                    canvas.paste(rf, ((2480 - card_w) // 2, 700))

                with Image.open(b_opt) as img_b:
                    rb = img_b.resize((card_w, card_h), Image.Resampling.BICUBIC)
                    canvas.paste(rb, ((2480 - card_w) // 2, 1900))

            else:
                # ================= 多人 (2~3人) 同页排版 (左列正面, 右列反面) =================
                multi_w, multi_h = 960, 606  # 适应两列的等比标准尺寸
                left_x = 180
                right_x = 1340
                gap_y = 3508 // (total_persons + 1)

                for idx in range(total_persons):
                    f_src = os.path.join(UPLOAD_DIR, f"idf_raw_{token}_{idx}.jpg")
                    b_src = os.path.join(UPLOAD_DIR, f"idb_raw_{token}_{idx}.jpg")
                    with open(f_src, "wb") as f:
                        f.write(front_files[idx]["body"])
                    with open(b_src, "wb") as f:
                        f.write(back_files[idx]["body"])

                    f_opt = os.path.join(UPLOAD_DIR, f"idf_opt_{token}_{idx}.jpg")
                    b_opt = os.path.join(UPLOAD_DIR, f"idb_opt_{token}_{idx}.jpg")

                    if not process_image_for_print(f_src, f_opt):
                        f_opt = f_src
                    if not process_image_for_print(b_src, b_opt):
                        b_opt = b_src

                    pos_y = int((idx + 0.5) * gap_y)

                    with Image.open(f_opt) as img_f:
                        rf = img_f.resize((multi_w, multi_h), Image.Resampling.BICUBIC)
                        canvas.paste(rf, (left_x, pos_y))

                    with Image.open(b_opt) as img_b:
                        rb = img_b.resize((multi_w, multi_h), Image.Resampling.BICUBIC)
                        canvas.paste(rb, (right_x, pos_y))

            merged_path = os.path.join(UPLOAD_DIR, f"idcard_final_{token}.jpg")
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
                self.write_json(True, f"已成功将 {total_persons} 人的身份证拼版至单张 A4 纸下发打印！", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"拼版打印异常: {str(e)}")
