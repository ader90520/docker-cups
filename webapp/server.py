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
    """
    容错加载器：
    如果指定模块文件缺失、语法报错或类不存在，返回一个友好的降级 Handler，
    保证 8088 核心服务永远不崩！
    """
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
            def get(self, *args, **kwargs):
                self.write({"success": False, "msg": f"{fallback_msg}: {str(e)}", "error_module": module_name})
            def post(self, *args, **kwargs):
                self.write({"success": False, "msg": f"{fallback_msg}: {str(e)}", "error_module": module_name})
        return FaultFallbackHandler

# 1. 安全解耦导入每个模块
DeviceHandler = safe_import("device_handler", "DeviceHandler", "设备探测服务异常")
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
        
        # 设备状态接口
        (r"/api/devices", DeviceHandler),
        
        # 打印系列独立路由（发票与身份证互不干扰）
        (r"/api/print/invoice", InvoiceHandler),
        (r"/api/print_invoice", InvoiceHandler),
        (r"/api/print/idcard", IdCardHandler),
        (r"/api/print_idcard", IdCardHandler),
        (r"/api/print", PrintHandler),
        
        # 扫描与文件路由
        (r"/api/do_scan", ScanHandler),
        (r"/api/scans", ScanListHandler),
        (r"/api/delete_scan", ScanDeleteHandler),
        
        # 邮件路由
        (r"/api/mail_config", MailConfigHandler),
        
        # 静态 Web 资源托管 (放末尾)
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
