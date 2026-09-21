#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import io
import time
import json
import subprocess
from PIL import Image, ImageOps, ImageFilter
import tornado.ioloop
import tornado.web

# 自适应检测是否可用 OpenCV 增强算子 (兼容海纳思与 N1/x86)
try:
    import numpy as np
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

PORT = int(os.getenv("WEB_PORT", "8088"))
UPLOAD_DIR = "/tmp/cups_web_uploads"
PPD_DIR = "/etc/cups/ppd"
SCAN_DIR = os.getenv("SCAN_DIR", "/scans")

for d in (UPLOAD_DIR, PPD_DIR, SCAN_DIR):
    os.makedirs(d, exist_ok=True)

PRINT_HISTORY = []

def get_clean_env():
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env

def get_uptime_str():
    try:
        with open('/proc/uptime') as f:
            sec = float(f.readline().split()[0])
            d, h = int(sec // 86400), int((sec % 86400) // 3600)
            return "%d天%d小时" % (d, h) if d > 0 else "%d小时%d分钟" % (h, int((sec % 3600) // 60))
    except Exception:
        return "正常运行中"

def get_printer_model(name):
    ppd_file = os.path.join(PPD_DIR, "%s.ppd" % name)
    if os.path.exists(ppd_file):
        try:
            with open(ppd_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("*NickName:"):
                        return line.split(":", 1)[1].strip().strip('"')
                    elif line.startswith("*ModelName:"):
                        return line.split(":", 1)[1].strip().strip('"')
        except Exception:
            pass
    try:
        r = subprocess.run(["lpstat", "-l", "-p", name], capture_output=True, text=True, env=get_clean_env(), timeout=3)
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("Description:"):
                desc = line.split(":", 1)[1].strip()
                if desc: return desc
    except Exception:
        pass
    return ""

def diagnose_printer(name):
    env = get_clean_env()
    status, stype = "空闲", "idle"
    try:
        r = subprocess.run(["lpstat", "-l", "-p", name], capture_output=True, text=True, env=env, timeout=3)
        raw = (r.stdout + r.stderr).lower()
        if any(k in raw for k in ["media-jam", "paper jam"]): status, stype = "卡纸", "error"
        elif any(k in raw for k in ["media-empty", "out of paper"]): status, stype = "缺纸", "error"
        elif any(k in raw for k in ["toner-empty", "marker-supply-empty", "cartridge"]): status, stype = "缺墨", "warn"
        elif "door-open" in raw or "cover open" in raw: status, stype = "机盖打开", "warn"
        elif "offline" in raw or "not connected" in raw: status, stype = "脱机", "error"
        elif "disabled" in raw: status, stype = "已暂停", "warn"
        elif "printing" in raw: status, stype = "打印中", "busy"
    except Exception:
        pass

    try:
        q = subprocess.run(["lpstat", "-o", name], capture_output=True, text=True, env=env, timeout=3)
        jobs = len([l for l in q.stdout.splitlines() if l.strip()])
    except Exception:
        jobs = 0

    return status, stype, jobs

# ================= 图像处理逻辑 (海纳思原生 Pillow + N1/x86 OpenCV 自适应) =================

def remove_shadow_and_whiten(cv_img):
    if not HAS_OPENCV: return cv_img
    rgb_planes = cv2.split(cv_img)
    result_norm_planes = []
    for plane in rgb_planes:
        dilated_img = cv2.dilate(plane, np.ones((15, 15), np.uint8))
        bg_img = cv2.medianBlur(dilated_img, 21)
        diff_img = 255 - cv2.absdiff(plane, bg_img)
        norm_img = cv2.normalize(diff_img, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)
        result_norm_planes.append(norm_img)
    result_norm = cv2.merge(result_norm_planes)
    gray = cv2.cvtColor(result_norm, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 225, 255, cv2.THRESH_BINARY)
    result_norm[mask == 255] = [255, 255, 255]
    return result_norm

def correct_document_perspective(cv_img):
    if not HAS_OPENCV: return cv_img
    orig = cv_img.copy()
    h, w = cv_img.shape[:2]
    scale = 800.0 / max(h, w)
    small = cv2.resize(cv_img, (int(w * scale), int(h * scale)))
    
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 30, 120)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated = cv2.dilate(edged, kernel, iterations=2)
    
    cnts, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
    
    doc_cnt = None
    for c in cnts:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4 and cv2.contourArea(c) > (small.shape[0] * small.shape[1] * 0.25):
            doc_cnt = approx
            break
            
    if doc_cnt is not None:
        pts = doc_cnt.reshape(4, 2) / scale
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        
        (tl, tr, br, bl) = rect
        widthA = np.linalg.norm(br - bl)
        widthB = np.linalg.norm(tr - tl)
        maxWidth = max(int(widthA), int(widthB))
        heightA = np.linalg.norm(tr - br)
        heightB = np.linalg.norm(tl - bl)
        maxHeight = max(int(heightA), int(heightB))
        
        dst = np.array([
            [0, 0],
            [maxWidth - 1, 0],
            [maxWidth - 1, maxHeight - 1],
            [0, maxHeight - 1]
        ], dtype="float32")
        
        M = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(orig, M, (maxWidth, maxHeight), flags=cv2.INTER_LANCZOS4)
    return orig

def process_smart_image(image_bytes, auto_whiten=True, auto_deskew=True):
    if not HAS_OPENCV:
        im = Image.open(io.BytesIO(image_bytes))
        return ImageOps.exif_transpose(im).convert('RGB')
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return Image.open(io.BytesIO(image_bytes)).convert('RGB')
    if auto_deskew: img = correct_document_perspective(img)
    if auto_whiten: img = remove_shadow_and_whiten(img)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)

def smart_crop_card(image_bytes, crop_pct=0.06, target_w=1011, target_h=638):
    im = Image.open(io.BytesIO(image_bytes))
    im = ImageOps.exif_transpose(im).convert('RGB')
    if im.height > im.width:
        im = im.rotate(270, expand=True)
    w, h = im.size
    pct = max(0.0, min(0.25, float(crop_pct)))
    crop_rect = (int(w * pct), int(h * pct), int(w * (1.0 - pct)), int(h * (1.0 - pct)))
    im = im.crop(crop_rect)
    return ImageOps.fit(im, (target_w, target_h), method=Image.Resampling.LANCZOS)

HTML = '''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>CUPS 智能控制台</title>
<style>
:root{--p:#00c065;--b:#0284c7;--d:#ef4444;--bg:#f4f6f8;--c:#fff;--bd:#e2e8f0;--tx:#1e293b;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}
.nav{background:var(--c);border-bottom:1px solid var(--bd);padding:10px 24px;display:flex;justify-content:space-between;align-items:center}
.top-tabs{display:flex;gap:4px;background:#e2e8f0;padding:3px;border-radius:8px}
.top-tab{border:none;background:transparent;padding:6px 16px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;color:var(--mu)}
.top-tab.active{background:#fff;color:var(--tx);box-shadow:0 1px 3px rgba(0,0,0,.08)}
.btn{border:none;padding:6px 14px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:4px}
.btn-p{background:var(--p);color:#fff}.btn-b{background:var(--b);color:#fff}.btn-o{border:1px solid var(--bd);background:var(--c);color:var(--tx)}
.box{max-width:1280px;margin:20px auto;padding:0 20px;display:grid;grid-template-columns:1.55fr 1fr;gap:20px}
@media(max-width:900px){.box{grid-template-columns:1fr}}
.card{background:var(--c);border-radius:10px;border:1px solid var(--bd);padding:20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.02)}
.card-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #f1f5f9;font-weight:600}

.sub-nav-tabs{display:flex;border:1px solid var(--bd);border-radius:8px;overflow:hidden;margin-bottom:16px;background:#f8fafc}
.sub-tab-btn{flex:1;text-align:center;padding:10px 0;font-weight:600;color:var(--mu);cursor:pointer;border-right:1px solid var(--bd);background:#fff;transition:.2s}
.sub-tab-btn:last-child{border-right:none}
.sub-tab-btn.active{background:var(--b);color:#fff}

.row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.fg{display:flex;flex-direction:column;gap:6px}.fc{width:100%;height:38px;border:1px solid var(--bd);border-radius:6px;padding:0 12px;outline:none}
.pills{display:flex;border:1px solid var(--bd);border-radius:6px;overflow:hidden;height:38px}
.pill{flex:1;border:none;background:#f8fafc;cursor:pointer;font-size:13px;font-weight:500}.pill.act{background:var(--p);color:#fff;font-weight:600}
.drop{border:2px dashed #cbd5e1;border-radius:8px;padding:22px;text-align:center;cursor:pointer;background:#f8fafc;margin-bottom:16px}
.drop:hover{border-color:var(--b);background:#f0f9ff}

.idcard-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.idcard-box{border:2px dashed #cbd5e1;border-radius:8px;height:140px;display:flex;flex-direction:column;justify-content:center;align-items:center;cursor:pointer;background:#f8fafc;position:relative;overflow:hidden}
.idcard-box:hover{border-color:var(--b);background:#f0f9ff}
.idcard-box img{width:100%;height:100%;object-fit:cover;position:absolute;top:0;left:0}

.ai-enhance-bar{background:#f8fafc;border:1px dashed #38bdf8;border-radius:6px;padding:10px 14px;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center}
.crop-slider-bar{background:#f1f5f9;border:1px solid var(--bd);border-radius:6px;padding:10px 14px;margin-bottom:14px;display:flex;flex-direction:column;gap:8px}

.file-list{display:none;flex-direction:column;gap:8px;margin-bottom:16px}
.file-item{display:flex;align-items:center;justify-content:space-between;background:#f8fafc;border:1px solid var(--bd);padding:8px 12px;border-radius:6px}
.prev-box{background:#cbd5e1;border-radius:8px;padding:16px;display:flex;justify-content:center;align-items:center;min-height:300px}
.paper{background:#fff;box-shadow:0 6px 16px rgba(0,0,0,.15);border-radius:4px;display:flex;justify-content:center;align-items:center;overflow:hidden;width:220px;height:311px;position:relative}
.st-row{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:#f8fafc;border-radius:6px;margin-bottom:8px;border:1px solid #edf2f7}
.badge{font-size:12px;padding:3px 10px;border-radius:12px;font-weight:600}
.badge-idle{background:#dcfce7;color:#15803d}.badge-busy{background:#e0f2fe;color:#0369a1}.badge-warn{background:#fef9c3;color:#854d0e}.badge-error{background:#fee2e2;color:#b91c1c}
.sub-btn{width:100%;height:44px;background:var(--p);color:#fff;border:none;border-radius:6px;font-size:15px;font-weight:600;cursor:pointer}
.modal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.45);display:none;justify-content:center;align-items:center;z-index:999}
.m-box{background:#fff;width:500px;max-width:92%;border-radius:12px;padding:24px;box-shadow:0 8px 24px rgba(0,0,0,.15)}
</style></head>
<body>
<header class="nav">
  <div style="display:flex;align-items:center;gap:16px">
    <div style="font-size:18px;font-weight:700">🖨️ CUPS 智能打印系统</div>
    <div class="top-tabs">
      <button class="top-tab active" id="topTabPrint" onclick="switchMainTab('print')">📄 智能打印</button>
      <button class="top-tab" id="topTabScan" onclick="switchMainTab('scan')">📠 扫描复印</button>
    </div>
  </div>
  <div style="display:flex;gap:10px">
    <button class="btn btn-b" onclick="openModal()">➕ 添加驱动</button>
    <button class="btn btn-p" onclick="loadAll()">🔄 刷新</button>
    <a id="cupsL" target="_blank" class="btn btn-o">⚙️ 631后台</a>
  </div>
</header>

<main class="box" id="printView">
<section>
  <div class="card">
    <div class="card-h">
      <span>🖨️ 打印机与业务类型</span>
      <a href="javascript:openModal()" style="font-size:12px;color:var(--b);text-decoration:none">➕ 安装新驱动</a>
    </div>
    <div class="fg" style="margin-bottom:14px"><select id="selP" class="fc" onchange="syncP()"></select></div>

    <div class="sub-nav-tabs">
      <div class="sub-tab-btn active" id="subTabStd" onclick="switchPrintMode('std')">📄 标准/作业打印</div>
      <div class="sub-tab-btn" id="subTabInv" onclick="switchPrintMode('inv')">🧾 发票打印</div>
      <div class="sub-tab-btn" id="subTabId" onclick="switchPrintMode('id')">🪪 身份证打印</div>
    </div>

    <!-- 1. 标准打印面板 -->
    <div id="panelStd">
      <div class="ai-enhance-bar">
        <span style="font-weight:600;font-size:12px;color:var(--b)">✨ 作业试卷拍摄增强:</span>
        <label style="cursor:pointer;font-size:12px"><input type="checkbox" id="chkWhiten" checked> 消除灰暗黑底</label>
        <label style="cursor:pointer;font-size:12px"><input type="checkbox" id="chkDeskew" checked> 自动透视拉平</label>
      </div>
      <div class="drop" id="dzStd"><input type="file" id="fiStd" multiple style="display:none"><div style="font-size:28px">📑</div><div style="font-weight:600">点击或拖入照片/试卷/PDF (支持多选批量)</div></div>
      <div class="file-list" id="flistStd"></div>
    </div>

    <!-- 2. 发票打印面板 -->
    <div id="panelInv" style="display:none">
      <div class="drop" id="dzInv" style="border-color:#38bdf8"><input type="file" id="fiInv" accept=".pdf,image/*" multiple style="display:none"><div style="font-size:28px">🧾</div><div style="font-weight:600">点击上传发票文件 (支持 PDF/图片多选)</div><div style="font-size:12px;color:var(--mu);margin-top:2px">自动居中并适配发票纸张边缘</div></div>
      <div class="file-list" id="flistInv"></div>
    </div>

    <!-- 3. 身份证双面打印面板 -->
    <div id="panelId" style="display:none">
      <div style="margin-bottom:8px;font-weight:600;font-size:13px">🪪 上传身份证正反面</div>
      <div class="idcard-grid">
        <div class="idcard-box" id="boxIdFront" onclick="document.getElementById('fiIdF').click()">
          <input type="file" id="fiIdF" accept="image/*" style="display:none" onchange="handleIdFile(this,'front')">
          <div style="font-size:26px">📷</div><div style="font-weight:600;margin-top:2px">正面 (人像面)</div><div style="font-size:11px;color:var(--mu)">点击或拍照上传</div>
          <img id="imgIdF" style="display:none">
        </div>
        <div class="idcard-box" id="boxIdBack" onclick="document.getElementById('fiIdB').click()">
          <input type="file" id="fiIdB" accept="image/*" style="display:none" onchange="handleIdFile(this,'back')">
          <div style="font-size:26px">📷</div><div style="font-weight:600;margin-top:2px">反面 (国徽面)</div><div style="font-size:11px;color:var(--mu)">点击或拍照上传</div>
          <img id="imgIdB" style="display:none">
        </div>
      </div>

      <div class="crop-slider-bar">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-weight:600;font-size:12px">✂️ 桌面边缘深度剔除程度: <span id="cropValText" style="color:var(--b)">切除四周边缘 6%</span></span>
          <button type="button" class="btn btn-o" style="padding:1px 8px;font-size:11px" onclick="clearIdCards()">重新上传</button>
        </div>
        <input type="range" id="cropRange" min="0" max="18" value="6" step="1" oninput="onCropRangeChange(this.value)" style="width:100%;accent-color:var(--b)">
        <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--mu)">
          <span>保持原图(0%)</span>
          <span>适中去桌边(6%)</span>
          <span>深度去杂边(18%)</span>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-h"><span>⚲ 打印参数</span></div>
    <div class="row">
      <div class="fg"><label>色彩</label><div class="pills"><button type="button" class="pill act" id="bCol" onclick="setOpt('c','color')">彩色</button><button type="button" class="pill" id="bGray" onclick="setOpt('c','gray')">黑白</button></div></div>
      <div class="fg"><label>方向</label><div class="pills"><button type="button" class="pill act" id="bPor" onclick="setOpt('o','portrait')">纵向</button><button type="button" class="pill" id="bLan" onclick="setOpt('o','landscape')">横向</button></div></div>
    </div>
    <div class="row">
      <div class="fg"><label>份数</label><input type="number" id="cop" class="fc" value="1" min="1" max="99"></div>
      <div class="fg"><label>纸张</label><select id="med" class="fc"><option value="A4">A4 (210×297mm)</option><option value="A5">A5</option></select></div>
    </div>
    
    <div class="fg" style="margin-bottom:16px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
        <label>👀 纸张排版预览 <span id="prevName" style="color:var(--mu);font-size:12px"></span></label>
        <a id="extPrevLink" href="#" target="_blank" style="display:none;font-size:12px;color:var(--b);text-decoration:none">在新标签页打开 ↗</a>
      </div>
      <div class="prev-box">
        <div class="paper" id="paperSheet">
          <div id="phTip" style="color:var(--mu);text-align:center"><div style="font-size:32px">🖼️</div>请上传文件预览</div>
          <img id="prevImg" style="display:none;max-width:100%;max-height:100%;object-fit:contain">
          <div id="pdfWrap" style="display:none;width:100%;height:100%"></div>
        </div>
      </div>
    </div>

    <button class="sub-btn" id="submitBtn" onclick="submitPrint()">🚀 开始打印</button>
  </div>
</section>

<section>
  <div class="card">
    <div class="card-h"><span>📈 打印机状态</span><button class="btn btn-o" style="padding:2px 8px;font-size:11px" onclick="loadPrinters()">🔄</button></div>
    <div class="st-row"><span>硬件型号</span><span id="stM" style="font-weight:600;color:var(--b);max-width:60%;text-align:right">-</span></div>
    <div class="st-row"><span>设备状态</span><span class="badge badge-idle" id="stB">检测中</span></div>
    <div class="st-row"><span>队列任务</span><b id="stJ">0</b></div>
    <div class="st-row"><span>持续运行</span><span id="stU">-</span></div>
  </div>
  <div class="card"><div class="card-h"><span>🕒 打印记录</span></div><div id="hlist" style="color:var(--mu);text-align:center;padding:12px">暂无记录</div></div>
</section>
</main>

<main class="box" id="scanView" style="display:none">
<section>
  <div class="card">
    <div class="card-h"><span>📠 扫描仪设备</span><button class="btn btn-p" onclick="scanHardwareScanners()">🔍 探测扫描仪</button></div>
    <div class="fg" style="margin-bottom:14px"><select id="scannerSelect" class="fc"><option value="">探测中...</option></select></div>
    <div class="row">
      <div class="fg"><label>分辨率 (DPI)</label><select id="scanResolution" class="fc"><option value="300" selected>300 DPI (高清)</option><option value="150">150 DPI</option></select></div>
      <div class="fg"><label>色彩</label><select id="scanMode" class="fc"><option value="Color">彩色</option><option value="Gray">灰度</option></select></div>
    </div>
    <button class="sub-btn" id="startScanBtn" onclick="doStartScan()">🚀 开始扫描并生成文件</button>
  </div>
  
  <div class="card">
    <div class="card-h">
      <span>👀 扫描预览与复印</span>
      <div id="scanToolbar" style="display:none;gap:8px">
        <button class="btn btn-p" onclick="printCurrentScan()">🖨️ 立即复印出纸</button>
        <a id="scanDownloadBtn" class="btn btn-o" download style="font-size:12px;padding:4px 8px">下载</a>
      </div>
    </div>
    <div class="prev-box">
      <div class="paper" id="scanSheet">
        <div id="scanTip" style="color:var(--mu);text-align:center"><div style="font-size:36px">📠</div>等待扫描</div>
        <img id="scanPrevImg" style="display:none;max-width:100%;max-height:100%;object-fit:contain">
        <div id="scanPdfWrap" style="display:none;width:100%;height:100%"></div>
      </div>
    </div>
  </div>
</section>
<section>
  <div class="card"><div class="card-h"><span>📁 扫描库 (/scans)</span><button class="btn btn-o" onclick="loadScanFiles()">刷新</button></div><div id="scanFileList"></div></div>
</section>
</main>

<div class="modal" id="mD">
  <div class="m-box">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <b style="font-size:16px">➕ 绑定与安装打印机驱动</b>
      <span style="cursor:pointer;font-size:22px;color:var(--mu)" onclick="closeModal()">&times;</span>
    </div>
    <div class="fg" style="margin-bottom:10px">
      <label>1. 扫描物理硬件</label>
      <div style="display:flex;gap:6px"><select id="devs" class="fc" onchange="document.getElementById('devUri').value=this.value"></select><button class="btn btn-p" onclick="scanDevs()">扫描</button></div>
    </div>
    <div class="fg" style="margin-bottom:10px"><label>设备 URI</label><input type="text" id="devUri" class="fc" placeholder="usb://..."></div>
    <div class="fg" style="margin-bottom:10px"><label>打印机标识 (英文)</label><input type="text" id="pName" class="fc" placeholder="如 HP_M126a"></div>
    <div class="fg" style="margin-bottom:14px"><label>选择驱动</label>
      <div style="display:flex;gap:12px;margin-bottom:6px">
        <label><input type="radio" name="pm" value="e" checked onchange="togPpd()"> 系统推荐驱动库</label>
        <label><input type="radio" name="pm" value="u" onchange="togPpd()"> 上传PPD文件</label>
      </div>
      <select id="ePpd" class="fc"></select>
      <input type="file" id="uPpd" class="fc" accept=".ppd" style="display:none;padding-top:6px">
    </div>
    <button class="sub-btn" id="addBtn" onclick="submitAddP()">🚀 立即绑定驱动</button>
  </div>
</div>

<script>
var currentMode = 'std';
var filesStd = [], filesInv = [];
var idFrontFile = null, idBackFile = null, idCardMergedBlob = null;
var currentCropPct = 0.06;
var cfg = {c: 'color', o: 'portrait'}, pData = [], currentScanFile = null;

document.getElementById('cupsL').href = 'http://' + location.hostname + ':631';

function switchMainTab(t) {
  document.getElementById('topTabPrint').className = (t === 'print' ? 'top-tab active' : 'top-tab');
  document.getElementById('topTabScan').className = (t === 'scan' ? 'top-tab active' : 'top-tab');
  document.getElementById('printView').style.display = (t === 'print' ? 'grid' : 'none');
  document.getElementById('scanView').style.display = (t === 'scan' ? 'grid' : 'none');
  if (t === 'scan') { scanHardwareScanners(); loadScanFiles(); }
}

function switchPrintMode(m) {
  currentMode = m;
  document.getElementById('subTabStd').className = (m === 'std' ? 'sub-tab-btn active' : 'sub-tab-btn');
  document.getElementById('subTabInv').className = (m === 'inv' ? 'sub-tab-btn active' : 'sub-tab-btn');
  document.getElementById('subTabId').className = (m === 'id' ? 'sub-tab-btn active' : 'sub-tab-btn');
  document.getElementById('panelStd').style.display = (m === 'std' ? 'block' : 'none');
  document.getElementById('panelInv').style.display = (m === 'inv' ? 'block' : 'none');
  document.getElementById('panelId').style.display = (m === 'id' ? 'block' : 'none');

  if (m === 'std') {
    if (filesStd.length) showFilePreview(filesStd[filesStd.length - 1]);
    else clearPreview();
  } else if (m === 'inv') {
    if (filesInv.length) showFilePreview(filesInv[filesInv.length - 1]);
    else clearPreview();
  } else if (m === 'id') {
    if (idCardMergedBlob) showBlobPreview(idCardMergedBlob, '身份证正反面排版.pdf');
    else clearPreview();
  }
}

var dzStd = document.getElementById('dzStd'), fiStd = document.getElementById('fiStd');
dzStd.onclick = function() { fiStd.click(); };
fiStd.onchange = function() { for (var i = 0; i < this.files.length; i++) filesStd.push(this.files[i]); renderFileList('std'); };

var dzInv = document.getElementById('dzInv'), fiInv = document.getElementById('fiInv');
dzInv.onclick = function() { fiInv.click(); };
fiInv.onchange = function() { for (var i = 0; i < this.files.length; i++) filesInv.push(this.files[i]); renderFileList('inv'); };

function renderFileList(type) {
  var arr = (type === 'std' ? filesStd : filesInv);
  var list = document.getElementById(type === 'std' ? 'flistStd' : 'flistInv');
  if (!arr.length) { list.style.display = 'none'; clearPreview(); return; }
  list.style.display = 'flex';
  var html = '';
  for (var i = 0; i < arr.length; i++) {
    html += '<div class="file-item">' +
            '<span>' + (arr[i].name.toLowerCase().indexOf('.pdf') !== -1 ? '📑 ' : '🖼️ ') + arr[i].name + '</span>' +
            '<button class="btn btn-o" style="color:var(--d)" onclick="removeFile(\\'' + type + '\\',' + i + ')">删除</button>' +
            '</div>';
  }
  list.innerHTML = html;
  showFilePreview(arr[arr.length - 1]);
}

function removeFile(type, idx) {
  if (type === 'std') { filesStd.splice(idx, 1); renderFileList('std'); }
  else { filesInv.splice(idx, 1); renderFileList('inv'); }
}

function handleIdFile(input, side) {
  if (!input.files || !input.files[0]) return;
  var file = input.files[0];
  var reader = new FileReader();
  reader.onload = function(e) {
    if (side === 'front') {
      idFrontFile = file;
      var img = document.getElementById('imgIdF');
      img.src = e.target.result; img.style.display = 'block';
    } else {
      idBackFile = file;
      var img = document.getElementById('imgIdB');
      img.src = e.target.result; img.style.display = 'block';
    }
    triggerIdCardMerge();
  };
  reader.readAsDataURL(file);
}

function onCropRangeChange(val) {
  currentCropPct = val / 100.0;
  document.getElementById('cropValText').innerText = '切除四周边缘 ' + val + '%';
  triggerIdCardMerge();
}

function clearIdCards() {
  idFrontFile = null; idBackFile = null; idCardMergedBlob = null;
  document.getElementById('imgIdF').style.display = 'none';
  document.getElementById('imgIdB').style.display = 'none';
  document.getElementById('fiIdF').value = '';
  document.getElementById('fiIdB').value = '';
  clearPreview();
}

function triggerIdCardMerge() {
  if (!idFrontFile || !idBackFile) return;
  var tip = document.getElementById('phTip');
  tip.innerHTML = '<div style="font-size:28px">🔍</div><div>正在剔除背景并按真实 1:1 比例合成...</div>';
  tip.style.display = 'block';
  document.getElementById('prevImg').style.display = 'none';
  document.getElementById('pdfWrap').style.display = 'none';

  var fd = new FormData();
  fd.append('front', idFrontFile);
  fd.append('back', idBackFile);
  fd.append('crop_pct', currentCropPct);

  fetch('/api/merge_idcard', { method: 'POST', body: fd }).then(function(res) { return res.blob(); }).then(function(blob) {
    idCardMergedBlob = blob;
    showBlobPreview(blob, '身份证1比1去杂边排版.pdf');
  }).catch(function(e) { alert('身份证排版生成异常: ' + e); });
}

function clearPreview() {
  document.getElementById('prevImg').style.display = 'none';
  document.getElementById('pdfWrap').style.display = 'none';
  document.getElementById('phTip').style.display = 'block';
  document.getElementById('prevName').innerText = '';
  document.getElementById('extPrevLink').style.display = 'none';
}

function showFilePreview(f) {
  var tip = document.getElementById('phTip');
  tip.style.display = 'none';
  document.getElementById('prevName').innerText = '— ' + f.name;
  var url = URL.createObjectURL(f);
  var ext = document.getElementById('extPrevLink');
  ext.href = url; ext.style.display = 'inline-block';

  if (f.name.toLowerCase().indexOf('.pdf') !== -1) {
    document.getElementById('prevImg').style.display = 'none';
    var wrap = document.getElementById('pdfWrap');
    wrap.innerHTML = '<embed src="' + url + '" type="application/pdf" width="100%" height="100%" />';
    wrap.style.display = 'block';
  } else {
    document.getElementById('pdfWrap').style.display = 'none';
    var img = document.getElementById('prevImg');
    var reader = new FileReader();
    reader.onload = function(e) { img.src = e.target.result; img.style.display = 'block'; };
    reader.readAsDataURL(f);
  }
}

function showBlobPreview(blob, title) {
  document.getElementById('phTip').style.display = 'none';
  document.getElementById('prevImg').style.display = 'none';
  document.getElementById('prevName').innerText = '— ' + title;
  var url = URL.createObjectURL(blob);
  var ext = document.getElementById('extPrevLink');
  ext.href = url; ext.style.display = 'inline-block';
  var wrap = document.getElementById('pdfWrap');
  wrap.innerHTML = '<embed src="' + url + '" type="application/pdf" width="100%" height="100%" />';
  wrap.style.display = 'block';
}

function setOpt(k, v) {
  cfg[k] = v;
  if (k === 'c') {
    document.getElementById('bCol').className = (v === 'color' ? 'pill act' : 'pill');
    document.getElementById('bGray').className = (v === 'gray' ? 'pill act' : 'pill');
  } else {
    document.getElementById('bPor').className = (v === 'portrait' ? 'pill act' : 'pill');
    document.getElementById('bLan').className = (v === 'landscape' ? 'pill act' : 'pill');
    var sheet = document.getElementById('paperSheet');
    sheet.style.width = (v === 'portrait' ? '220px' : '311px');
    sheet.style.height = (v === 'portrait' ? '311px' : '220px');
  }
}

function syncP() {
  var sel = document.getElementById('selP');
  var p = null;
  for (var i = 0; i < pData.length; i++) { if (pData[i].name === sel.value) { p = pData[i]; break; } }
  if (!p) return;
  document.getElementById('stM').innerText = p.model || p.name;
  document.getElementById('stB').innerText = p.status;
  document.getElementById('stB').className = 'badge badge-' + p.status_type;
  document.getElementById('stJ').innerText = p.jobs;
}

function loadPrinters() {
  fetch('/api/printers?_t=' + Date.now()).then(function(res) { return res.json(); }).then(function(r) {
    pData = r.printers || [];
    var sel = document.getElementById('selP');
    sel.innerHTML = '';
    if (!pData.length) {
      sel.innerHTML = '<option value="">(未检测到打印机，请点击右上角添加驱动)</option>';
      document.getElementById('stM').innerText = '未检测到设备';
      document.getElementById('stB').innerText = '无设备';
      return;
    }
    for (var i = 0; i < pData.length; i++) {
      var opt = document.createElement('option');
      opt.value = pData[i].name;
      opt.innerText = pData[i].display_name + ' [' + pData[i].status + ']';
      if (pData[i].name === r.default) opt.selected = true;
      sel.appendChild(opt);
    }
    syncP();
    if (r.uptime) document.getElementById('stU').innerText = r.uptime;
  }).catch(function(e) {});
}

function loadHistory() {
  fetch('/api/history?_t=' + Date.now()).then(function(res) { return res.json(); }).then(function(h) {
    var box = document.getElementById('hlist');
    if (!h.length) { box.innerHTML = '<div style="color:var(--mu);text-align:center;padding:12px">暂无记录</div>'; return; }
    var html = '';
    for (var i = 0; i < h.length; i++) {
      html += '<div class="st-row"><div><b>' + h[i].filename + '</b><div style="font-size:11px;color:var(--mu)">' + h[i].printer + ' · ' + h[i].time + '</div></div><span class="badge badge-idle">' + h[i].status + '</span></div>';
    }
    box.innerHTML = html;
  }).catch(function(e) {});
}

function submitPrint() {
  var sel = document.getElementById('selP');
  if (!sel.value) return alert('请先选择打印机！');
  var btn = document.getElementById('submitBtn');

  var fd = new FormData();
  fd.append('printer', sel.value);
  fd.append('color', cfg.c);
  fd.append('orient', cfg.o);
  fd.append('copies', document.getElementById('cop').value);
  fd.append('media', document.getElementById('med').value);
  fd.append('mode', currentMode);

  if (currentMode === 'std') {
    if (!filesStd.length) return alert('请先选择标准打印文件！');
    for (var i = 0; i < filesStd.length; i++) fd.append('files', filesStd[i]);
    fd.append('auto_whiten', document.getElementById('chkWhiten').checked ? 'true' : 'false');
    fd.append('auto_deskew', document.getElementById('chkDeskew').checked ? 'true' : 'false');
  } else if (currentMode === 'inv') {
    if (!filesInv.length) return alert('请先选择发票文件！');
    for (var i = 0; i < filesInv.length; i++) fd.append('files', filesInv[i]);
  } else if (currentMode === 'id') {
    if (!idCardMergedBlob) return alert('请先上传身份证正反面！');
    fd.append('files', idCardMergedBlob, '身份证正反面排版.pdf');
  }

  btn.disabled = true; btn.innerText = '⏳ 正在出纸...';
  fetch('/api/print', { method: 'POST', body: fd }).then(function(res) { return res.json(); }).then(function(ret) {
    if (ret.code === 0) {
      alert('🎉 打印任务已成功发送至打印机！');
      if (currentMode === 'std') { filesStd = []; renderFileList('std'); }
      else if (currentMode === 'inv') { filesInv = []; renderFileList('inv'); }
      else if (currentMode === 'id') { clearIdCards(); }
      loadHistory(); loadPrinters();
    } else {
      alert('出纸失败: ' + ret.msg);
    }
  }).catch(function(err) {
    alert('请求异常: ' + err);
  }).finally(function() {
    btn.disabled = false; btn.innerText = '🚀 开始打印';
  });
}

function openModal() { document.getElementById('mD').style.display = 'flex'; scanDevs(); loadEp(); }
function closeModal() { document.getElementById('mD').style.display = 'none'; }
function togPpd() {
  var isUpload = document.querySelector('input[name="pm"]:checked').value === 'u';
  document.getElementById('ePpd').style.display = isUpload ? 'none' : 'block';
  document.getElementById('uPpd').style.display = isUpload ? 'block' : 'none';
}

function scanDevs() {
  var devs = document.getElementById('devs');
  devs.innerHTML = '<option>扫描物理硬件中...</option>';
  fetch('/api/scan_devices?_t=' + Date.now()).then(function(res) { return res.json(); }).then(function(l) {
    devs.innerHTML = '';
    if (!l.length) { devs.innerHTML = '<option value="">未检测到物理设备(请检查USB)</option>'; return; }
    for (var i = 0; i < l.length; i++) {
      var opt = document.createElement('option');
      opt.value = l[i].uri; opt.innerText = l[i].name; devs.appendChild(opt);
      if (i === 0) {
        document.getElementById('devUri').value = l[i].uri;
        document.getElementById('pName').value = l[i].name.replace(/[^a-zA-Z0-9_]/g, '_');
      }
    }
  }).catch(function(e) {});
}

function loadEp() {
  var ep = document.getElementById('ePpd');
  ep.innerHTML = '<option>加载驱动库...</option>';
  fetch('/api/installed_ppds?_t=' + Date.now()).then(function(res) { return res.json(); }).then(function(l) {
    ep.innerHTML = '';
    if (!l.length) { ep.innerHTML = '<option value="">(暂无可复用驱动，请选上传PPD)</option>'; return; }
    for (var i = 0; i < l.length; i++) {
      var opt = document.createElement('option');
      opt.value = l[i].path; opt.innerText = '📄 ' + l[i].name; ep.appendChild(opt);
    }
  }).catch(function(e) {});
}

function submitAddP() {
  var name = document.getElementById('pName').value.trim();
  var uri = document.getElementById('devUri').value.trim();
  if (!name || !uri) return alert('请先填写名称和URI！');
  var btn = document.getElementById('addBtn');
  btn.disabled = true; btn.innerText = '⏳ 正在绑定驱动...';

  var fd = new FormData();
  fd.append('name', name);
  fd.append('uri', uri);
  var isUpload = document.querySelector('input[name="pm"]:checked').value === 'u';
  if (isUpload && document.getElementById('uPpd').files.length) {
    fd.append('ppd', document.getElementById('uPpd').files[0]);
  } else {
    fd.append('ppd_path', document.getElementById('ePpd').value);
  }

  fetch('/api/add_printer', { method: 'POST', body: fd }).then(function(res) { return res.json(); }).then(function(r) {
    if (r.code === 0) {
      alert('🎉 驱动绑定并启用成功！');
      closeModal(); loadPrinters();
    } else {
      alert('绑定失败: ' + r.msg);
    }
  }).catch(function(e) { alert('请求异常: ' + e); }).finally(function() {
    btn.disabled = false; btn.innerText = '🚀 立即绑定驱动';
  });
}

function scanHardwareScanners() {
  var sel = document.getElementById('scannerSelect');
  sel.innerHTML = '<option>探测中...</option>';
  fetch('/api/sane_scanners?_t=' + Date.now()).then(function(res) { return res.json(); }).then(function(list) {
    sel.innerHTML = '';
    if (!list.length) { sel.innerHTML = '<option value="">未找到扫描仪(请检查USB)</option>'; return; }
    for (var i = 0; i < list.length; i++) {
      var opt = document.createElement('option');
      opt.value = list[i].device; opt.innerText = list[i].name + ' (' + list[i].device + ')'; sel.appendChild(opt);
    }
  }).catch(function(e) {});
}

function doStartScan() {
  var dev = document.getElementById('scannerSelect').value;
  if (!dev) return alert('未选择扫描仪设备！');
  var btn = document.getElementById('startScanBtn');
  btn.disabled = true; btn.innerText = '⏳ 正在扫描中 (约15-25秒)...';

  var fd = new FormData();
  fd.append('device', dev);
  fd.append('resolution', document.getElementById('scanResolution').value);
  fd.append('mode', document.getElementById('scanMode').value);
  
  fetch('/api/scan_job', { method: 'POST', body: fd }).then(function(res) { return res.json(); }).then(function(ret) {
    if (ret.code === 0) {
      currentScanFile = ret.filename;
      alert('🎉 扫描成功！');
      document.getElementById('scanToolbar').style.display = 'flex';
      document.getElementById('scanDownloadBtn').href = ret.url;
      document.getElementById('scanTip').style.display = 'none';

      var pimg = document.getElementById('scanPrevImg');
      var pwrap = document.getElementById('scanPdfWrap');
      if (ret.url.toLowerCase().indexOf('.pdf') !== -1) {
        pimg.style.display = 'none';
        pwrap.innerHTML = '<embed src="' + ret.url + '" type="application/pdf" width="100%" height="100%" />';
        pwrap.style.display = 'block';
      } else {
        pwrap.style.display = 'none';
        pimg.src = ret.url + '?_t=' + Date.now();
        pimg.style.display = 'block';
      }
      loadScanFiles();
    } else {
      alert('扫描失败: ' + ret.msg);
    }
  }).catch(function(e) { alert('请求异常: ' + e); }).finally(function() {
    btn.disabled = false; btn.innerText = '🚀 开始扫描并生成文件';
  });
}

function printCurrentScan() {
  if (!currentScanFile) return alert('无扫描文件');
  var printer = document.getElementById('selP').value;
  if (!confirm('确认使用 [' + (printer || '默认设备') + '] 复印该文件吗？')) return;
  var fd = new FormData();
  fd.append('scan_file', currentScanFile);
  fd.append('printer', printer);
  fetch('/api/print_scan_file', { method: 'POST', body: fd }).then(function(res) { return res.json(); }).then(function(r) {
    if (r.code === 0) { alert('🎉 已提交复印！'); loadHistory(); } else { alert('失败: ' + r.msg); }
  });
}

function loadScanFiles() {
  fetch('/api/scan_files?_t=' + Date.now()).then(function(res) { return res.json(); }).then(function(l) {
    var box = document.getElementById('scanFileList');
    if (!l.length) { box.innerHTML = '<div style="color:var(--mu);text-align:center;padding:12px">暂无文件</div>'; return; }
    var html = '';
    for (var i = 0; i < l.length; i++) {
      html += '<div class="st-row"><a href="' + l[i].url + '" target="_blank">' + l[i].name + '</a><a href="' + l[i].url + '" download class="btn btn-o">下载</a></div>';
    }
    box.innerHTML = html;
  }).catch(function(e) {});
}

function loadAll() { loadPrinters(); loadHistory(); loadScanFiles(); }
loadAll();
setInterval(loadPrinters, 4000);
</script></body></html>'''

class MainH(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html; charset=UTF-8")
        self.set_header("Cache-Control", "no-cache, no-store")
        self.write(HTML)

class PrnH(tornado.web.RequestHandler):
    def get(self):
        r, d = [], ""
        env = get_clean_env()
        try:
            dp = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, env=env, timeout=3)
            m_def = re.search(r"(destination|目标)[:：]\s*(\S+)", dp.stdout, re.IGNORECASE)
            if m_def: d = m_def.group(2).strip()

            ps = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, env=env, timeout=3)
            for l in ps.stdout.splitlines():
                l_str = l.strip()
                m_p = re.match(r"^(?:printer|打印机)\s+([^\s:]+)", l_str, re.IGNORECASE)
                if m_p:
                    n = m_p.group(1).strip()
                    model = get_printer_model(n)
                    disp = "%s (%s)" % (n, model) if model and model != n else n
                    st, stype, j = diagnose_printer(n)
                    r.append({"name": n, "model": model or n, "display_name": disp, "status": st, "status_type": stype, "jobs": j})
        except Exception:
            pass
        self.write(json.dumps({"printers": r, "default": d, "uptime": get_uptime_str()}))

class DevsH(tornado.web.RequestHandler):
    def get(self):
        devs = []
        try:
            r = subprocess.run(["lpinfo", "-v"], capture_output=True, text=True, env=get_clean_env(), timeout=5)
            for l in r.stdout.splitlines():
                parts = l.split(maxsplit=1)
                if len(parts) == 2 and any(parts[1].startswith(p) for p in ["usb://", "ipp://", "dnssd://", "socket://"]):
                    devs.append({"name": parts[1].split("/")[-1].replace("%20", " "), "uri": parts[1].strip()})
        except Exception:
            pass
        self.write(json.dumps(devs))

class PpdH(tornado.web.RequestHandler):
    def get(self):
        ppd_list = []
        seen = set()
        search_dirs = [PPD_DIR, "/usr/share/cups/model", "/usr/share/ppd"]
        for sdir in search_dirs:
            if not os.path.exists(sdir): continue
            for root, _, files in os.walk(sdir):
                for f in files:
                    if f.endswith(".ppd") or f.endswith(".ppd.gz"):
                        fp = os.path.join(root, f)
                        bname = os.path.splitext(f)[0]
                        if bname not in seen:
                            seen.add(bname)
                            ppd_list.append({"name": bname, "path": fp})
        
        if len(ppd_list) < 5:
            try:
                res = subprocess.run(["lpinfo", "-m"], capture_output=True, text=True, env=get_clean_env(), timeout=3)
                for l in res.stdout.splitlines():
                    parts = l.split(maxsplit=1)
                    if len(parts) == 2 and parts[0] not in seen:
                        seen.add(parts[0])
                        ppd_list.append({"name": parts[1].strip(), "path": parts[0].strip()})
            except Exception:
                pass
        self.write(json.dumps(ppd_list[:150]))

class AddH(tornado.web.RequestHandler):
    def post(self):
        n = re.sub(r'[^a-zA-Z0-9_-]', '_', self.get_argument("name", "").strip())
        u = self.get_argument("uri", "").strip()
        ppd_path = self.get_argument("ppd_path", "").strip()
        if not n or not u: return self.write(json.dumps({"code": 1, "msg": "参数缺失"}))

        target_ppd = os.path.join(PPD_DIR, "%s.ppd" % n)
        used_ppd_flag = None

        if 'ppd' in self.request.files:
            with open(target_ppd, 'wb') as f: f.write(self.request.files['ppd'][0]['body'])
            used_ppd_flag = target_ppd
        elif ppd_path:
            if ppd_path.endswith(".ppd") and os.path.exists(ppd_path): used_ppd_flag = ppd_path
            else: used_m_flag = ppd_path

        cmd = ["lpadmin", "-p", n, "-E", "-v", u]
        if used_ppd_flag: cmd.extend(["-P", used_ppd_flag])
        elif 'used_m_flag' in locals() and used_m_flag: cmd.extend(["-m", used_m_flag])
        else: cmd.extend(["-m", "everywhere"])

        try:
            env = get_clean_env()
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=12)
            if res.returncode == 0:
                subprocess.run(["cupsaccept", n], env=env)
                subprocess.run(["cupsenable", n], env=env)
                subprocess.run(["lpadmin", "-d", n], env=env)
                self.write(json.dumps({"code": 0}))
            else:
                self.write(json.dumps({"code": 1, "msg": res.stderr.strip() or res.stdout.strip()}))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": str(e)}))

class MergeIdCardH(tornado.web.RequestHandler):
    def post(self):
        front_file = self.request.files.get('front', [None])[0]
        back_file = self.request.files.get('back', [None])[0]
        crop_pct = float(self.get_argument('crop_pct', '0.06'))
        if not front_file or not back_file:
            self.set_status(400)
            return self.write("正反面缺失")

        a4_w, a4_h = 2480, 3508
        canvas = Image.new('RGB', (a4_w, a4_h), (255, 255, 255))
        card_w, card_h = 1011, 638

        im_front = smart_crop_card(front_file['body'], crop_pct=crop_pct, target_w=card_w, target_h=card_h)
        im_back = smart_crop_card(back_file['body'], crop_pct=crop_pct, target_w=card_w, target_h=card_h)

        x = (a4_w - card_w) // 2
        y1 = 650
        y2 = y1 + card_h + 260

        canvas.paste(im_front, (x, y1))
        canvas.paste(im_back, (x, y2))

        out_io = io.BytesIO()
        canvas.save(out_io, 'PDF', resolution=300.0)
        self.set_header('Content-Type', 'application/pdf')
        self.write(out_io.getvalue())

class HistoryH(tornado.web.RequestHandler):
    def get(self):
        self.write(json.dumps(PRINT_HISTORY))

class PrintH(tornado.web.RequestHandler):
    def post(self):
        files = self.request.files.get('files', [])
        if not files: return self.write(json.dumps({"code":1,"msg":"无文件"}))
        p = self.get_argument("printer", "")
        copies = self.get_argument("copies", "1")
        media = self.get_argument("media", "A4")
        orient = self.get_argument("orient", "portrait")
        color = self.get_argument("color", "color")
        mode = self.get_argument("mode", "std")
        auto_whiten = (self.get_argument("auto_whiten", "false") == "true")
        auto_deskew = (self.get_argument("auto_deskew", "false") == "true")
        
        env = get_clean_env()
        succ = 0
        last_err = ""
        for f_obj in files:
            fname = f_obj['filename']
            fp = os.path.join(UPLOAD_DIR, fname)
            
            is_img = any(fname.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.bmp', '.webp'])
            if mode == "std" and is_img and (auto_whiten or auto_deskew) and HAS_OPENCV:
                pil_im = process_smart_image(f_obj['body'], auto_whiten=auto_whiten, auto_deskew=auto_deskew)
                pil_im.save(fp, "JPEG", quality=95)
            else:
                with open(fp, "wb") as f: f.write(f_obj['body'])

            cmd = ["lp"]
            if p: cmd.extend(["-d", p])
            cmd.extend(["-n", str(copies), "-o", "media=%s" % media])
            
            if mode == "inv":
                cmd.extend(["-o", "fit-to-page", "-o", "position=center"])
            elif mode == "id":
                cmd.extend(["-o", "scaling=100", "-o", "position=center"])
            else:
                cmd.extend(["-o", "fit-to-page"])

            if orient == "landscape": cmd.extend(["-o", "orientation-requested=4"])
            if color == "gray": cmd.extend(["-o", "ColorModel=Gray"])
            cmd.append(fp)
            
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
            if res.returncode == 0:
                succ += 1
                tag = "发票" if mode == "inv" else ("身份证" if mode == "id" else "标准")
                PRINT_HISTORY.insert(0, {"filename": "[%s] %s" % (tag, fname), "printer": p or "默认", "time": time.strftime("%H:%M"), "status": "已出纸"})
            else:
                last_err = res.stderr.strip()
        
        if succ > 0: self.write(json.dumps({"code": 0}))
        else: self.write(json.dumps({"code": 1, "msg": last_err or "出纸失败"}))

class SaneH(tornado.web.RequestHandler):
    def get(self):
        scanners = []
        try:
            env = get_clean_env()
            res = subprocess.run(["scanimage", "-L"], capture_output=True, text=True, env=env, timeout=10)
            for line in res.stdout.splitlines():
                m = re.search(r"device `([^']+)' is a (.*)", line)
                if m: scanners.append({"device": m.group(1).strip(), "name": m.group(2).strip()})
        except Exception: pass
        self.write(json.dumps(scanners))

class ScanJobH(tornado.web.RequestHandler):
    def post(self):
        dev = self.get_argument("device", "").strip()
        resolution = self.get_argument("resolution", "300")
        mode = self.get_argument("mode", "Color")

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        os.makedirs(SCAN_DIR, exist_ok=True)
        t = time.strftime("%Y%m%d_%H%M%S")
        target_name = "Scan_%s.pdf" % t
        target = os.path.join(SCAN_DIR, target_name)
        raw = os.path.join(UPLOAD_DIR, "raw_%s.tiff" % t)
        env = get_clean_env()
        cmd = ["scanimage", "--format=tiff", "--resolution=%s" % resolution, "--mode=%s" % mode, "-o", raw]
        if dev: cmd.extend(["-d", dev])
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
            if res.returncode != 0 or not os.path.exists(raw) or os.path.getsize(raw) == 0:
                return self.write(json.dumps({"code": 1, "msg": res.stderr.strip() or "扫描仪响应超时"}))
            with Image.open(raw) as im: im.convert("RGB").save(target, "PDF", resolution=float(resolution))
            if os.path.exists(raw): os.remove(raw)
            self.write(json.dumps({"code": 0, "filename": target_name, "url": "/scans/%s" % target_name}))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": str(e)}))

class PrintScanFileH(tornado.web.RequestHandler):
    def post(self):
        fn = self.get_argument("scan_file", "").strip()
        printer = self.get_argument("printer", "").strip()
        fp = os.path.join(SCAN_DIR, fn)
        if not os.path.exists(fp): return self.write(json.dumps({"code": 1, "msg": "文件不存在"}))
        cmd = ["lp"]
        if printer: cmd.extend(["-d", printer])
        cmd.extend(["-n", "1", "-o", "media=A4", "-o", "fit-to-page", fp])
        try:
            env = get_clean_env()
            subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=25)
            self.write(json.dumps({"code": 0}))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": str(e)}))

class ScanFilesH(tornado.web.RequestHandler):
    def get(self):
        l = []
        if os.path.exists(SCAN_DIR):
            for fn in sorted(os.listdir(SCAN_DIR), reverse=True):
                if not fn.startswith('.'):
                    l.append({"name": fn, "url": "/scans/%s" % fn})
        self.write(json.dumps(l))

class InlineStaticFileHandler(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        self.set_header("Content-Disposition", "inline")

def make_app():
    return tornado.web.Application([
        (r"/?", MainH),
        (r"/api/printers", PrnH),
        (r"/api/scan_devices", DevsH),
        (r"/api/installed_ppds", PpdH),
        (r"/api/add_printer", AddH),
        (r"/api/merge_idcard", MergeIdCardH),
        (r"/api/history", HistoryH),
        (r"/api/print", PrintH),
        (r"/api/sane_scanners", SaneH),
        (r"/api/scan_job", ScanJobH),
        (r"/api/print_scan_file", PrintScanFileH),
        (r"/api/scan_files", ScanFilesH),
        (r"/scans/(.*)", InlineStaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(PORT, address="0.0.0.0")
    tornado.ioloop.IOLoop.current().start()
