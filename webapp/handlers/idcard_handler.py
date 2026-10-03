#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import os
import re
import uuid
import subprocess
from PIL import Image, ImageOps
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import process_image_for_print, clean_old_tmp_files

def crop_to_idcard_ratio(img):
    """按二代身份证 85.6 : 54 物理长宽比安全裁切，去除拍照多余背景"""
    w, h = img.size
    target_ratio = 85.6 / 54.0
    current_ratio = w / float(h)

    if current_ratio > target_ratio:
        # 过宽，裁左右多余背景
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        # 过长/过高，裁上下多余背景
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

            # 获取前端传回的正反面旋转角度参数（以逗号分隔，如 "90,0,180"）
            rot_f_list = [int(x) for x in self.get_argument("rot_f", "").split(",") if x.isdigit()]
            rot_b_list = [int(x) for x in self.get_argument("rot_b", "").split(",") if x.isdigit()]

            if not front_files or not back_files:
                self.write_json(False, "必须同时上传正面与反面照片")
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

            # A4 300DPI 标准像素尺寸: 2480 x 3508
            canvas = Image.new("RGB", (2480, 3508), (255, 255, 255))
            card_w, card_h = 1010, 638

            if total_persons == 1:
                # 1人标准排版：居中上下排布
                f_rot = rot_f_list[0] if len(rot_f_list) > 0 else 0
                b_rot = rot_b_list[0] if len(rot_b_list) > 0 else 0

                # 正面处理
                img_f = Image.open(io.BytesIO(front_files[0]["body"])).convert("RGB")
                if f_rot != 0:
                    img_f = img_f.rotate(-f_rot, expand=True)
                img_f = crop_to_idcard_ratio(img_f)
                rf = img_f.resize((card_w, card_h), Image.Resampling.BICUBIC)
                canvas.paste(rf, ((2480 - card_w) // 2, 700))

                # 反面处理
                img_b = Image.open(io.BytesIO(back_files[0]["body"])).convert("RGB")
                if b_rot != 0:
                    img_b = img_b.rotate(-b_rot, expand=True)
                img_b = crop_to_idcard_ratio(img_b)
                rb = img_b.resize((card_w, card_h), Image.Resampling.BICUBIC)
                canvas.paste(rb, ((2480 - card_w) // 2, 1900))

            else:
                # 多人 (2~3人) 同页排版：左列正面，右列反面
                multi_w, multi_h = 960, 606
                left_x = 180
                right_x = 1340
                gap_y = 3508 // (total_persons + 1)

                for idx in range(total_persons):
                    f_rot = rot_f_list[idx] if idx < len(rot_f_list) else 0
                    b_rot = rot_b_list[idx] if idx < len(rot_b_list) else 0

                    img_f = Image.open(io.BytesIO(front_files[idx]["body"])).convert("RGB")
                    if f_rot != 0:
                        img_f = img_f.rotate(-f_rot, expand=True)
                    img_f = crop_to_idcard_ratio(img_f)
                    rf = img_f.resize((multi_w, multi_h), Image.Resampling.BICUBIC)

                    img_b = Image.open(io.BytesIO(back_files[idx]["body"])).convert("RGB")
                    if b_rot != 0:
                        img_b = img_b.rotate(-b_rot, expand=True)
                    img_b = crop_to_idcard_ratio(img_b)
                    rb = img_b.resize((multi_w, multi_h), Image.Resampling.BICUBIC)

                    pos_y = int((idx + 0.5) * gap_y)
                    canvas.paste(rf, (left_x, pos_y))
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
                self.write_json(True, f"已成功将 {total_persons} 人的身份证拼版至单张 A4 纸下发打印！")
            else:
                self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"拼版打印异常: {str(e)}")
