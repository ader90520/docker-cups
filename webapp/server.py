#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import tornado.ioloop
import tornado.web
import tornado.httpserver

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from handlers.device_handler import DeviceHandler
from handlers.print_handler import PrintHandler
from handlers.idcard_handler import IdCardHandler
from handlers.invoice_handler import InvoiceHandler
from handlers.printer_admin_handler import PrinterAdminHandler
from handlers.mail_handler import MailConfigHandler
from handlers.scan_handler import ScanHandler, DownloadScanHandler

STATIC_DIR = os.path.join(CURRENT_DIR, "static")

def make_app():
    handlers = [
        (r"/", tornado.web.RedirectHandler, {"url": "/index.html"}),
        (r"/api/devices", DeviceHandler),
        (r"/api/print", PrintHandler),
        (r"/api/print/idcard", IdCardHandler),
        (r"/api/print/invoice", InvoiceHandler),
        (r"/api/printer_admin", PrinterAdminHandler),
        (r"/api/mail_config", MailConfigHandler),
        (r"/api/scan", ScanHandler),
        (r"/api/scan/devices", ScanHandler),
        (r"/download/scan/(.*)", DownloadScanHandler),
        (r"/(.*)", tornado.web.StaticFileHandler, {"path": STATIC_DIR, "default_filename": "index.html"}),
    ]
    return tornado.web.Application(handlers, debug=False)

if __name__ == "__main__":
    try:
        app = make_app()
        server = tornado.httpserver.HTTPServer(app)
        server.listen(8088, address="0.0.0.0")
        print(">>> [Web] 8088 综合控制台服务启动成功！监听 0.0.0.0:8088")
        tornado.ioloop.IOLoop.current().start()
    except Exception as e:
        print(f">>> [Web] 启动异常: {e}")
        sys.exit(1)
