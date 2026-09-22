#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import tornado.ioloop
import tornado.web

# 导入同级 handlers 模块
sys.path.append(os.path.dirname(__file__))
from handlers.print_handler import PrintUploadHandler
from handlers.scan_handler import DevicesHandler, DoScanHandler
from handlers.file_handler import ScanListHandler, ScanDeleteHandler
from handlers.mail_handler import MailConfigHandler

SCAN_DIR = "/scans"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

class MainHandler(tornado.web.RequestHandler):
    def get(self):
        with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
            self.write(f.read())

def make_app():
    return tornado.web.Application([
        (r"/", MainHandler),
        (r"/api/devices", DevicesHandler),
        (r"/api/print", PrintUploadHandler),
        (r"/api/do_scan", DoScanHandler),
        (r"/api/scans", ScanListHandler),
        (r"/api/delete_scan", ScanDeleteHandler),
        (r"/api/mail_config", MailConfigHandler),
        (r"/scans/(.*)", tornado.web.StaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(8088, address="0.0.0.0")
    tornado.ioloop.IOLoop.current().start()
