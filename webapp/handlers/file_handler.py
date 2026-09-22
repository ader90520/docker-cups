#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import glob
import json
import time
import tornado.web

SCAN_DIR = "/scans"

class ScanListHandler(tornado.web.RequestHandler):
    def get(self):
        files = []
        scan_files = sorted(glob.glob(os.path.join(SCAN_DIR, "*.*")), key=os.path.getmtime, reverse=True)
        for f in scan_files:
            fname = os.path.basename(f)
            files.append({
                "filename": fname,
                "url": f"/scans/{fname}",
                "size": f"{round(os.path.getsize(f) / 1024, 1)} KB",
                "is_pdf": fname.lower().endswith(".pdf"),
                "mtime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(f)))
            })
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.write(json.dumps({"success": True, "files": files}))

class ScanDeleteHandler(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            fname = os.path.basename(data.get("filename", ""))
            target = os.path.join(SCAN_DIR, fname)
            if fname and os.path.exists(target):
                os.remove(target)
                self.write(json.dumps({"success": True, "msg": f"文件 {fname} 已删除"}))
            else:
                self.set_status(404)
                self.write(json.dumps({"success": False, "msg": "文件不存在"}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))
