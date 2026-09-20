#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import subprocess
import tornado.ioloop
import tornado.web

# 导入底层图像与 Office 处理库
try:
    from mail_print import auto_process_image, convert_office_to_pdf
except ImportError:
    auto_process_image = lambda x: True
    convert_office_to_pdf = lambda x: None

PORT = int(os.getenv("WEB_PORT", "8088"))
UPLOAD_DIR = "/tmp/cups_web_uploads"
SCAN_DIR = os.getenv("SCAN_DIR", "/scans")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)

PRINT_HISTORY = []

def get_printers_info():
    printers = []
    default_printer = ""
    try:
        env = dict(os.environ, LC_ALL="C")
        res_d = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, text=True, env=env, timeout=4)
        if "destination: " in res_d.stdout:
            default_printer = res_d.stdout.split("destination: ")[-1].strip()

        res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, text=True, env=env, timeout=4)
        for line in res_p.stdout.splitlines():
            if line.startswith("printer "):
                parts = line.split()
                p_name = parts[1].strip()
                status = "空闲"
                if "disabled" in line:
                    status = "已暂停"
                elif "printing" in line:
                    status = "打印中"
                printers.append({"name": p_name, "status": status})
    except Exception as e:
        print(f"获取打印机错误: {e}")
    return printers, default_printer

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CUPS 智能打印控制台</title>
    <style>
        :root {
            --primary: #00c065;
            --primary-hover: #00a858;
            --bg: #f4f6f8;
            --card-bg: #ffffff;
            --border: #e2e8f0;
            --text-main: #1e293b;
            --text-muted: #64748b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text-main); font-size: 14px; }
        .navbar { background: #fff; border-bottom: 1px solid var(--border); padding: 12px 28px; display: flex; justify-content: space-between; align-items: center; }
        .navbar-brand { font-size: 18px; font-weight: 700; color: #0f172a; display: flex; align-items: center; gap: 8px; }
        .navbar-brand span { font-size: 13px; font-weight: normal; color: var(--text-muted); }
        .nav-links { display: flex; gap: 12px; }
        .nav-btn { text-decoration: none; padding: 6px 14px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; border: 1px solid transparent; }
        .nav-btn-primary { background: var(--primary); color: #fff; }
        .nav-btn-outline { border-color: var(--border); background: #fff; color: var(--text-main); }
        .container { max-width: 1280px; margin: 24px auto; padding: 0 20px; display: grid; grid-template-columns: 1.6fr 1fr; gap: 24px; }
        @media (max-width: 900px) { .container { grid-template-columns: 1fr; } }
        .card { background: var(--card-bg); border-radius: 10px; border: 1px solid var(--border); padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.03); }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; border-bottom: 1px solid #f1f5f9; padding-bottom: 12px; font-weight: 600; font-size: 15px; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
        .form-group { display: flex; flex-direction: column; gap: 6px; }
        .form-label { font-size: 13px; font-weight: 500; color: #334155; }
        .form-control { width: 100%; height: 38px; border: 1px solid var(--border); border-radius: 6px; padding: 0 12px; font-size: 13px; background: #fff; outline: none; }
        .form-control:focus { border-color: var(--primary); }
        .pill-group { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; background: #f8fafc; height: 38px; }
        .pill-btn { flex: 1; border: none; background: transparent; cursor: pointer; font-size: 13px; font-weight: 500; display: flex; align-items: center; justify-content: center; gap: 6px; }
        .pill-btn.active { background: var(--primary); color: #fff; font-weight: 600; }
        .upload-zone { border: 2px dashed #cbd5e1; border-radius: 8px; padding: 24px 16px; text-align: center; cursor: pointer; background: #f8fafc; margin-bottom: 16px; }
        .upload-zone:hover { border-color: var(--primary); background: #f0fdf4; }
        .upload-info { display: flex; align-items: center; justify-content: space-between; background: #f0fdf4; border: 1px solid #bbf7d0; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
        .btn-submit { width: 100%; height: 44px; background: var(--primary); color: white; border: none; border-radius: 6px; font-size: 15px; font-weight: 600; cursor: pointer; }
        .btn-submit:hover { background: var(--primary-hover); }
        .record-item { border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; background: #fff; display: flex; justify-content: space-between; align-items: center; }
        .record-title { font-weight: 600; font-size: 13px; color: #1e293b; margin-bottom: 4px; max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .record-sub { font-size: 11px; color: var(--text-muted); }
        .status-tag { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: #dcfce7; color: #15803d; font-weight: 600; }
    </style>
</head>
<body>
    <header class="navbar">
        <div class="navbar-brand">🖨️ CUPS 打印 <span>admin</span></div>
        <div class="nav-links">
            <button class="nav-btn nav-btn-primary" onclick="location.reload()">🔄 刷新</button>
            <a href="http://" + location.hostname + ":631" target="_blank" class="nav-btn nav-btn-outline" id="cupsLink">⚙️ 原生后台 (631)</a>
        </div>
    </header>

    <main class="container">
        <section>
            <div class="card">
                <div class="card-header"><span>🖨️ 打印机与文档</span></div>
                <div class="form-group" style="margin-bottom: 16px;">
                    <label class="form-label">选择目标打印机</label>
                    <select id="printerSelect" class="form-control"></select>
                </div>
                <div class="upload-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
                    <input type="file" id="fileInput" style="display: none;" onchange="handleFileSelect(this.files)">
                    <div style="font-size: 28px; margin-bottom: 6px;">📄</div>
                    <div style="font-weight: 600; color: #334155;">点击或拖拽试卷/照片/文档到此处</div>
                    <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">支持自动四角纠偏与纯白去黑底滤镜</div>
                </div>
                <div id="fileInfoBox" class="upload-info" style="display: none;">
                    <div>
                        <span id="fileNameDisplay" style="font-weight: 600;"></span>
                        <span id="fileSizeDisplay" style="color: var(--text-muted); margin-left: 8px;"></span>
                    </div>
                    <span style="color: var(--primary); font-weight: 600;">✓ 就绪</span>
                </div>
            </div>

            <div class="card">
                <div class="card-header"><span>⚙️ 打印参数</span></div>
                <div class="form-row">
                    <div class="form-group">
                        <label class="form-label">颜色模式</label>
                        <div class="pill-group">
                            <button type="button" class="pill-btn active" id="btnColor" onclick="setColor('color')">🌈 彩色打印</button>
                            <button type="button" class="pill-btn" id="btnGray" onclick="setColor('gray')">⚪ 黑白打印</button>
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label">打印方向</label>
                        <div class="pill-group">
                            <button type="button" class="pill-btn active" id="btnPortrait" onclick="setOrient('portrait')">▯ 纵向</button>
                            <button type="button" class="pill-btn" id="btnLandscape" onclick="setOrient('landscape')">▭ 横向</button>
                        </div>
                    </div>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label class="form-label">双面打印</label>
                        <select id="duplexSelect" class="form-control">
                            <option value="one-sided">单面打印</option>
                            <option value="two-sided-long-edge">双面 (长边翻转)</option>
                            <option value="two-sided-short-edge">双面 (短边翻转)</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label">份数</label>
                        <input type="number" id="copiesInput" class="form-control" value="1" min="1" max="99">
                    </div>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label class="form-label">纸张大小</label>
                        <select id="mediaSelect" class="form-control">
                            <option value="A4">A4 (210×297mm)</option>
                            <option value="A5">A5 (148×210mm)</option>
                            <option value="B5">B5 (182×257mm)</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label">页面范围</label>
                        <input type="text" id="pageRangeInput" class="form-control" placeholder="留空=全部，如: 1-5">
                    </div>
                </div>

                <button class="btn-submit" onclick="submitPrintJob()">🖨️ 立即提交打印</button>
            </div>
        </section>

        <section>
            <div class="card">
                <div class="card-header">
                    <span>📈 打印机状态</span>
                    <span class="status-tag" id="curStatusTag">空闲</span>
                </div>
                <div style="font-size: 13px; line-height: 2;">
                    <div>• 队列就绪状态：<b>正常监听</b></div>
                    <div>• 纸盒标准：<b>A4 进纸就绪 (已纠正 AirPrint)</b></div>
                    <div>• 图像滤镜引擎：<b>扫描全能王级纯白加深已激活</b></div>
                </div>
            </div>

            <div class="card">
                <div class="card-header"><span>🕒 打印记录</span></div>
                <div id="historyList">
                    <div style="text-align: center; color: var(--text-muted); padding: 20px;">暂无打印记录</div>
                </div>
            </div>
        </section>
    </main>

    <script>
        document.getElementById('cupsLink').href = 'http://' + location.hostname + ':631';
        let selectedFile = null;
        let printConfig = { color: 'color', orient: 'portrait' };

        function setColor(m) {
            printConfig.color = m;
            document.getElementById('btnColor').classList.toggle('active', m === 'color');
            document.getElementById('btnGray').classList.toggle('active', m === 'gray');
        }
        function setOrient(o) {
            printConfig.orient = o;
            document.getElementById('btnPortrait').classList.toggle('active', o === 'portrait');
            document.getElementById('btnLandscape').classList.toggle('active', o === 'landscape');
        }
        function handleFileSelect(files) {
            if (!files.length) return;
            selectedFile = files[0];
            document.getElementById('fileNameDisplay').textContent = selectedFile.name;
            document.getElementById('fileSizeDisplay').textContent = (selectedFile.size / 1024 / 1024).toFixed(2) + ' MB';
            document.getElementById('fileInfoBox').style.display = 'flex';
        }
        async function loadPrinters() {
            try {
                const res = await fetch('/api/printers');
                const data = await res.json();
                const sel = document.getElementById('printerSelect');
                sel.innerHTML = '';
                data.printers.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.name;
                    opt.textContent = `${p.name} (${p.status})`;
                    if (p.name === data.default) opt.selected = true;
                    sel.appendChild(opt);
                });
                if (data.printers.length > 0) document.getElementById('curStatusTag').textContent = data.printers[0].status;
            } catch(e) {}
        }
        async function loadHistory() {
            try {
                const res = await fetch('/api/history');
                const list = await res.json();
                const c = document.getElementById('historyList');
                if (!list.length) return;
                c.innerHTML = list.map(i => `
                    <div class="record-item">
                        <div>
                            <div class="record-title">${i.filename}</div>
                            <div class="record-sub">${i.printer} · ${i.time}</div>
                        </div>
                        <span class="status-tag">${i.status}</span>
                    </div>
                `).join('');
            } catch(e) {}
        }
        async function submitPrintJob() {
            if (!selectedFile) return alert('请先选择文件！');
            const fd = new FormData();
            fd.append('file', selectedFile);
            fd.append('printer', document.getElementById('printerSelect').value);
            fd.append('color', printConfig.color);
            fd.append('orient', printConfig.orient);
            fd.append('duplex', document.getElementById('duplexSelect').value);
            fd.append('copies', document.getElementById('copiesInput').value);
            fd.append('media', document.getElementById('mediaSelect').value);
            fd.append('pageRange', document.getElementById('pageRangeInput').value);

            const res = await fetch('/api/print', { method: 'POST', body: fd });
            const ret = await res.json();
            if (ret.code === 0) {
                alert('打印任务提交成功！');
                loadHistory();
            } else {
                alert('提交失败: ' + ret.msg);
            }
        }
        loadPrinters();
        loadHistory();
        setInterval(loadHistory, 5000);
    </script>
</body>
</html>
"""

class MainHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html; charset=UTF-8")
        self.write(HTML_PAGE)

class PrintersApiHandler(tornado.web.RequestHandler):
    def get(self):
        printers, def_p = get_printers_info()
        self.write(json.dumps({"printers": printers, "default": def_p}))

class HistoryApiHandler(tornado.web.RequestHandler):
    def get(self):
        self.write(json.dumps(PRINT_HISTORY))

class PrintApiHandler(tornado.web.RequestHandler):
    def post(self):
        if 'file' not in self.request.files:
            self.write(json.dumps({"code": 1, "msg": "未上传文件"}))
            return

        file_obj = self.request.files['file'][0]
        filename = file_obj['filename']
        filepath = os.path.join(UPLOAD_DIR, filename)
        ext = os.path.splitext(filename)[1].lower()

        with open(filepath, 'wb') as f:
            f.write(file_obj['body'])

        # 核心联动：如果是照片/试卷，Web 上传直接调用纠偏与白底纯化
        if ext in [".jpg", ".jpeg", ".png", ".bmp", ".heic"]:
            auto_process_image(filepath)
        # 如果是 Office 且系统装了 LibreOffice，自动转换
        elif ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]:
            conv_pdf = convert_office_to_pdf(filepath)
            if conv_pdf:
                filepath = conv_pdf

        printer = self.get_argument("printer", "")
        color = self.get_argument("color", "color")
        orient = self.get_argument("orient", "portrait")
        duplex = self.get_argument("duplex", "one-sided")
        copies = self.get_argument("copies", "1")
        media = self.get_argument("media", "A4")
        page_range = self.get_argument("pageRange", "")

        cmd = ["lp"]
        if printer: cmd.extend(["-d", printer])
        cmd.extend(["-n", str(copies)])
        cmd.extend(["-o", f"media={media}"])
        cmd.extend(["-o", "fit-to-page"])

        if orient == "landscape":
            cmd.extend(["-o", "orientation-requested=4"])
        if color == "gray":
            cmd.extend(["-o", "ColorModel=Gray"])
        if duplex != "one-sided":
            cmd.extend(["-o", f"sides={duplex}"])
        if page_range.strip():
            cmd.extend(["-o", f"page-ranges={page_range.strip()}"])

        cmd.append(filepath)

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)
            if res.returncode == 0:
                PRINT_HISTORY.insert(0, {
                    "filename": filename,
                    "printer": printer or "默认设备",
                    "time": time.strftime("%m/%d %H:%M"),
                    "status": "已提交"
                })
                if len(PRINT_HISTORY) > 20: PRINT_HISTORY.pop()
                self.write(json.dumps({"code": 0, "msg": "成功"}))
            else:
                self.write(json.dumps({"code": 1, "msg": res.stderr.strip()}))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": str(e)}))

def make_app():
    return tornado.web.Application([
        (r"/?", MainHandler),
        (r"/api/printers", PrintersApiHandler),
        (r"/api/history", HistoryApiHandler),
        (r"/api/print", PrintApiHandler),
        (r"/scans/(.*)", tornado.web.StaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(PORT, address="0.0.0.0")
    print(f" [Web App] 扁平化打印控制台已监听 0.0.0.0:{PORT} ...", flush=True)
    tornado.ioloop.IOLoop.current().start()
