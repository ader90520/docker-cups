#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import gc
import sys
import glob
import json
import time
import tornado.ioloop
import tornado.web
import tornado.httpserver
from tornado.web import StaticFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from handlers.print_handler import PrintHandler
from handlers.idcard_handler import IDCardHandler
from handlers.device_handler import DevicesHandler
from handlers.printer_admin_handler import PrinterAdminHandler
from handlers.mail_handler import MailConfigHandler
from handlers.scan_handler import (
    ScanProbeHandler,
    ScanHandler,
    DownloadScanHandler,
    PreviewScanHandler,
    ScanPrintHandler
)
from handlers.enhance_handler import EnhancePreviewHandler

STATIC_PATH = os.path.join(BASE_DIR, "static")
SCANS_PATH = "/scans"

os.makedirs(SCANS_PATH, exist_ok=True)
os.makedirs(STATIC_PATH, exist_ok=True)

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

class SystemAdminRouteAdapter(PrinterAdminHandler):
    def get(self, route_type):
        if route_type == "discover":
            self.get_argument = lambda k, d=None: "discovered_devices" if k == "action" else d
        elif route_type == "search":
            q_val = tornado.web.RequestHandler.get_argument(self, "q", "")
            self.get_argument = lambda k, d=None: "drivers" if k == "action" else (q_val if k == "q" else d)
        super().get()

    def post(self, route_type):
        action_map = {
            "reset_usb": "reset_usb",
            "clear": "cancel_all",
            "clean_tmp": "clean_disk"
        }
        mapped_action = action_map.get(route_type, route_type)
        self.get_argument = lambda k, d=None: mapped_action if k == "action" else tornado.web.RequestHandler.get_argument(self, k, d)
        super().post()

def periodic_memory_cleanup():
    gc.collect()

def make_app():
    handlers = [
        (r"/scans/(.*)", StaticFileHandler, {"path": SCANS_PATH}),
        (r"/static/(.*)", StaticFileHandler, {"path": STATIC_PATH}),
        (r"/(favicon\.ico)", StaticFileHandler, {"path": STATIC_PATH}),
        (r"/()", StaticFileHandler, {"path": STATIC_PATH, "default_filename": "index.html"}),

        (r"/api/print", PrintHandler),
        (r"/api/idcard", IDCardHandler),
        (r"/api/preview/enhance", EnhancePreviewHandler),
        (r"/api/preview_enhance", EnhancePreviewHandler),
        (r"/enhance_preview", EnhancePreviewHandler),

        (r"/api/devices", DevicesHandler),
        (r"/api/printers", DevicesHandler),
        (r"/api/printer_admin", PrinterAdminHandler),
        (r"/api/devices/(discover)", SystemAdminRouteAdapter),
        (r"/api/drivers/(search)", SystemAdminRouteAdapter),
        (r"/api/system/(reset_usb)", SystemAdminRouteAdapter),
        (r"/api/system/(clean_tmp)", SystemAdminRouteAdapter),
        (r"/api/jobs/(clear)", SystemAdminRouteAdapter),

        (r"/api/mail_config", MailConfigHandler),
        (r"/api/mail/config", MailConfigHandler),

        (r"/api/scan/devices", ScanProbeHandler),
        (r"/api/scan", ScanHandler),
        (r"/api/scan/download", DownloadScanHandler),
        (r"/api/scan/preview", PreviewScanHandler),
        (r"/api/scan/print", ScanPrintHandler),
        (r"/api/scan/list", ScanListHandler),
        (r"/api/scan/delete", ScanDeleteHandler),
    ]

    return tornado.web.Application(handlers, debug=False, autoreload=False)

if __name__ == "__main__":
    app = make_app()
    server = tornado.httpserver.HTTPServer(
        app,
        max_buffer_size=20971520,
        idle_connection_timeout=25
    )
    server.listen(8088, address="0.0.0.0")
    print(">>> [Web] CUPS 智能工作台已在 8088 端口启动 (0.0.0.0:8088)", flush=True)

    tornado.ioloop.PeriodicCallback(periodic_memory_cleanup, 60000).start()
    tornado.ioloop.IOLoop.current().start()
