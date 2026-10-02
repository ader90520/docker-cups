#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import glob
import json
import time
import shutil
import tornado.ioloop
import tornado.web
import tornado.httpserver
from tornado.web import StaticFileHandler

from handlers.print_handler import PrintHandler
from handlers.idcard_handler import IDCardHandler
from handlers.invoice_handler import InvoiceHandler
from handlers.device_handler import DevicesHandler
from handlers.printer_admin_handler import PrinterAdminHandler
from handlers.mail_config_handler import MailConfigHandler
from handlers.scan_handler import ScanProbeHandler, ScanHandler, DownloadScanHandler, PreviewScanHandler

STATIC_PATH = "/opt/webapp/static"
BASE_PATH = "/opt/webapp"
SCANS_PATH = "/scans"

os.makedirs(SCANS_PATH, exist_ok=True)
os.makedirs(STATIC_PATH, exist_ok=True)

# 确保前端 index.html 即使在根目录也能被识别
if os.path.exists(os.path.join(BASE_PATH, "index.html")) and not os.path.exists(os.path.join(STATIC_PATH, "index.html")):
    try:
        shutil.copy2(os.path.join(BASE_PATH, "index.html"), os.path.join(STATIC_PATH, "index.html"))
    except Exception:
        pass

class ScanListHandler(tornado.web.RequestHandler):
    def get(self):
        files = []
        scan_files = sorted(glob.glob(os.path.join(SCANS_PATH, "*.*")), key=os.path.getmtime, reverse=True)
        for f in scan_files:
            fname = os.path.basename(f)
            files.append({
                "filename": fname,
                "url": f"/scans/{fname}",
                "download_url": f"/api/scan/download?file={fname}",
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
            target = os.path.join(SCANS_PATH, fname)
            if fname and os.path.exists(target):
                os.remove(target)
                self.write(json.dumps({"success": True, "msg": f"文件 {fname} 已删除"}))
            else:
                self.set_status(404)
                self.write(json.dumps({"success": False, "msg": "文件不存在"}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))

def make_app():
    handlers = [
        # 静态文件及历史扫描映射
        (r"/scans/(.*)", StaticFileHandler, {"path": SCANS_PATH}),
        (r"/static/(.*)", StaticFileHandler, {"path": STATIC_PATH}),
        (r"/(favicon\.ico)", StaticFileHandler, {"path": STATIC_PATH}),
        (r"/", StaticFileHandler, {"path": STATIC_PATH, "default_filename": "index.html"}),

        # 核心业务打印接口
        (r"/api/print", PrintHandler),
        (r"/api/idcard", IDCardHandler),
        (r"/api/invoice", InvoiceHandler),
        (r"/api/devices", DevicesHandler),
        (r"/api/printer_admin", PrinterAdminHandler),
        (r"/api/mail_config", MailConfigHandler),

        # 完整扫描仪接口
        (r"/api/scan/devices", ScanProbeHandler),
        (r"/api/scan", ScanHandler),
        (r"/api/scan/download", DownloadScanHandler),
        (r"/api/scan/preview", PreviewScanHandler),
        (r"/api/scan/list", ScanListHandler),
        (r"/api/scan/delete", ScanDeleteHandler),
    ]

    settings = {
        "debug": False,
        "autoreload": False
    }

    return tornado.web.Application(handlers, **settings)

if __name__ == "__main__":
    app = make_app()
    server = tornado.httpserver.HTTPServer(app, max_buffer_size=104857600)
    server.listen(8088, address="0.0.0.0")
    print(">>> CUPS 智能工作台已在 8088 端口正常启动 (Listen: 0.0.0.0:8088)", flush=True)
    tornado.ioloop.IOLoop.current().start()
