#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HANDLERS_DIR = os.path.join(BASE_DIR, "handlers")
for p in [BASE_DIR, HANDLERS_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import tornado.ioloop
import tornado.web

# 导入与各个 handler 文件完全匹配的真实类名
from handlers.scan_handler import DevicesHandler, DoScanHandler
from handlers.file_handler import ScanListHandler, ScanDeleteHandler
from handlers.print_handler import PrintHandler
from handlers.mail_handler import MailConfigHandler

STATIC_DIR = os.path.join(BASE_DIR, "static")

def make_app():
    return tornado.web.Application([
        (r"/", tornado.web.RedirectHandler, {"url": "/index.html"}),
        (r"/api/devices", DevicesHandler),
        (r"/api/print", PrintHandler),
        (r"/api/do_scan", DoScanHandler),
        (r"/api/scans", ScanListHandler),
        (r"/api/delete_scan", ScanDeleteHandler),
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
