#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import subprocess
from PIL import Image, ImageOps
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import process_image_for_print

class IDCardHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            front_file = self.request.files.get("front", [])
            back_file = self.request.files.get("back", [])

            if not front_file or not back_file:
                self.write_json(False, "请同时上传身份证正面（人像面）和反面（国徽面）！")
                return

            token = uuid.uuid4().hex[:8]
            f_raw = os.path.join(UPLOAD_DIR, f"id_f_raw_{token}.jpg")
            b_raw = os.path.join(UPLOAD_DIR, f"id_b_raw_{token}.jpg")
            f_opt = os.path.join(UPLOAD_DIR, f"id_f_opt_{token}.jpg")
            b_opt = os.path.join(UPLOAD_DIR, f"id_b_opt_{token}.jpg")

            with open(f_raw, "wb") as f:
                f.write(front_file[0]["body"])
            with open(b_raw, "wb") as f:
                f.write(back_file[0]["body"])

            # 图像增强与去底灰
            if not process_image_for_print(f_raw, f_opt):
                f_opt = f_raw
            if not process_image_for_print(b_raw, b_opt):
                b_opt = b_raw

            # 创建标准 A4 300 DPI 画布 (2480 x 3508)
            a4_canvas = Image.new("RGB", (2480, 3508), (255, 255, 255))

            # 身份证标准比例 300 DPI: 1011 x 638 像素
            id_w, id_h = 1011, 638

            img_front = Image.open(f_opt).convert("RGB")
            img_back = Image.open(b_opt).convert("RGB")

            # 保持方向一致（宽大于高）
            if img_front.height > img_front.width:
                img_front = img_front.rotate(90, expand=True)
            if img_back.height > img_back.width:
                img_back = img_back.rotate(90, expand=True)

            img_front = img_front.resize((id_w, id_h), Image.Resampling.BICUBIC)
            img_back = img_back.resize((id_w, id_h), Image.Resampling.BICUBIC)

            # 计算上下居中位置
            pos_x = (2480 - id_w) // 2
            pos_y1 = 650
            pos_y2 = 650 + id_h + 350

            a4_canvas.paste(img_front, (pos_x, pos_y1))
            a4_canvas.paste(img_back, (pos_x, pos_y2))

            final_id_path = os.path.join(UPLOAD_DIR, f"idcard_final_{token}.jpg")
            a4_canvas.save(final_id_path, format="JPEG", quality=95, dpi=(300, 300))

            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            cmd = [
                "lp",
                "-d", printer,
                "-n", str(copies),
                "-o", "media=A4",
                "-o", "PageSize=A4",
                "-o", "fit-to-page",
                "-o", "natural-scaling=95",
                "-o", "position=center",
                final_id_path
            ]

            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            if res.returncode == 0:
                self.write_json(True, "身份证 1:1 拼版打印任务已成功发送！", data={"url": f"/static/uploads/{os.path.basename(final_id_path)}"})
            else:
                self.write_json(False, f"CUPS拒绝打印: {res.stderr.strip()}")

        except Exception as e:
            self.write_json(False, f"身份证拼版异常: {str(e)}")
