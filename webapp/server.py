#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

# 优先注册运行目录和 handlers 目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HANDLERS_DIR = os.path.join(BASE_DIR, "handlers")
for p in [BASE_DIR, HANDLERS_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import tornado.ioloop
import tornado.web

from handlers.device_handler import DeviceHandler
from handlers.scan_handler import ScanHandler, ScanFileHandler, DeleteScanHandler
from handlers.mail_handler import MailConfigHandler

# 兼容不同的打印上传类命名
try:
    from handlers.print_handler import PrintUploadHandler as PrintHandlerCls
except ImportError:
    from handlers.print_handler import PrintHandler as PrintHandlerCls

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
    print(">>> [Web] 8088 综合控制台服务已启动 (0.0.0.0:8088)", flush=True)
    tornado.ioloop.IOLoop.current().start()
