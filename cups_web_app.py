#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import subprocess
import tornado.ioloop
import tornado.web

UPLOAD_DIR = "/tmp/cups_web_uploads"
SCAN_DIR = os.getenv("SCAN_DIR", "/scans")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)

HTML_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>家庭智能打印与扫描控制台</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 20px; background: #f4f6f9; color: #333; }
        .card { background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
        h2 { margin-top: 0; color: #1a73e8; font-size: 18px; }
        .btn { display: inline-block; width: 100%; box-sizing: border-box; background: #1a73e8; color: white; border: none; padding: 12px; border-radius: 8px; font-size: 16px; cursor: pointer; text-align: center; text-decoration: none; margin-top: 10px; font-weight: bold; }
        .btn-green { background: #34a853; }
        .btn-gray { background: #5f6368; }
        input[type="file"] { width: 100%; margin: 10px 0; }
        .footer { text-align: center; font-size: 13px; color: #888; margin-top: 30px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>📄 手机/电脑极速打印</h2>
        <form action="/upload" method="post" enctype="multipart/form-data">
            <input type="file" name="file" required>
            <button type="submit" class="btn">立即提交打印</button>
        </form>
    </div>

    <div class="card">
        <h2>🖨️ 扫描仪一键扫描</h2>
        <form action="/scan" method="post">
            <button type="submit" class="btn btn-green">启动扫描并保存 A4 PDF</button>
        </form>
        <a href="/scans/" class="btn btn-gray">查看/下载历史扫描件</a>
    </div>

    <div class="card">
        <h2>⚙️ 底层后台快捷入口</h2>
        <a href="http://" + window.location.hostname + ":631" class="btn btn-gray" target="_blank">进入 CUPS 631 汉化管理后台</a>
    </div>

    <div class="footer">
        HiNAS & 智能打印系统 · 极速出纸模式已就绪
    </div>
</body>
</html>
"""

class MainHandler(tornado.web.RequestHandler):
    def get(self):
        self.write(HTML_PAGE)

class UploadHandler(tornado.web.RequestHandler):
    def post(self):
        if 'file' not in self.request.files:
            self.write("<script>alert('未选择文件！');history.back();</script>")
            return

        file_obj = self.request.files['file'][0]
        filename = file_obj['filename']
        filepath = os.path.join(UPLOAD_DIR, filename)

        with open(filepath, 'wb') as f:
            f.write(file_obj['body'])

        # 调取 lp 打印
        cmd = ["lp", "-o", "media=A4", "-o", "fit-to-page", "-o", "Resolution=300dpi", filepath]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.write(f"<script>alert('文件 [{filename}] 已成功提交至打印队列！');location.href='/';</script>")

class ScanHandler(tornado.web.RequestHandler):
    def post(self):
        timestamp = int(time.time())
        pdf_path = os.path.join(SCAN_DIR, f"scan_{timestamp}.pdf")
        
        # 调取 scanimage 输出 PDF
        cmd = f"scanimage --format=pdf --resolution 300 > {pdf_path}"
        ret = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if ret.returncode == 0 and os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            self.write(f"<script>alert('扫描完成！文件已保存');location.href='/scans/';</script>")
        else:
            self.write("<script>alert('未检测到可用扫描仪或扫描失败，请检查 USB 连线！');location.href='/';</script>")

def make_app():
    return tornado.web.Application([
        (r"/", MainHandler),
        (r"/upload", UploadHandler),
        (r"/scan", ScanHandler),
        (r"/scans/(.*)", tornado.web.StaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(8000)
    print(" [Web App] 快速打印/扫描 Web 服务已监听 8000 端口...", flush=True)
    tornado.ioloop.IOLoop.current().start()
