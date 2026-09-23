#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import subprocess
from PIL import Image
from handlers.base_handler import BaseHandler

UPLOAD_DIR = "/tmp/cups_web_uploads"

class InvoicePrintHandler(BaseHandler):
    """发票打印：自动纠正横竖版、避免被 A4 裁边"""
    def post(self):
        printer = self.get_argument("printer", "")
        copies = self.get_argument("copies", "1")
        files = self.request.files.get("file", [])

        if not files:
            self.write_json(False, "未收到上传的发票文件")
            return

        f = files[0]
        ext = os.path.splitext(f["filename"])[-1].lower()
        src_path = os.path.join(UPLOAD_DIR, f"inv_src_{uuid.uuid4().hex}{ext}")
        with open(src_path, "wb") as out:
            out.write(f["body"])

        # 发票图像自适应处理
        target_path = src_path
        if ext in [".jpg", ".jpeg", ".png"]:
            try:
                img = Image.open(src_path).convert("RGB")
                if img.width > img.height:  # 横向电子发票自动旋转
                    img = img.rotate(270, expand=True)
                target_path = os.path.join(UPLOAD_DIR, f"inv_ready_{uuid.uuid4().hex}.jpg")
                img.save(target_path, quality=95)
            except Exception:
                target_path = src_path

        # 提交打印
        cmd = ["lp", "-d", printer, "-n", str(copies), "-o", "fit-to-page", target_path] if printer else ["lp", "-n", str(copies), "-o", "fit-to-page", target_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        
        if res.returncode == 0:
            self.write_json(True, "发票打印任务已成功派发", job=res.stdout.strip())
        else:
            self.write_json(False, f"CUPS拒绝打印: {res.stderr.strip()}")
