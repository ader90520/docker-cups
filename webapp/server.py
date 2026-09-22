#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

# 1. 绝对路径优先注入
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
HANDLERS_DIR = os.path.join(CURRENT_DIR, "handlers")

for path in [CURRENT_DIR, HANDLERS_DIR]:
    if path not in sys.path:
        sys.path.insert(0, path)

import tornado.ioloop
import tornado.web

# 2. 兼容导入方式（包导入或单模块直导）
try:
    from handlers.device_handler import DeviceHandler
except ImportError:
    from device_handler import DeviceHandler

try:
    try:
        from handlers.print_handler import PrintUploadHandler as PrintHandlerCls
    except ImportError:
        from handlers.print_handler import PrintHandler as PrintHandlerCls
except ImportError:
    try:
        from print_handler import PrintUploadHandler as PrintHandlerCls
    except ImportError:
        from print_handler import PrintHandler as PrintHandlerCls

try:
    from handlers.scan_handler import ScanHandler, ScanFileHandler, DeleteScanHandler
except ImportError:
    from scan_handler import ScanHandler, ScanFileHandler, DeleteScanHandler

try:
    from handlers.mail_handler import MailConfigHandler
except ImportError:
    from mail_handler import MailConfigHandler

STATIC_DIR = os.path.join(CURRENT_DIR, "static")

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
