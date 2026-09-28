#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import subprocess
import tornado.ioloop
import tornado.web

from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import PrintHandler
from handlers.scan_handler import ScanHandler, ScanProbeHandler, DownloadScanHandler
from handlers.printer_admin_handler import PrinterAdminHandler
from handlers.mail_handler import MailConfigHandler

class IndexRedirectHandler(tornado.web.RequestHandler):
    """同时支持 HEAD 和 GET 请求，避免探活报 405 Method Not Allowed"""
    def head(self):
        self.redirect("/index.html")

    def get(self):
        self.redirect("/index.html")

class DevicesHandler(BaseHandler):
    """获取系统已安装的 CUPS 打印机列表，实现 8088 与 631 实时互通"""
    def get(self):
        printers_list = []
        default_printer = ""
        try:
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"

            # 1. 优先通过 lpstat -a 提取队列名（第一列固定为打印机名称，兼容中英文输出）
            res_a = subprocess.run(["lpstat", "-a"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
            for line in res_a.stdout.splitlines():
                parts = line.strip().split()
                if parts:
                    p = parts[0].strip()
                    if p and p not in printers_list:
                        printers_list.append(p)

            # 2. 如果 -a 为空，使用 lpstat -p 兜底匹配第二列
            if not printers_list:
                res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
                for line in res_p.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        p = parts[1].strip()
                        if p and p not in printers_list:
                            printers_list.append(p)

            # 3. 提取默认打印机名（兼容中文全角冒号与英文半角冒号）
            res_d = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
            for line in res_d.stdout.splitlines():
                if "：" in line:
                    default_printer = line.split("：")[-1].strip()
                elif ":" in line:
                    default_printer = line.split(":")[-1].strip()

            if not default_printer and printers_list:
                default_printer = printers_list[0]

        except Exception as e:
            print(f"[DevicesHandler] 设备提取异常: {e}", flush=True)

        # 严格对齐前端所需的字典结构：p.name, p.status, p.is_default
        devices = []
        for p in printers_list:
            devices.append({
                "name": p,
                "status": "就绪",
                "is_default": (p == default_printer),
                "has_error": False
            })

        self.write_json(True, "", data={
            "printers": devices,
            "default": default_printer
        })

def make_app():
    static_path = os.path.join(os.path.dirname(__file__), "static")
    return tornado.web.Application([
        (r"/", IndexRedirectHandler),
        (r"/api/devices", DevicesHandler),
        (r"/api/print", PrintHandler),
        (r"/api/scan", ScanHandler),
        (r"/api/scan/probe", ScanProbeHandler),
        (r"/api/scan/download", DownloadScanHandler),
        (r"/api/printer_admin", PrinterAdminHandler),
        (r"/api/mail/config", MailConfigHandler),
        (r"/(.*)", tornado.web.StaticFileHandler, {"path": static_path, "default_filename": "index.html"}),
    ],
    autoreload=False,
    debug=False)

if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.system("fuser -k 8088/tcp 2>/dev/null || true")
    app = make_app()
    app.listen(8088, address="0.0.0.0")
    print("[Server] CUPS Web 服务已平稳启动，监听端口 8088...", flush=True)
    tornado.ioloop.IOLoop.current().start()
