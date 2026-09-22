#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

# 关键修复：确保 Python 能够正确识别当前目录及子包 handlers
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import tornado.ioloop
import tornado.web

from handlers.device_handler import DeviceHandler
try:
    from handlers.print_handler import PrintUploadHandler as PrintHandlerCls
except ImportError:
    from handlers.print_handler import PrintHandler as PrintHandlerCls

from handlers.scan_handler import ScanHandler, ScanFileHandler, DeleteScanHandler
from handlers.mail_handler import MailConfigHandler

STATIC_DIR = os.path.join(BASE_DIR, "static")

def make_app():
    return tornado.web.Application([
        (r"/", tornado.web.RedirectHandler, {"url": "/index.html"}),
        (r"/api/devices", DeviceHandler),
        (r"/api/print", PrintHandlerCls),
        (r"/api/do_scan", ScanHandler),
        (r"/api/scans", ScanFileHandler),
        (r"/api/delete_scan", DeleteScanHandler),
        (r"/api/mail_config", MailConfigHandler),
        (r"/(.*)", tornado.web.StaticFileHandler, {"path": STATIC_DIR, "default_filename": "index.html"}),
    ],
    autoreload=False,
    debug=False
    )

if __name__ == "__main__":
    app = make_app()
    app.listen(8088, address="0.0.0.0")
    print(">>> 8088 Web 综合控制台已监听在 0.0.0.0:8088")
    tornado.ioloop.IOLoop.current().start()
