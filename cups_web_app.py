#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import subprocess
import tornado.ioloop
import tornado.web

PORT = int(os.getenv("WEB_PORT", 8000))
UPLOAD_DIR = "/tmp/cups_web_uploads"
SCAN_DIR = os.getenv("SCAN_DIR", "/scans")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)

HTML_INDEX = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>云打印与扫描控制台</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; max-width: 650px; margin: 20px auto; padding: 15px; }
        .card { border: 1px solid #ddd; border-radius: 8px; padding: 18px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 20px; }
        h2 { margin-top: 0; color: #222; font-size: 1.15rem; }
        button, input[type="file"], input[type="submit"] { display: block; width: 100%; margin: 10px 0; padding: 10px; font-size: 0.95rem; box-sizing: border-box; }
        button, input[type="submit"] { background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; }
        button.scan-btn { background: #28a745; }
        .msg { padding: 10px; border-radius: 4px; background: #eef; margin-top: 10px; font-size: 0.85rem; word-break: break-all; }
        pre { margin: 0; white-space: pre-wrap; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🖨️ 打印机设备状态</h2>
        <div class="msg"><pre>{{ printer_status }}</pre></div>
    </div>

    <div class="card">
        <h2>📄 局域网快速打印 (支持 PDF / Word / 图片)</h2>
        <form method="post" action="/upload" enctype="multipart/form-data">
            <input type="file" name="file" required>
            <input type="submit" value="立即极速打印">
        </form>
        {% if print_result %}
        <div class="msg">{{ print_result }}</div>
        {% end %}
    </div>

    <div class="card">
        <h2>📡 平板扫描仪一键扫描</h2>
        <form method="post" action="/scan">
            <button type="submit" class="scan-btn">📷 立即触发扫描 (生成 A4 PDF)</button>
        </form>
        {% if scan_download_link %}
        <div class="msg">扫描完成！👉 <a href="{{ scan_download_link }}" target="_blank">点击下载扫描件 PDF</a></div>
        {% end %}
    </div>
</body>
</html>
"""

def get_cups_status():
    try:
        res = subprocess.run(["lpstat", "-p", "-d"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        return res.stdout.strip() or "暂未安装打印机（可通过 631 后台添加）"
    except Exception as e:
        return f"状态探测异常: {e}"

def convert_to_pdf_if_office(filepath, ext):
    if ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]:
        out_dir = os.path.dirname(filepath)
        base = os.path.splitext(os.path.basename(filepath))[0]
        cmd = ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", out_dir, filepath]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        target = os.path.join(out_dir, f"{base}.pdf")
        if os.path.exists(target):
            return target
    return filepath

def execute_fast_print(filepath, filename):
    try:
        res = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, text=True)
        printer_name = res.stdout.split("destination: ")[-1].strip() if "destination: " in res.stdout else None
        if not printer_name:
            res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, text=True)
            for line in res_p.stdout.splitlines():
                if line.startswith("printer "):
                    printer_name = line.split()[1].strip()
                    break

        if not printer_name:
            return False, "未找到可用打印机，请先在 631 端口后台配置打印机。"

        ext = os.path.splitext(filename)[1].lower()
        actual_print_file = convert_to_pdf_if_office(filepath, ext)

        cmd = [
            "lp", "-d", printer_name,
            "-o", "media=A4", "-o", "fit-to-page",
            "-o", "Resolution=300dpi", "-o", "pdftops-renderer=pdftocairo",
            "-o", "ColorModel=Gray", "-o", "job-sheets=none",
            actual_print_file
        ]
        sub = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if sub.returncode == 0:
            return True, f"出纸任务已提交至 [{printer_name}]: {sub.stdout.strip()}"
        return False, f"底层拒绝提交: {sub.stderr.strip()}"
    except Exception as e:
        return False, f"打印异常: {e}"

class MainHandler(tornado.web.RequestHandler):
    def get(self):
        self.render_page("", "")

    def render_page(self, print_result="", scan_link=""):
        status = get_cups_status()
        tmpl = tornado.template.Template(HTML_INDEX)
        self.finish(tmpl.generate(printer_status=status, print_result=print_result, scan_download_link=scan_link))

class UploadHandler(tornado.web.RequestHandler):
    def post(self):
        f = self.request.files.get("file")
        if not f:
            self.redirect("/")
            return
        upload_obj = f[0]
        fname = upload_obj["filename"]
        fpath = os.path.join(UPLOAD_DIR, fname)
        with open(fpath, "wb") as up:
            up.write(upload_obj["body"])

        _, msg = execute_fast_print(fpath, fname)
        if os.path.exists(fpath):
            try: os.remove(fpath)
            except Exception: pass

        self.render_page(print_result=msg)

    def render_page(self, print_result):
        status = get_cups_status()
        tmpl = tornado.template.Template(HTML_INDEX)
        self.finish(tmpl.generate(printer_status=status, print_result=print_result, scan_download_link=""))

class ScanHandler(tornado.web.RequestHandler):
    def post(self):
        timestamp = int(time.time())
        png_path = os.path.join(SCAN_DIR, f"scan_{timestamp}.png")
        pdf_name = f"scan_{timestamp}.pdf"
        pdf_path = os.path.join(SCAN_DIR, pdf_name)

        scan_cmd = ["scanimage", "--resolution=300", "--mode=Gray", "--format=png", f"--output-file={png_path}"]
        res = subprocess.run(scan_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)

        link = ""
        if res.returncode == 0 and os.path.exists(png_path):
            gs_cmd = ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4", "-dPDFSETTINGS=/ebook", f"-sOutputFile={pdf_path}", png_path]
            subprocess.run(gs_cmd, timeout=30)
            if os.path.exists(png_path):
                os.remove(png_path)
            link = f"/download/{pdf_name}"

        status = get_cups_status()
        tmpl = tornado.template.Template(HTML_INDEX)
        self.finish(tmpl.generate(printer_status=status, print_result="扫描完成" if link else f"扫描未成功: {res.stderr}", scan_download_link=link))

def make_app():
    return tornado.web.Application([
        (r"/", MainHandler),
        (r"/upload", UploadHandler),
        (r"/scan", ScanHandler),
        (r"/download/(.*)", tornado.web.StaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(PORT)
    print(f" [Cups-Web Online] 网页打印及扫描服务运行于端口 {PORT}", flush=True)
    tornado.ioloop.IOLoop.current().start()
