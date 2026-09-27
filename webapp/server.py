#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import tornado.ioloop
import tornado.web

# 导入所有处理器
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import PrintHandler
from handlers.scan_handler import ScanHandler, ScanProbeHandler, DownloadScanHandler
from handlers.printer_admin_handler import PrinterAdminHandler
from handlers.mail_handler import MailConfigHandler

class DevicesHandler(BaseHandler):
    """获取系统已安装的 CUPS 打印机列表"""
    def get(self):
        try:
            import subprocess
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            # 探测默认打印机
            def_res = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, text=True, env=env)
            default_printer = ""
            for line in def_res.stdout.splitlines():
                if "destination:" in line:
                    default_printer = line.split("destination:")[-1].strip()

            # 探测所有已就绪打印机状态
            p_res = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, text=True, env=env)
            printers = []
            for line in p_res.stdout.splitlines():
                if line.startswith("printer"):
                    parts = line.split()
                    p_name = parts[1]
                    status = "idle"
                    if "now printing" in line:
                        status = "printing"
                    elif "disabled" in line:
                        status = "disabled"
                    
                    has_error = ("offline" in line.lower() or "error" in line.lower())
                    printers.append({
                        "name": p_name,
                        "status": status,
                        "is_default": (p_name == default_printer),
                        "has_error": has_error
                    })

            self.write_json(True, data={"printers": printers})
        except Exception as e:
            self.write_json(False, f"获取打印机异常: {str(e)}")

def make_app():
    static_path = os.path.join(os.path.dirname(__file__), "static")
    return tornado.web.Application([
        # 页面入口
        (r"/", tornado.web.RedirectHandler, {"url": "/index.html"}),
        (r"/(index\.html)", tornado.web.StaticFileHandler, {"path": static_path}),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": static_path}),

        # 核心业务 API
        (r"/api/devices", DevicesHandler),
        (r"/api/print", PrintHandler),
        (r"/api/print/idcard", PrintHandler),
        (r"/api/print/invoice", PrintHandler),
        
        # 扫描仪专属 API (包含 Issue #111 预探测与下载)
        (r"/api/scan", ScanHandler),
        (r"/api/scan/devices", ScanHandler),
        (r"/api/scan/probe", ScanProbeHandler),
        (r"/download/scan/(.*)", DownloadScanHandler),

        # 打印机驱动与物理端口管理
        (r"/api/printer_admin", PrinterAdminHandler),

        # 云邮件策略配置
        (r"/api/mail_config", MailConfigHandler),
    ], template_path=static_path, static_path=static_path, debug=False)

if __name__ == "__main__":
    app = make_app()
    app.listen(8088, address="0.0.0.0")
    print(">>> [Web] 8088 综合控制台服务启动成功！监听 0.0.0.0:8088")
    tornado.ioloop.IOLoop.current().start()
