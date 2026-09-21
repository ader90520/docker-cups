#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import glob
import json
import time
import subprocess
import tornado.ioloop
import tornado.web

SCAN_DIR = "/scans"
UPLOAD_DIR = "/tmp/cups_web_uploads"
os.makedirs(SCAN_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 1. 扫描文件列表 API
class ScanListHandler(tornado.web.RequestHandler):
    def get(self):
        files = []
        scan_files = sorted(
            glob.glob(os.path.join(SCAN_DIR, "*.*")),
            key=os.path.getmtime,
            reverse=True
        )
        for f in scan_files:
            fname = os.path.basename(f)
            size_kb = round(os.path.getsize(f) / 1024, 1)
            ext = os.path.splitext(fname)[1].lower()
            files.append({
                "filename": fname,
                "url": f"/scans/{fname}",
                "size": f"{size_kb} KB",
                "is_img": ext in [".jpg", ".jpeg", ".png", ".tiff", ".tif"],
                "is_pdf": ext == ".pdf",
                "mtime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(f)))
            })
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.write(json.dumps({"success": True, "files": files}))

# 2. 扫描文件删除 API
class ScanDeleteHandler(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            filename = os.path.basename(data.get("filename", ""))
            target_path = os.path.join(SCAN_DIR, filename)
            
            if filename and os.path.exists(target_path):
                os.remove(target_path)
                self.write(json.dumps({"success": True, "msg": f"文件 {filename} 删除成功"}))
            else:
                self.set_status(404)
                self.write(json.dumps({"success": False, "msg": "文件不存在或路径不合法"}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))

# 3. 获取打印机与扫描仪设备列表 API
class DevicesHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        printers = []
        scanners = []
        
        # 获取 CUPS 打印机
        try:
            res = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in res.stdout.splitlines():
                if line.startswith("printer"):
                    p_name = line.split()[1]
                    printers.append(p_name)
        except Exception:
            pass

        # 探测 SANE 扫描仪
        try:
            res = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            for line in res.stdout.splitlines():
                if "device `" in line:
                    dev_id = line.split("`")[1].split("'")[0]
                    desc = line.split("' is a ")[-1] if "' is a " in line else dev_id
                    scanners.append({"id": dev_id, "name": desc})
        except Exception:
            pass

        self.write(json.dumps({"printers": printers, "scanners": scanners}))

# 4. 触发扫描及复印 API
class DoScanHandler(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            device = data.get("device", "").strip()
            mode = data.get("mode", "Color") # Color / Gray / Lineart
            resolution = data.get("resolution", "150")
            fmt = data.get("format", "pdf").lower()
            action = data.get("action", "scan") # scan / copy
            target_printer = data.get("printer", "")

            timestamp = time.strftime('%Y%m%d_%H%M%S')
            raw_tiff = f"/tmp/scan_{timestamp}.tiff"
            out_filename = f"scan_{timestamp}.{fmt}"
            final_path = os.path.join(SCAN_DIR, out_filename)

            # 调用 SANE 原生指令
            cmd = [
                "scanimage",
                "-d", device,
                f"--mode={mode}",
                f"--resolution={resolution}",
                "--format=tiff"
            ]
            
            with open(raw_tiff, "wb") as f:
                p = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=60)
            
            if p.returncode != 0:
                err_msg = p.stderr.decode('utf-8', errors='ignore')
                self.set_status(500)
                self.write(json.dumps({"success": False, "msg": f"扫描失败: {err_msg}"}))
                if os.path.exists(raw_tiff):
                    os.remove(raw_tiff)
                return

            # 转码格式
            if fmt == "pdf":
                subprocess.run(["python3", "-c", f"""
from PIL import Image
im = Image.open('{raw_tiff}')
if im.mode in ('RGBA', 'LA'):
    im = im.convert('RGB')
im.save('{final_path}', 'PDF', resolution=float({resolution}))
"""], check=True)
            elif fmt in ["jpg", "jpeg"]:
                subprocess.run(["python3", "-c", f"""
from PIL import Image
im = Image.open('{raw_tiff}')
im.convert('RGB').save('{final_path}', 'JPEG', quality=90)
"""], check=True)
            elif fmt == "png":
                subprocess.run(["python3", "-c", f"""
from PIL import Image
im = Image.open('{raw_tiff}')
im.save('{final_path}', 'PNG')
"""], check=True)
            else:
                os.rename(raw_tiff, final_path)

            if os.path.exists(raw_tiff):
                os.remove(raw_tiff)

            # 如果是复印任务，直接投递到 CUPS 队列
            if action == "copy" and target_printer:
                subprocess.run(["lp", "-d", target_printer, final_path], check=True)

            self.write(json.dumps({
                "success": True, 
                "msg": "复印指令已发送" if action == "copy" else "扫描完成", 
                "filename": out_filename,
                "url": f"/scans/{out_filename}"
            }))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))

# 5. 主控制台页面
class MainHandler(tornado.web.RequestHandler):
    def get(self):
        html = """
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>CUPS 智能打印与扫描控制台</title>
            <style>
                :root { --primary: #0066cc; --danger: #e53e3e; --success: #28a745; --bg: #f4f6f9; }
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 20px; background: var(--bg); color: #333; }
                .container { max-width: 960px; margin: 0 auto; }
                .nav-tabs { display: flex; gap: 10px; margin-bottom: 20px; border-bottom: 2px solid #e2e8f0; }
                .tab-btn { padding: 10px 20px; border: none; background: none; font-size: 16px; cursor: pointer; border-bottom: 3px solid transparent; font-weight: 500; }
                .tab-btn.active { border-color: var(--primary); color: var(--primary); }
                .tab-pane { display: none; }
                .tab-pane.active { display: block; }
                .card { background: white; padding: 24px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; }
                h3 { margin-top: 0; }
                .form-group { margin-bottom: 16px; }
                label { display: block; margin-bottom: 6px; font-weight: 500; font-size: 14px; }
                select, input[type="text"] { width: 100%; padding: 8px 12px; border: 1px solid #cbd5e0; border-radius: 4px; box-sizing: border-box; }
                .btn { padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: 500; text-decoration: none; display: inline-flex; align-items: center; justify-content: center; }
                .btn-primary { background: var(--primary); color: white; }
                .btn-success { background: var(--success); color: white; }
                .btn-danger { background: var(--danger); color: white; }
                .btn-group { display: flex; gap: 8px; }
                .preview-container { border: 2px dashed #cbd5e0; border-radius: 8px; background: #fff; min-height: 280px; display: flex; align-items: center; justify-content: center; overflow: hidden; margin-top: 15px; }
                .preview-container img { max-width: 100%; max-height: 500px; object-fit: contain; }
                .preview-container iframe { width: 100%; height: 500px; border: none; }
                .file-item { display: flex; justify-content: space-between; align-items: center; padding: 12px; border-bottom: 1px solid #edf2f7; }
                .file-item:last-child { border-bottom: none; }
                .file-meta { font-size: 12px; color: #718096; margin-top: 4px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="nav-tabs">
                    <button class="tab-btn active" onclick="switchTab('scan')">📠 扫描与复印</button>
                    <button class="tab-btn" onclick="switchTab('files')">📑 文档管理与预览</button>
                    <button class="tab-btn" onclick="window.open('http://' + window.location.hostname + ':631', '_blank')">⚙️ CUPS 原生管理</button>
                </div>

                <!-- 选项卡 1：扫描复印 -->
                <div id="tab-scan" class="tab-pane active">
                    <div class="card">
                        <h3>设备及参数配置</h3>
                        <div class="form-group">
                            <label>选择扫描仪：</label>
                            <select id="scanner-select"><option value="">正在探测扫描仪...</option></select>
                        </div>
                        <div style="display: flex; gap: 15px;">
                            <div class="form-group" style="flex:1;">
                                <label>色彩模式：</label>
                                <select id="scan-mode">
                                    <option value="Color">全彩模式</option>
                                    <option value="Gray">灰度模式</option>
                                    <option value="Lineart">黑白线条</option>
                                </select>
                            </div>
                            <div class="form-group" style="flex:1;">
                                <label>分辨率：</label>
                                <select id="scan-dpi">
                                    <option value="150">150 DPI (日常推荐)</option>
                                    <option value="200">200 DPI</option>
                                    <option value="300">300 DPI (高清文档)</option>
                                </select>
                            </div>
                            <div class="form-group" style="flex:1;">
                                <label>保存格式：</label>
                                <select id="scan-fmt">
                                    <option value="pdf">PDF 文档</option>
                                    <option value="jpg">JPG 图片</option>
                                    <option value="png">PNG 图片</option>
                                </select>
                            </div>
                        </div>
                        <div class="btn-group" style="margin-top: 10px;">
                            <button class="btn btn-primary" onclick="triggerScan('scan')">🚀 开始扫描</button>
                            <button class="btn btn-success" onclick="triggerScan('copy')">📋 扫描并一键复印</button>
                            <button class="btn" style="background:#e2e8f0; color:#333;" onclick="loadDevices()">🔄 重新探测设备</button>
                        </div>
                        <div id="status-msg" style="margin-top: 15px; font-size: 14px; font-weight: 500;"></div>
                    </div>
                </div>

                <!-- 选项卡 2：文档管理与预览 -->
                <div id="tab-files" class="tab-pane">
                    <div class="card">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <h3>扫描文档列表</h3>
                            <button class="btn btn-primary" onclick="loadFiles()">🔄 刷新列表</button>
                        </div>
                        
                        <!-- 实时预览区 -->
                        <div class="preview-container" id="preview-area">
                            <span style="color: #a0aec0;">点击下方文件项的“预览”按钮在此处查看</span>
                        </div>

                        <!-- 文件列表 -->
                        <div id="file-list" style="margin-top: 15px;"></div>
                    </div>
                </div>
            </div>

            <script>
                function switchTab(name) {
                    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
                    event.target.classList.add('active');
                    document.getElementById('tab-' + name).classList.add('active');
                    if (name === 'files') loadFiles();
                }

                function loadDevices() {
                    fetch('/api/devices')
                    .then(r => r.json())
                    .then(d => {
                        const sel = document.getElementById('scanner-select');
                        sel.innerHTML = '';
                        if (!d.scanners || d.scanners.length === 0) {
                            sel.innerHTML = '<option value="">未找到扫描设备，请检查 USB 连接</option>';
                        } else {
                            d.scanners.forEach(s => {
                                sel.innerHTML += `<option value="${s.id}">${s.name}</option>`;
                            });
                        }
                    });
                }

                function triggerScan(action) {
                    const dev = document.getElementById('scanner-select').value;
                    if (!dev) return alert('请先选择可用的扫描仪设备！');
                    
                    const msg = document.getElementById('status-msg');
                    msg.style.color = '#0066cc';
                    msg.innerText = action === 'copy' ? '正在执行扫描并复印，请稍候...' : '正在扫描出图中，请稍候...';
                    
                    fetch('/api/do_scan', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            device: dev,
                            mode: document.getElementById('scan-mode').value,
                            resolution: document.getElementById('scan-dpi').value,
                            format: document.getElementById('scan-fmt').value,
                            action: action
                        })
                    })
                    .then(r => r.json())
                    .then(res => {
                        if (res.success) {
                            msg.style.color = '#28a745';
                            msg.innerText = res.msg + '：' + res.filename;
                            loadFiles();
                            previewFile(res.url, res.filename.endsWith('.pdf'));
                        } else {
                            msg.style.color = '#e53e3e';
                            msg.innerText = '错误: ' + res.msg;
                        }
                    })
                    .catch(err => {
                        msg.style.color = '#e53e3e';
                        msg.innerText = '通信异常，请检查后端运行状态';
                    });
                }

                function loadFiles() {
                    fetch('/api/scans')
                    .then(r => r.json())
                    .then(data => {
                        const box = document.getElementById('file-list');
                        box.innerHTML = '';
                        if (!data.files || data.files.length === 0) {
                            box.innerHTML = '<div style="color:#a0aec0; padding:15px; text-align:center;">暂无扫描存档</div>';
                            return;
                        }
                        data.files.forEach(f => {
                            box.innerHTML += `
                                <div class="file-item">
                                    <div>
                                        <div style="font-weight: 500;">${f.filename}</div>
                                        <div class="file-meta">${f.mtime} · 大小: ${f.size}</div>
                                    </div>
                                    <div class="btn-group">
                                        <button class="btn btn-success" onclick="previewFile('${f.url}', ${f.is_pdf})">👁 预览</button>
                                        <a class="btn btn-primary" href="${f.url}" download>⬇ 下载</a>
                                        <button class="btn btn-danger" onclick="deleteFile('${f.filename}')">🗑 删除</button>
                                    </div>
                                </div>
                            `;
                        });
                    });
                }

                function previewFile(url, isPdf) {
                    const area = document.getElementById('preview-area');
                    if (isPdf) {
                        area.innerHTML = `<iframe src="${url}"></iframe>`;
                    } else {
                        area.innerHTML = `<img src="${url}?t=${Date.now()}" alt="扫描预览">`;
                    }
                }

                function deleteFile(fname) {
                    if (!confirm(`确定彻底删除 ${fname} 吗？`)) return;
                    fetch('/api/delete_scan', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ filename: fname })
                    })
                    .then(r => r.json())
                    .then(res => {
                        if (res.success) {
                            document.getElementById('preview-area').innerHTML = '<span style="color: #a0aec0;">文件已删除</span>';
                            loadFiles();
                        } else {
                            alert('删除失败: ' + res.msg);
                        }
                    });
                }

                loadDevices();
            </script>
        </body>
        </html>
        """
        self.write(html)

def make_app():
    return tornado.web.Application([
        (r"/", MainHandler),
        (r"/api/devices", DevicesHandler),
        (r"/api/do_scan", DoScanHandler),
        (r"/api/scans", ScanListHandler),
        (r"/api/delete_scan", ScanDeleteHandler),
        # 核心静态路由：向外提供 /scans 物理挂载卷的文件映射，实现无刷新加载与预览
        (r"/scans/(.*)", tornado.web.StaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(8088, address="0.0.0.0")
    tornado.ioloop.IOLoop.current().start()
