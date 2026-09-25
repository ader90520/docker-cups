#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tornado.ioloop
import tornado.web
import os
import sys

from handlers.device_handler import DeviceHandler
from handlers.print_handler import PrintHandler
from handlers.idcard_handler import IdCardHandler
from handlers.invoice_handler import InvoiceHandler
from handlers.printer_admin_handler import PrinterAdminHandler
from handlers.mail_handler import MailConfigHandler
from handlers.scan_handler import ScanHandler, DownloadScanHandler

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

def make_app():
    return tornado.web.Application([
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
    ], debug=False)

if __name__ == "__main__":
    app = make_app()
    app.listen(8088, address="0.0.0.0")
    print(">>> [Web] 8088 打印与多功能扫描控制台已启动...")
    tornado.ioloop.IOLoop.current().start()
