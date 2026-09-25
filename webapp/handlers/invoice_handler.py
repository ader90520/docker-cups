#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import subprocess
from handlers.base_handler import BaseHandler, UPLOAD_DIR

class InvoiceHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到发票文件")
                return

            jobs = []
            for f in files:
                ext = os.path.splitext(f["filename"])[-1].lower()
                token = uuid.uuid4().hex[:8]
                src_path = os.path.join(UPLOAD_DIR, f"inv_{token}{ext}")
                with open(src_path, "wb") as out:
                    out.write(f["body"])

                res = self.execute_lp(printer, copies, src_path)
                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS 拒绝: {res.stderr.strip()}")
                    return

            self.write_json(True, f"共 {len(files)} 份发票已派发打印", job=", ".join(jobs))
        except Exception as e:
            self.write_json(False, f"发票服务异常: {str(e)}")
