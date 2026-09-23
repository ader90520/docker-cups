#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
from PIL import Image
from handlers.base_handler import BaseHandler, UPLOAD_DIR

class InvoicePrintHandler(BaseHandler):
    """发票专用：识别 PDF/图片发票，极速排版防裁边"""
    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到上传的发票")
                return

            f = files[0]
            ext = os.path.splitext(f["filename"])[-1].lower()
            file_token = uuid.uuid4().hex[:8]
            src_path = os.path.join(UPLOAD_DIR, f"inv_{file_token}{ext}")
            with open(src_path, "wb") as out:
                out.write(f["body"])

            target_path = src_path
            extra_opts = []

            # 1. 若为常见图片格式，检查方向
            if ext in [".jpg", ".jpeg", ".png"]:
                try:
                    with Image.open(src_path) as img:
                        # 横向发票旋转为竖直排版，并转为高质量黑白灰度
                        if img.width > img.height:
                            img = img.rotate(270, expand=True)
                        target_path = os.path.join(UPLOAD_DIR, f"inv_ready_{file_token}.jpg")
                        img.convert("L").save(target_path, format="JPEG", quality=90)
                except Exception:
                    target_path = src_path

            # 2. 若为 PDF 发票，直接透传给 CUPS 并注入页面缩放参数
            elif ext == ".pdf":
                extra_opts.append("fit-to-page")

            # 派发打印作业
            res = self.execute_lp(printer, copies, target_path, extra_opts=extra_opts)
            if res.returncode == 0:
                self.write_json(True, "发票已成功提交打印", job=res.stdout.strip())
            else:
                self.write_json(False, f"打印拒绝: {res.stderr.strip()}")

        except Exception as e:
            self.write_json(False, f"发票处理故障: {str(e)}")
