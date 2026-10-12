#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import glob
import json
import time
import tornado.ioloop
import tornado.web
import tornado.httpserver
from tornado.web import StaticFileHandler

# 强制将当前脚本所在目录注入 Python 寻包路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 导入业务模块 (保留原代码所有 handler 导入，未删动一行)
from handlers.print_handler import PrintHandler
from handlers.idcard_handler import IDCardHandler
from handlers.invoice_handler import InvoiceHandler
from handlers.device_handler import DevicesHandler
from handlers.printer_admin_handler import PrinterAdminHandler
from handlers.mail_handler import MailConfigHandler
from handlers.scan_handler import (
    ScanProbeHandler,
    ScanHandler,
    DownloadScanHandler,
    PreviewScanHandler,
    ScanPrintHandler
)
from handlers.enhance_handler import EnhancePreviewHandler

STATIC_PATH = os.path.join(BASE_DIR, "static")
MODELS_PATH = os.path.join(BASE_DIR, "models")
SCANS_PATH = "/scans"

os.makedirs(SCANS_PATH, exist_ok=True)
os.makedirs(STATIC_PATH, exist_ok=True)
os.makedirs(MODELS_PATH, exist_ok=True)

class ScanListHandler(tornado.web.RequestHandler):
    def get(self):
        files = []
        scan_files = sorted(glob.glob(os.path.join(SCANS_PATH, "*.*")), key=os.path.getmtime, reverse=True)
        for f in scan_files:
            fname = os.path.basename(f)
            files.append({
                "filename": fname,
                "url": f"/scans/{fname}",
                "download_url": f"/api/scan/download?file={fname}",
                "size": f"{round(os.path.getsize(f) / 1024, 1)} KB",
                "is_pdf": fname.lower().endswith(".pdf"),
                "mtime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(f)))
            })
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.write(json.dumps({"success": True, "files": files}))

class ScanDeleteHandler(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            fname = os.path.basename(data.get("filename", ""))
            target = os.path.join(SCANS_PATH, fname)
            if fname and os.path.exists(target):
                os.remove(target)
                self.write(json.dumps({"success": True, "msg": f"文件 {fname} 已删除"}))
            else:
                self.set_status(404)
                self.write(json.dumps({"success": False, "msg": "文件不存在"}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))

class ModelUploadHandler(tornado.web.RequestHandler):
    """支持端侧 ONNX 模型的在线热上传与持久化存储"""
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            # 兼容 model_file 与 model 两种上传键名
            files = self.request.files.get("model_file", []) or self.request.files.get("model", [])
            if not files:
                self.set_status(400)
                self.write(json.dumps({"success": False, "msg": "未接收到模型文件"}))
                return

            upload_file = files[0]
            filename = upload_file["filename"]
            if not filename.lower().endswith(".onnx"):
                self.set_status(400)
                self.write(json.dumps({"success": False, "msg": "仅支持上传 .onnx 格式的模型文件"}))
                return

            target_path = os.path.join(MODELS_PATH, filename)
            with open(target_path, "wb") as f:
                f.write(upload_file["body"])

            # 兼容默认切边模型命名
            default_corner_path = os.path.join(MODELS_PATH, "doc_corner.onnx")
            if "corner" in filename.lower() or not os.path.exists(default_corner_path):
                with open(default_corner_path, "wb") as f:
                    f.write(upload_file["body"])

            self.write(json.dumps({
                "success": True,
                "msg": f"模型 {filename} 已成功上传并部署至 /models/{filename}"
            }))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))

class ModelManagerHandler(tornado.web.RequestHandler):
    """已部署模型列表查询与单键物理移除管理接口"""
    def set_default_headers(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, DELETE, OPTIONS")

    def options(self):
        self.set_status(204)
        self.finish()

    def get(self):
        """枚举 MODELS_PATH 下的所有 .onnx 模型"""
        models = []
        try:
            if os.path.exists(MODELS_PATH):
                for fname in sorted(os.listdir(MODELS_PATH)):
                    if fname.lower().endswith('.onnx'):
                        fpath = os.path.join(MODELS_PATH, fname)
                        size_mb = round(os.path.getsize(fpath) / (1024 * 1024), 2)
                        models.append({
                            "name": fname,
                            "size": f"{size_mb} MB"
                        })
            self.write(json.dumps({"success": True, "models": models}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))

    def delete(self):
        """根据名称物理删除指定的 .onnx 模型文件"""
        name = self.get_argument("name", "").strip()
        # 安全防御：杜绝路径穿越与非 onnx 文件误删
        if not name or "/" in name or "\\" in name or not name.lower().endswith('.onnx'):
            self.set_status(400)
            self.write(json.dumps({"success": False, "msg": "非法模型文件名"}))
            return

        target = os.path.join(MODELS_PATH, name)
        if os.path.exists(target):
            try:
                os.remove(target)
                self.write(json.dumps({"success": True, "msg": f"模型 {name} 已彻底删除"}))
            except Exception as e:
                self.set_status(500)
                self.write(json.dumps({"success": False, "msg": f"删除失败: {str(e)}"}))
        else:
            self.set_status(404)
            self.write(json.dumps({"success": False, "msg": "模型文件不存在"}))

def make_app():
    handlers = [
        # 1. 静态资源与首页映射
        (r"/scans/(.*)", StaticFileHandler, {"path": SCANS_PATH}),
        (r"/static/(.*)", StaticFileHandler, {"path": STATIC_PATH}),
        (r"/models/(.*)", StaticFileHandler, {"path": MODELS_PATH}),
        (r"/(favicon\.ico)", StaticFileHandler, {"path": STATIC_PATH}),
        (r"/()", StaticFileHandler, {"path": STATIC_PATH, "default_filename": "index.html"}),

        # 2. 核心业务打印与图像算法接口
        (r"/api/print", PrintHandler),
        (r"/api/idcard", IDCardHandler),
        (r"/api/invoice", InvoiceHandler),
        (r"/api/devices", DevicesHandler),
        (r"/api/printer_admin", PrinterAdminHandler),
        (r"/api/mail_config", MailConfigHandler),
        (r"/api/preview/enhance", EnhancePreviewHandler),
        (r"/api/preview_enhance", EnhancePreviewHandler),
        (r"/enhance_preview", EnhancePreviewHandler),
        (r"/api/model/upload", ModelUploadHandler),
        (r"/api/models", ModelManagerHandler),  # 挂载模型列表与删除接口

        # 3. 扫描仪接口
        (r"/api/scan/devices", ScanProbeHandler),
        (r"/api/scan", ScanHandler),
        (r"/api/scan/download", DownloadScanHandler),
        (r"/api/scan/preview", PreviewScanHandler),
        (r"/api/scan/print", ScanPrintHandler),
        (r"/api/scan/list", ScanListHandler),
        (r"/api/scan/delete", ScanDeleteHandler),
    ]

    settings = {
        "debug": False,
        "autoreload": False
    }

    return tornado.web.Application(handlers, **settings)

if __name__ == "__main__":
    app = make_app()
    server = tornado.httpserver.HTTPServer(app, max_buffer_size=104857600)
    server.listen(8088, address="0.0.0.0")
    print(">>> CUPS 智能工作台已在 8088 端口正常启动 (Listen: 0.0.0.0:8088)", flush=True)
    print(f">>> 静态模型目录已映射至: {MODELS_PATH} (路由: /models/*)", flush=True)
    tornado.ioloop.IOLoop.current().start()
