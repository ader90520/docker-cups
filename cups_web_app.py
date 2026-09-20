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
PPD_DIR = "/etc/cups/ppd"
SCAN_DIR = os.getenv("SCAN_DIR", "/scans")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PPD_DIR, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)

PRINT_HISTORY = []

def get_system_uptime_str():
    """获取系统运行时间（如：78天7小时）"""
    try:
        with open('/proc/uptime', 'r') as f:
            total_seconds = float(f.readline().split()[0])
            days = int(total_seconds // 86400)
            hours = int((total_seconds % 86400) // 3600)
            if days > 0:
                return f"{days}天{hours}小时"
            minutes = int((total_seconds % 3600) // 60)
            return f"{hours}小时{minutes}分钟"
    except Exception:
        return "1小时内"

def diagnose_printer_detail(p_name):
    """
    深度诊断打印机：
    识别 空闲 / 打印中 / 卡纸 / 缺纸 / 缺墨 / 机盖打开 / 脱机
    """
    status_text = "空闲"
    status_type = "idle"  # idle | busy | warn | error

    try:
        env = dict(os.environ, LC_ALL="C")
        res = subprocess.run(["lpstat", "-l", "-p", p_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=3)
        raw = (res.stdout + res.stderr).lower()

        if any(k in raw for k in ["media-jam", "paper jam", "jam"]):
            status_text = "卡纸"
            status_type = "error"
        elif any(k in raw for k in ["media-empty", "out of paper", "paper empty"]):
            status_text = "缺纸"
            status_type = "error"
        elif any(k in raw for k in ["toner-empty", "marker-supply-empty", "ink empty", "toner low", "ink low", "cartridge"]):
            status_text = "缺墨"
            status_type = "warn"
        elif any(k in raw for k in ["door-open", "cover open", "cover-open"]):
            status_text = "机盖打开"
            status_type = "warn"
        elif any(k in raw for k in ["offline", "not connected"]):
            status_text = "脱机"
            status_type = "error"
        elif "disabled" in raw:
            status_text = "已暂停"
            status_type = "warn"
        elif "is printing" in raw or "printing" in raw:
            status_text = "打印中"
            status_type = "busy"
        else:
            status_text = "空闲"
            status_type = "idle"
    except Exception:
        status_text = "就绪"
        status_type = "idle"

    # 统计排队作业数
    job_count = 0
    try:
        env = dict(os.environ, LC_ALL="C")
        res_o = subprocess.run(["lpstat", "-o", p_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
        lines = [line.strip() for line in res_o.stdout.splitlines() if line.strip()]
        job_count = len(lines)
    except Exception:
        job_count = 0

    return status_text, status_type, job_count

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
                s_text, s_type, job_cnt = diagnose_printer_detail(p_name)
                printers.append({
                    "name": p_name,
                    "status": s_text,
                    "status_type": s_type,
                    "jobs": job_cnt
                })
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
            --blue: #0284c7;
            --danger: #ef4444;
            --danger-hover: #dc2626;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text-main); font-size: 14px; }
        .navbar { background: #fff; border-bottom: 1px solid var(--border); padding: 12px 28px; display: flex; justify-content: space-between; align-items: center; }
        .navbar-brand { font-size: 18px; font-weight: 700; color: #0f172a; display: flex; align-items: center; gap: 8px; }
        .navbar-brand span { font-size: 13px; font-weight: normal; color: var(--text-muted); }
        .nav-links { display: flex; gap: 10px; }
        .nav-btn { text-decoration: none; padding: 6px 14px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; border: 1px solid transparent; display: flex; align-items: center; gap: 4px; }
        .nav-btn-primary { background: var(--primary); color: #fff; }
        .nav-btn-blue { background: var(--blue); color: #fff; }
        .nav-btn-outline { border-color: var(--border); background: #fff; color: var(--text-main); }
        
        .container { max-width: 1280px; margin: 24px auto; padding: 0 20px; display: grid; grid-template-columns: 1.55fr 1fr; gap: 24px; }
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
        
        /* 拖拽区域 */
        .upload-zone { border: 2px dashed #cbd5e1; border-radius: 8px; padding: 20px 16px; text-align: center; cursor: pointer; background: #f8fafc; margin-bottom: 16px; transition: all 0.2s ease; }
        .upload-zone:hover { border-color: var(--primary); background: #f0fdf4; }
        .upload-zone.dragover { border-color: var(--primary); background: #dcfce7; transform: scale(1.01); }
        
        /* 文件信息与删除按钮样式 */
        .upload-info { display: flex; align-items: center; justify-content: space-between; background: #f0fdf4; border: 1px solid #bbf7d0; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
        .btn-delete-file { background: #fee2e2; color: var(--danger); border: 1px solid #fca5a5; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 4px; transition: all 0.2s; }
        .btn-delete-file:hover { background: var(--danger); color: #fff; }

        /* 仿真纸张打印预览区域 */
        .preview-container { background: #e2e8f0; border-radius: 8px; padding: 20px; display: flex; justify-content: center; align-items: center; min-height: 250px; overflow: hidden; }
        .paper-sheet { background: #ffffff; box-shadow: 0 4px 14px rgba(0,0,0,0.12); border-radius: 4px; display: flex; justify-content: center; align-items: center; overflow: hidden; transition: all 0.3s ease; padding: 8px; box-sizing: border-box; }
        .paper-portrait { width: 200px; height: 283px; }
        .paper-landscape { width: 283px; height: 200px; }
        .preview-img { max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 2px; }
        .doc-placeholder { text-align: center; color: var(--text-muted); }
        .doc-placeholder .icon { font-size: 38px; margin-bottom: 8px; }

        /* 状态卡片中的指标行 */
        .status-item { display: flex; justify-content: space-between; align-items: center; padding: 12px 14px; background: #f8fafc; border-radius: 6px; margin-bottom: 10px; font-size: 13px; border: 1px solid #edf2f7; }
        .status-item-left { display: flex; align-items: center; gap: 8px; color: #334155; }
        .status-val { font-weight: 600; font-size: 14px; color: #0f172a; }

        /* 状态徽标样式 */
        .badge { font-size: 12px; padding: 3px 10px; border-radius: 12px; font-weight: 600; }
        .badge-idle { background: #dcfce7; color: #15803d; }     /* 空闲 */
        .badge-busy { background: #e0f2fe; color: #0369a1; }     /* 打印中 */
        .badge-warn { background: #fef9c3; color: #854d0e; }     /* 缺墨/开盖 */
        .badge-error { background: #fee2e2; color: #b91c1c; }    /* 卡纸/缺纸/脱机 */

        /* 纸盒槽条目 */
        .tray-box { background: #f8fafc; border: 1px solid #edf2f7; border-radius: 6px; padding: 8px 12px; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; font-size: 12px; color: #475569; }
        
        .btn-submit { width: 100%; height: 44px; background: var(--primary); color: white; border: none; border-radius: 6px; font-size: 15px; font-weight: 600; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px; }
        .btn-submit:hover { background: var(--primary-hover); }

        .record-item { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; background: #fff; display: flex; justify-content: space-between; align-items: center; }
        .record-title { font-weight: 600; font-size: 13px; color: #1e293b; margin-bottom: 2px; max-width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .record-sub { font-size: 11px; color: var(--text-muted); }

        /* 模态弹窗 */
        .modal-mask { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.4); display: none; justify-content: center; align-items: center; z-index: 1000; }
        .modal-box { background: #fff; width: 500px; max-width: 90%; border-radius: 12px; padding: 24px; box-shadow: 0 10px 25px rgba(0,0,0,0.1); }
        .modal-title { font-size: 17px; font-weight: 700; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }
        .modal-close { cursor: pointer; font-size: 20px; color: var(--text-muted); border: none; background: none; }
    </style>
</head>
<body>
    <header class="navbar">
        <div class="navbar-brand">🖨️ CUPS 智能打印控制台 <span>admin</span></div>
        <div class="nav-links">
            <button class="nav-btn nav-btn-blue" onclick="openDriverModal()">➕ 添加打印机驱动</button>
            <button class="nav-btn nav-btn-primary" onclick="loadPrinters(); loadHistory();">🔄 刷新</button>
            <a href="http://" + location.hostname + ":631" target="_blank" class="nav-btn nav-btn-outline" id="cupsLink">⚙️ 原生后台 (631)</a>
        </div>
    </header>

    <main class="container">
        <!-- 左侧：参数与预览提交 -->
        <section>
            <div class="card">
                <div class="card-header">
                    <span>🖨️ 目标打印机与文档</span>
                    <a href="javascript:void(0)" onclick="openDriverModal()" style="font-size: 12px; color: var(--blue); text-decoration: none; font-weight: 500;">➕ 添加/安装驱动</a>
                </div>
                <div class="form-group" style="margin-bottom: 16px;">
                    <label class="form-label">选择打印机</label>
                    <select id="printerSelect" class="form-control" onchange="syncSelectedPrinter()"></select>
                </div>
                
                <div class="upload-zone" id="dropZone">
                    <input type="file" id="fileInput" style="display: none;">
                    <div style="font-size: 30px; margin-bottom: 6px;">📄</div>
                    <div style="font-weight: 600; color: #334155;">点击或将微信文件/照片直接拖入此处</div>
                    <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">支持 PDF, Word, Excel, JPG, PNG（自动纠偏去黑底）</div>
                </div>

                <!-- 选定文件提示卡片：增加删除/清空按钮 -->
                <div id="fileInfoBox" class="upload-info" style="display: none;">
                    <div>
                        <span id="fileNameDisplay" style="font-weight: 600; color: #15803d;"></span>
                        <span id="fileSizeDisplay" style="color: var(--text-muted); margin-left: 8px;"></span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span style="color: var(--primary); font-weight: 600;">✓ 已就绪</span>
                        <button type="button" class="btn-delete-file" onclick="clearSelectedFile()" title="点击移除已选文件">❌ 删除</button>
                    </div>
                </div>
            </div>

            <!-- 打印参数核心卡片 -->
            <div class="card">
                <div class="card-header"><span>⚲ 打印参数</span></div>
                
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
                            <option value="two-sided-long-edge">双面打印 (长边翻转)</option>
                            <option value="two-sided-short-edge">双面打印 (短边翻转)</option>
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
                            <option value="A6">A6 (105×148mm)</option>
                            <option value="B5">B5 (182×257mm)</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label">纸张类型</label>
                        <select id="paperTypeSelect" class="form-control">
                            <option value="plain">普通纸</option>
                            <option value="photo">相片纸</option>
                            <option value="heavy">厚纸</option>
                        </select>
                    </div>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label class="form-label">缩放</label>
                        <select id="scaleSelect" class="form-control">
                            <option value="fit-to-page">适应纸张</option>
                            <option value="actual">实际大小 (100%)</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label">页面范围</label>
                        <input type="text" id="pageRangeInput" class="form-control" placeholder="留空=全部，如: 1-5 8">
                    </div>
                </div>

                <div class="form-group" style="margin-bottom: 16px;">
                    <label class="form-label">镜像打印</label>
                    <label style="display: flex; align-items: center; gap: 8px; font-size: 13px; color: #475569; cursor: pointer;">
                        <input type="checkbox" id="mirrorCheckbox" style="width: 16px; height: 16px; accent-color: var(--primary);">
                        <span>[⇋] 水平镜像翻转</span>
                    </label>
                </div>

                <!-- 仿真纸张打印预览卡片 -->
                <div class="form-group" style="margin-bottom: 20px;">
                    <label class="form-label">👀 仿真纸张打印预览</label>
                    <div class="preview-container">
                        <div class="paper-sheet paper-portrait" id="paperSheet">
                            <div class="doc-placeholder" id="previewPlaceholder">
                                <div class="icon">🖼️</div>
                                <div style="font-size: 13px;">选择或拖入图片查看排版</div>
                            </div>
                            <img id="previewImg" class="preview-img" style="display: none;" alt="预览图">
                        </div>
                    </div>
                </div>

                <button class="btn-submit" onclick="submitPrintJob()">
                    <span>🖨️ 提交打印</span>
                </button>
            </div>
        </section>

        <!-- 右侧：打印机状态卡片 -->
        <section>
            <div class="card">
                <div class="card-header">
                    <span>📈 打印机状态</span>
                    <button class="nav-btn nav-btn-outline" style="padding: 2px 8px; font-size: 11px;" onclick="loadPrinters()">🔄 刷新</button>
                </div>

                <!-- 1. 打印机状态徽标 -->
                <div class="status-item">
                    <div class="status-item-left">
                        <span>ℹ️</span>
                        <span>打印机状态</span>
                    </div>
                    <span class="badge badge-idle" id="curStatusBadge">空闲</span>
                </div>

                <!-- 2. 队列任务数 -->
                <div class="status-item">
                    <div class="status-item-left">
                        <span>📊</span>
                        <span>队列任务数</span>
                    </div>
                    <span class="status-val" id="queueJobCount">0</span>
                </div>

                <!-- 3. 状态持续时间 -->
                <div class="status-item">
                    <div class="status-item-left">
                        <span>🕒</span>
                        <span>状态持续</span>
                    </div>
                    <span class="status-val" id="uptimeDisplay" style="font-size: 13px; font-weight: normal; color: #475569;">计算中...</span>
                </div>

                <!-- 4. 纸盒信息明细 -->
                <div style="margin-top: 14px;">
                    <div style="font-weight: 600; font-size: 13px; margin-bottom: 8px; color: #334155; display: flex; align-items: center; gap: 6px;">
                        <span>📚 纸盒信息</span>
                    </div>
                    <div class="tray-box">
                        <input type="checkbox" checked disabled>
                        <span>iso_a4_210x297mm (默认标准进纸)</span>
                    </div>
                    <div class="tray-box">
                        <input type="checkbox" disabled>
                        <span>iso_a6_105x148mm (相片纸进纸槽)</span>
                    </div>
                    <div class="tray-box">
                        <input type="checkbox" disabled>
                        <span>iso_a5_148x210mm (半页票据)</span>
                    </div>
                    <div class="tray-box">
                        <input type="checkbox" disabled>
                        <span>iso_a3_297x420mm</span>
                    </div>
                </div>
            </div>

            <!-- 打印记录 -->
            <div class="card">
                <div class="card-header"><span>🕒 最近提交记录</span></div>
                <div id="historyList">
                    <div style="text-align: center; color: var(--text-muted); padding: 16px;">暂无打印记录</div>
                </div>
            </div>
        </section>
    </main>

    <!-- 添加打印机与驱动模态弹窗 -->
    <div class="modal-mask" id="driverModal">
        <div class="modal-box">
            <div class="modal-title">
                <span>➕ 添加打印机与加载驱动</span>
                <button class="modal-close" onclick="closeDriverModal()">&times;</button>
            </div>
            
            <div class="form-group" style="margin-bottom: 12px;">
                <label class="form-label">1. 扫描/探测物理硬件 (USB/网络)</label>
                <div style="display: flex; gap: 8px;">
                    <select id="detectedDevices" class="form-control" onchange="document.getElementById('deviceUri').value = this.value">
                        <option value="">正在探测设备...</option>
                    </select>
                    <button class="nav-btn nav-btn-primary" style="white-space: nowrap;" onclick="scanHardwareDevices()">重新扫描</button>
                </div>
            </div>

            <div class="form-group" style="margin-bottom: 12px;">
                <label class="form-label">设备 URI 地址</label>
                <input type="text" id="deviceUri" class="form-control" placeholder="如 usb://HP/LaserJet%201020 或 ipp://...">
            </div>

            <div class="form-group" style="margin-bottom: 12px;">
                <label class="form-label">2. 打印机英文标识 (不可包含空格)</label>
                <input type="text" id="newPrinterName" class="form-control" placeholder="如 HP_LaserJet_1020">
            </div>

            <div class="form-group" style="margin-bottom: 16px;">
                <label class="form-label">3. 上传 PPD 驱动文件 (.ppd)</label>
                <input type="file" id="ppdFileInput" class="form-control" accept=".ppd" style="padding-top: 6px;">
                <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">留空则自动选用 IPP Everywhere / Raw 通用驱动</div>
            </div>

            <button class="btn-submit" onclick="submitAddPrinter()">🚀 立即绑定并加载驱动</button>
        </div>
    </div>

    <script>
        document.getElementById('cupsLink').href = 'http://' + location.hostname + ':631';
        let selectedFile = null;
        let printConfig = { color: 'color', orient: 'portrait' };
        let printersData = [];

        const dropZone = document.getElementById('dropZone');
        const fileInput = document.getElementById('fileInput');
        const previewImg = document.getElementById('previewImg');
        const previewPlaceholder = document.getElementById('previewPlaceholder');
        const paperSheet = document.getElementById('paperSheet');

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            window.addEventListener(eventName, e => { e.preventDefault(); e.stopPropagation(); }, false);
            dropZone.addEventListener(eventName, e => { e.preventDefault(); e.stopPropagation(); }, false);
        });

        ['dragenter', 'dragover'].forEach(eventName => dropZone.addEventListener(eventName, () => dropZone.classList.add('dragover'), false));
        ['dragleave', 'drop'].forEach(eventName => dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'), false));

        dropZone.addEventListener('click', () => fileInput.click());
        dropZone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            if (dt && dt.files && dt.files.length > 0) handleFileSelect(dt.files);
        });
        fileInput.addEventListener('change', function() { handleFileSelect(this.files); });

        function handleFileSelect(files) {
            if (!files || !files.length) return;
            selectedFile = files[0];
            document.getElementById('fileNameDisplay').textContent = selectedFile.name;
            const sz = (selectedFile.size / 1024 / 1024).toFixed(2);
            document.getElementById('fileSizeDisplay').textContent = sz > 0 ? sz + ' MB' : (selectedFile.size / 1024).toFixed(1) + ' KB';
            document.getElementById('fileInfoBox').style.display = 'flex';

            if (selectedFile.type.startsWith('image/') || /\.(jpg|jpeg|png|bmp|heic|webp)$/i.test(selectedFile.name)) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    previewImg.src = e.target.result;
                    previewImg.style.display = 'block';
                    previewPlaceholder.style.display = 'none';
                    updateMirrorPreview();
                };
                reader.readAsDataURL(selectedFile);
            } else {
                previewImg.style.display = 'none';
                previewPlaceholder.style.display = 'block';
                previewPlaceholder.innerHTML = `
                    <div class="icon">📑</div>
                    <div style="font-weight: 600; color: #334155; margin-bottom: 4px;">${selectedFile.name}</div>
                    <div style="font-size: 11px;">文档将自动解析版面</div>
                `;
            }
        }

        // 清空/移除已选文件函数
        function clearSelectedFile() {
            selectedFile = null;
            fileInput.value = '';
            document.getElementById('fileInfoBox').style.display = 'none';
            previewImg.src = '';
            previewImg.style.display = 'none';
            previewPlaceholder.style.display = 'block';
            previewPlaceholder.innerHTML = `
                <div class="icon">🖼️</div>
                <div style="font-size: 13px;">选择或拖入图片查看排版</div>
            `;
        }

        document.getElementById('mirrorCheckbox').addEventListener('change', updateMirrorPreview);
        function updateMirrorPreview() {
            const isMirror = document.getElementById('mirrorCheckbox').checked;
            previewImg.style.transform = isMirror ? 'scaleX(-1)' : 'none';
        }

        function setColor(m) {
            printConfig.color = m;
            document.getElementById('btnColor').classList.toggle('active', m === 'color');
            document.getElementById('btnGray').classList.toggle('active', m === 'gray');
        }

        function setOrient(o) {
            printConfig.orient = o;
            document.getElementById('btnPortrait').classList.toggle('active', o === 'portrait');
            document.getElementById('btnLandscape').classList.toggle('active', o === 'landscape');
            if (o === 'portrait') {
                paperSheet.classList.add('paper-portrait');
                paperSheet.classList.remove('paper-landscape');
            } else {
                paperSheet.classList.add('paper-landscape');
                paperSheet.classList.remove('paper-portrait');
            }
        }

        function syncSelectedPrinter() {
            const selName = document.getElementById('printerSelect').value;
            const target = printersData.find(p => p.name === selName);
            if (!target) return;

            const badge = document.getElementById('curStatusBadge');
            badge.textContent = target.status;
            badge.className = 'badge ' + (
                target.status_type === 'error' ? 'badge-error' :
                target.status_type === 'warn' ? 'badge-warn' :
                target.status_type === 'busy' ? 'badge-busy' : 'badge-idle'
            );

            document.getElementById('queueJobCount').textContent = target.jobs;
        }

        async function loadPrinters() {
            try {
                const res = await fetch('/api/printers?_t=' + Date.now());
                const data = await res.json();
                printersData = data.printers || [];
                const sel = document.getElementById('printerSelect');
                sel.innerHTML = '';
                
                printersData.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.name;
                    opt.textContent = `${p.name} [${p.status}]`;
                    if (p.name === data.default) opt.selected = true;
                    sel.appendChild(opt);
                });

                if (printersData.length > 0) {
                    syncSelectedPrinter();
                } else {
                    document.getElementById('curStatusBadge').textContent = '未连接';
                    document.getElementById('curStatusBadge').className = 'badge badge-warn';
                }

                if (data.uptime) {
                    document.getElementById('uptimeDisplay').textContent = data.uptime;
                }
            } catch(e) {}
        }

        async function loadHistory() {
            try {
                const res = await fetch('/api/history?_t=' + Date.now());
                const list = await res.json();
                const c = document.getElementById('historyList');
                if (!list.length) return;
                c.innerHTML = list.map(i => `
                    <div class="record-item">
                        <div>
                            <div class="record-title">${i.filename}</div>
                            <div class="record-sub">${i.printer} · ${i.time}</div>
                        </div>
                        <span class="badge ${i.status === '已出纸' ? 'badge-idle' : 'badge-busy'}">${i.status}</span>
                    </div>
                `).join('');
            } catch(e) {}
        }

        async function submitPrintJob() {
            if (!selectedFile) return alert('请先拖入或选择文件！');
            const fd = new FormData();
            fd.append('file', selectedFile);
            fd.append('printer', document.getElementById('printerSelect').value);
            fd.append('color', printConfig.color);
            fd.append('orient', printConfig.orient);
            fd.append('duplex', document.getElementById('duplexSelect').value);
            fd.append('copies', document.getElementById('copiesInput').value);
            fd.append('media', document.getElementById('mediaSelect').value);
            fd.append('paperType', document.getElementById('paperTypeSelect').value);
            fd.append('scale', document.getElementById('scaleSelect').value);
            fd.append('pageRange', document.getElementById('pageRangeInput').value);
            fd.append('mirror', document.getElementById('mirrorCheckbox').checked ? 'true' : 'false');

            const res = await fetch('/api/print', { method: 'POST', body: fd });
            const ret = await res.json();
            if (ret.code === 0) {
                alert('🎉 打印任务已成功加入队列！');
                clearSelectedFile(); // 提交成功后自动重置文件
                loadHistory();
                loadPrinters();
            } else {
                alert('提交失败: ' + ret.msg);
            }
        }

        function openDriverModal() {
            document.getElementById('driverModal').style.display = 'flex';
            scanHardwareDevices();
        }

        function closeDriverModal() {
            document.getElementById('driverModal').style.display = 'none';
        }

        async function scanHardwareDevices() {
            const devSelect = document.getElementById('detectedDevices');
            devSelect.innerHTML = '<option value="">正在探测设备中...</option>';
            try {
                const res = await fetch('/api/scan_devices?_t=' + Date.now());
                const list = await res.json();
                devSelect.innerHTML = '';
                if (list.length === 0) {
                    devSelect.innerHTML = '<option value="">未自动发现新硬件，请检查USB</option>';
                    return;
                }
                list.forEach((item, idx) => {
                    const opt = document.createElement('option');
                    opt.value = item.uri;
                    opt.textContent = `${item.name} (${item.type})`;
                    devSelect.appendChild(opt);
                    if (idx === 0) {
                        document.getElementById('deviceUri').value = item.uri;
                        const autoName = item.name.replace(/[^a-zA-Z0-9_]/g, '_');
                        document.getElementById('newPrinterName').value = autoName;
                    }
                });
            } catch (e) {
                devSelect.innerHTML = '<option value="">探测请求失败</option>';
            }
        }

        async function submitAddPrinter() {
            const name = document.getElementById('newPrinterName').value.trim();
            const uri = document.getElementById('deviceUri').value.trim();
            const ppdInput = document.getElementById('ppdFileInput');

            if (!name || !uri) return alert('请填写【打印机名称】和【设备 URI】！');

            const fd = new FormData();
            fd.append('name', name);
            fd.append('uri', uri);
            if (ppdInput.files.length > 0) fd.append('ppd', ppdInput.files[0]);

            const res = await fetch('/api/add_printer', { method: 'POST', body: fd });
            const ret = await res.json();
            if (ret.code === 0) {
                alert('🎉 打印机与驱动加载成功！已开启 AirPrint 共享！');
                closeDriverModal();
                loadPrinters();
            } else {
                alert('添加失败: ' + ret.msg);
            }
        }

        loadPrinters();
        loadHistory();
        setInterval(loadPrinters, 4000);
        setInterval(loadHistory, 6000);
    </script>
</body>
</html>
"""

class MainHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html; charset=UTF-8")
        # 禁用浏览器对 HTML 的强缓存，确保每次加载最新界面
        self.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.set_header("Pragma", "no-cache")
        self.set_header("Expires", "0")
        self.write(HTML_PAGE)

class PrintersApiHandler(tornado.web.RequestHandler):
    def get(self):
        printers, def_p = get_printers_info()
        uptime = get_system_uptime_str()
        self.set_header("Cache-Control", "no-cache")
        self.write(json.dumps({
            "printers": printers,
            "default": def_p,
            "uptime": uptime
        }))

class HistoryApiHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Cache-Control", "no-cache")
        self.write(json.dumps(PRINT_HISTORY))

class ScanDevicesApiHandler(tornado.web.RequestHandler):
    def get(self):
        devices = []
        try:
            res = subprocess.run(["lpinfo", "-v"], stdout=subprocess.PIPE, text=True, timeout=6)
            for line in res.stdout.splitlines():
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    dtype, uri = parts[0].strip(), parts[1].strip()
                    if any(uri.startswith(p) for p in ["usb://", "ipp://", "dnssd://", "socket://", "beh://"]):
                        name = uri.split("/")[-1].replace("%20", " ") or "未知打印设备"
                        devices.append({"name": name, "uri": uri, "type": dtype})
        except Exception as e:
            print(f"探测设备异常: {e}")
        self.write(json.dumps(devices))

class AddPrinterApiHandler(tornado.web.RequestHandler):
    def post(self):
        name = self.get_argument("name", "").strip()
        uri = self.get_argument("uri", "").strip()
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
        if not safe_name or not uri:
            self.write(json.dumps({"code": 1, "msg": "打印机名称或 URI 不能为空"}))
            return

        ppd_path = None
        if 'ppd' in self.request.files:
            ppd_file = self.request.files['ppd'][0]
            ppd_path = os.path.join(PPD_DIR, f"{safe_name}.ppd")
            with open(ppd_path, 'wb') as f:
                f.write(ppd_file['body'])

        cmd = ["lpadmin", "-p", safe_name, "-E", "-v", uri]
        if ppd_path and os.path.exists(ppd_path):
            cmd.extend(["-P", ppd_path])
        else:
            cmd.extend(["-m", "everywhere"])

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=12)
            if res.returncode != 0:
                self.write(json.dumps({"code": 1, "msg": res.stderr.strip() or "lpadmin 执行失败"}))
                return

            subprocess.run(["cupsaccept", safe_name])
            subprocess.run(["cupsenable", safe_name])
            subprocess.run(["lpadmin", "-d", safe_name])

            self.write(json.dumps({"code": 0, "msg": "成功"}))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": str(e)}))

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

        if ext in [".jpg", ".jpeg", ".png", ".bmp", ".heic"]:
            auto_process_image(filepath)
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
        paper_type = self.get_argument("paperType", "plain")
        scale = self.get_argument("scale", "fit-to-page")
        page_range = self.get_argument("pageRange", "")
        mirror = self.get_argument("mirror", "false")

        cmd = ["lp"]
        if printer: cmd.extend(["-d", printer])
        cmd.extend(["-n", str(copies)])
        cmd.extend(["-o", f"media={media}"])

        if scale == "fit-to-page":
            cmd.extend(["-o", "fit-to-page"])
        if orient == "landscape":
            cmd.extend(["-o", "orientation-requested=4"])
        if color == "gray":
            cmd.extend(["-o", "ColorModel=Gray"])
        if paper_type == "photo":
            cmd.extend(["-o", "MediaType=Photo"])
        if mirror == "true":
            cmd.extend(["-o", "mirror"])
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
                    "status": "已出纸"
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
        (r"/api/scan_devices", ScanDevicesApiHandler),
        (r"/api/add_printer", AddPrinterApiHandler),
        (r"/api/history", HistoryApiHandler),
        (r"/api/print", PrintApiHandler),
        (r"/scans/(.*)", tornado.web.StaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(PORT, address="0.0.0.0")
    print(f" [Web App] 全功能控制台已监听 0.0.0.0:{PORT} ...", flush=True)
    tornado.ioloop.IOLoop.current().start()
