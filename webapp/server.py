#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import traceback
import tornado.ioloop
import tornado.web

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HANDLERS_DIR = os.path.join(BASE_DIR, "handlers")
for p in [BASE_DIR, HANDLERS_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

def safe_import(module_name, class_name, fallback_msg="模块暂时不可用"):
    """安全隔离导入机制：单个模块故障绝不影响全局服务"""
    try:
        mod = __import__(f"handlers.{module_name}", fromlist=[class_name])
        cls = getattr(mod, class_name)
        logging.info(f"✔ 成功加载模块: {module_name}.{class_name}")
        return cls
    except Exception as e:
        logging.error(f"✖ 模块 [{module_name}.{class_name}] 加载失败: {e}")
        traceback.print_exc()

        class FaultFallbackHandler(tornado.web.RequestHandler):
            def set_default_headers(self):
                self.set_header("Content-Type", "application/json; charset=UTF-8")
                self.set_header("Access-Control-Allow-Origin", "*")
            def get(self, *args, **kwargs):
                self.write({"success": False, "msg": f"{fallback_msg}: {str(e)}", "error_module": module_name})
            def post(self, *args, **kwargs):
                self.write({"success": False, "msg": f"{fallback_msg}: {str(e)}", "error_module": module_name})
        return FaultFallbackHandler

# 动态容错挂载全量模块
DeviceHandler = safe_import("device_handler", "DeviceHandler", "设备探测服务异常")
PrinterAdminHandler = safe_import("printer_admin_handler", "PrinterAdminHandler", "打印机管理服务异常")
InvoiceHandler = safe_import("invoice_handler", "InvoicePrintHandler", "发票打印功能未就绪")
IdCardHandler = safe_import("idcard_handler", "IdCardPrintHandler", "身份证打印功能未就绪")
PrintHandler = safe_import("print_handler", "PrintHandler", "通用打印服务异常")
ScanHandler = safe_import("scan_handler", "DoScanHandler", "扫描驱动服务异常")
ScanListHandler = safe_import("file_handler", "ScanListHandler", "扫描件列表获取异常")
ScanDeleteHandler = safe_import("file_handler", "ScanDeleteHandler", "文件删除服务异常")
MailConfigHandler = safe_import("mail_handler", "MailConfigHandler", "邮件配置服务异常")

STATIC_DIR = os.path.join(BASE_DIR, "static")

def make_app():
    return tornado.web.Application([
        (r"/", tornado.web.RedirectHandler, {"url": "/index.html"}),
        
        # 设备探测与 631 驱动管理同步接口
        (r"/api/devices", DeviceHandler),
        (r"/api/printer_admin", PrinterAdminHandler),
        
        # 打印业务接口
        (r"/api/print/invoice", InvoiceHandler),
        (r"/api/print_invoice", InvoiceHandler),
        (r"/api/print/idcard", IdCardHandler),
        (r"/api/print_idcard", IdCardHandler),
        (r"/api/print", PrintHandler),
        
        # 扫描仪与文件管理接口
        (r"/api/do_scan", ScanHandler),
        (r"/api/scans", ScanListHandler),
        (r"/api/delete_scan", ScanDeleteHandler),
        
        # 云邮箱接口
        (r"/api/mail_config", MailConfigHandler),
        
        # 静态资源托管
        (r"/(.*)", tornado.web.StaticFileHandler, {"path": STATIC_DIR, "default_filename": "index.html"}),
    ],
    autoreload=False,
    debug=False
    )

if __name__ == "__main__":
    app = make_app()
    app.listen(8088, address="0.0.0.0")
    print(">>> [Web] 8088 模块化弹性控制台已启动 (0.0.0.0:8088)", flush=True)
    tornado.ioloop.IOLoop.current().start()
