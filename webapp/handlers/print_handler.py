#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image
from handlers.base_handler import BaseHandler, UPLOAD_DIR

class PrintHandler(BaseHandler):
    """通用/试卷/去底灰打印处理器"""
    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            whiten = self.get_argument("whiten", "0")
            deskew = self.get_argument("deskew", "0")
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到文件")
                return

            f = files[0]
            ext = os.path.splitext(f["filename"])[-1].lower()
            token = uuid.uuid4().hex[:8]
            src_path = os.path.join(UPLOAD_DIR, f"print_{token}{ext}")
            with open(src_path, "wb") as out:
                out.write(f["body"])

            target_path = src_path

            # 如果用户勾选了去底灰或倾斜纠正
            if (whiten == "1" or deskew == "1") and ext in [".jpg", ".jpeg", ".png"]:
                try:
                    with Image.open(src_path) as img:
                        img = img.convert("L")
                        if max(img.size) > 2000:
                            img.thumbnail((2000, 2000), Image.Resampling.BILINEAR)
                        arr = np.array(img, dtype=np.float32)

                        # 去底灰阶梯拉伸
                        if whiten == "1":
                            arr = np.clip((arr - 45) * (255.0 / (200 - 45)), 0, 255)

                        clean_arr = arr.astype(np.uint8)
                        target_path = os.path.join(UPLOAD_DIR, f"clean_{token}.jpg")
                        Image.fromarray(clean_arr).save(target_path, format="JPEG", quality=85)
                except Exception:
                    target_path = src_path

            res = self.execute_lp(printer, copies, target_path)
            if res.returncode == 0:
                self.write_json(True, "任务派发成功", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")

        except Exception as e:
            self.write_json(False, f"打印服务异常: {str(e)}")
