#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import subprocess
from PIL import Image
from handlers.base_handler import BaseHandler

UPLOAD_DIR = "/tmp/cups_web_uploads"

class IdCardPrintHandler(BaseHandler):
    """身份证打印：将单面或双面照片合成至标准 A4 页面排版"""
    def post(self):
        printer = self.get_argument("printer", "")
        copies = self.get_argument("copies", "1")
        files = self.request.files.get("file", [])

        if not files:
            self.write_json(False, "未收到身份证图片")
            return

        saved_paths = []
        for f in files:
            p = os.path.join(UPLOAD_DIR, f"id_{uuid.uuid4().hex}.jpg")
            with open(p, "wb") as out:
                out.write(f["body"])
            saved_paths.append(p)

        # 300 DPI A4 画布 (2479 x 3508)，身份证真实物理比例 1011 x 638
        a4_w, a4_h = 2479, 3508
        card_w, card_h = 1011, 638
        canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
        
        # 上下排列两个卡槽坐标
        slots = [((a4_w - card_w) // 2, 600), ((a4_w - card_w) // 2, 1750)]
        for idx, img_path in enumerate(saved_paths[:2]):
            try:
                card = Image.open(img_path).convert("RGB")
                card = card.resize((card_w, card_h), Image.Resampling.LANCZOS)
                canvas.paste(card, slots[idx])
            except Exception:
                pass

        ready_path = os.path.join(UPLOAD_DIR, f"idcard_a4_{uuid.uuid4().hex}.jpg")
        canvas.save(ready_path, quality=95)

        cmd = ["lp", "-d", printer, "-n", str(copies), "-o", "fit-to-page", ready_path] if printer else ["lp", "-n", str(copies), "-o", "fit-to-page", ready_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        
        if res.returncode == 0:
            self.write_json(True, "身份证排版打印成功", job=res.stdout.strip())
        else:
            self.write_json(False, f"CUPS拒绝打印: {res.stderr.strip()}")
