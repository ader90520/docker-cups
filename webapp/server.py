#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import tornado.ioloop
import tornado.web
from tornado.web import StaticFileHandler

from handlers.print_handler import PrintHandler
from handlers.idcard_handler import IDCardHandler
from handlers.device_handler import DevicesHandler
from handlers.printer_admin_handler import PrinterAdminHandler
from handlers.mail_config_handler import MailConfigHandler
from handlers.scan_handler import ScanProbeHandler, ScanHandler, DownloadScanHandler, PreviewScanHandler

STATIC_PATH = "/opt/webapp/static"
SCANS_PATH = "/scans"
os.makedirs(SCANS_PATH, exist_ok=True)

def make_app():
    handlers = [
        # 静态资源与扫描结果映射
        (r"/scans/(.*)", StaticFileHandler, {"path": SCANS_PATH}),
        (r"/static/(.*)", StaticFileHandler, {"path": STATIC_PATH}),
        (r"/", StaticFileHandler, {"path": STATIC_PATH, "default_filename": "index.html"}),

        # 核心业务接口
        (r"/api/print", PrintHandler),
        (r"/api/idcard", IDCardHandler),
        (r"/api/devices", DevicesHandler),
        (r"/api/printer_admin", PrinterAdminHandler),
        (r"/api/mail_config", MailConfigHandler),

        # 扫描仪接口
        (r"/api/scan/devices", ScanProbeHandler),
        (r"/api/scan", ScanHandler),
        (r"/api/scan/download", DownloadScanHandler),
        (r"/api/scan/preview", PreviewScanHandler),
    ]

    settings = {
        "debug": False,
        "autoreload": False
    }

    return tornado.web.Application(handlers, **settings)

if __name__ == "__main__":
    app = make_app()
    app.listen(8088, address="0.0.0.0")
    print("CUPS 智能工作台已在 8088 端口正常启动", flush=True)
    tornado.ioloop.IOLoop.current().start()
