#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import subprocess
import tornado.ioloop
import tornado.web

try:
    from mail_print import auto_process_image, convert_office_to_pdf
except ImportError:
    auto_process_image = lambda x: True
    convert_office_to_pdf = lambda x: None

PORT = int(os.getenv("WEB_PORT", "8088"))
UPLOAD_DIR = "/tmp/cups_web_uploads"
PPD_DIR = "/etc/cups/ppd"
SCAN_DIR = os.getenv("SCAN_DIR", "/scans")

for d in (UPLOAD_DIR, PPD_DIR, SCAN_DIR):
    os.makedirs(d, exist_ok=True)

PRINT_HISTORY = []

def get_uptime_str():
    try:
        with open('/proc/uptime') as f:
            sec = float(f.readline().split()[0])
            d, h = int(sec // 86400), int((sec % 86400) // 3600)
            return f"{d}天{h}小时" if d > 0 else f"{h}小时{int((sec % 3600) // 60)}分钟"
    except Exception:
        return "1小时内"

def diagnose_printer(name):
    env = dict(os.environ, LC_ALL="C")
    status, stype = "空闲", "idle"
    try:
        r = subprocess.run(["lpstat", "-l", "-p", name], capture_output=True, text=True, env=env, timeout=3)
        raw = (r.stdout + r.stderr).lower()
        if any(k in raw for k in ["media-jam", "paper jam"]):
            status, stype = "卡纸", "error"
        elif any(k in raw for k in ["media-empty", "out of paper"]):
            status, stype = "缺纸", "error"
        elif any(k in raw for k in ["toner-empty", "marker-supply-empty", "cartridge"]):
            status, stype = "缺墨", "warn"
        elif "door-open" in raw or "cover open" in raw:
            status, stype = "机盖打开", "warn"
        elif "offline" in raw or "not connected" in raw:
            status, stype = "脱机", "error"
        elif "disabled" in raw:
            status, stype = "已暂停", "warn"
        elif "printing" in raw:
            status, stype = "打印中", "busy"
    except Exception:
        pass

    try:
        q = subprocess.run(["lpstat", "-o", name], capture_output=True, text=True, env=env, timeout=3)
        jobs = len([l for l in q.stdout.splitlines() if l.strip()])
    except Exception:
        jobs = 0

    return status, stype, jobs

HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>CUPS 智能打印与扫描控制台</title>
<style>
:root{--p:#00c065;--ph:#00a858;--b:#0284c7;--d:#ef4444;--bg:#f4f6f8;--c:#fff;--bd:#e2e8f0;--tx:#1e293b;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}
.nav{background:var(--c);border-bottom:1px solid var(--bd);padding:10px 24px;display:flex;justify-content:space-between;align-items:center}
.tab-group{display:flex;gap:4px;background:#e2e8f0;padding:3px;border-radius:8px}
.tab-btn{border:none;background:transparent;padding:6px 16px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;color:var(--mu);transition:all .2s}
.tab-btn.active{background:#fff;color:var(--tx);box-shadow:0 1px 3px rgba(0,0,0,.08)}
.btn{border:none;padding:6px 14px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;display:inline-flex;align-items:center;gap:4px;text-decoration:none}
.btn-p{background:var(--p);color:#fff}.btn-b{background:var(--b);color:#fff}.btn-o{border:1px solid var(--bd);background:var(--c);color:var(--tx)}
.box{max-width:1280px;margin:20px auto;padding:0 20px;display:grid;grid-template-columns:1.55fr 1fr;gap:20px}
@media(max-width:900px){.box{grid-template-columns:1fr}}
.card{background:var(--c);border-radius:10px;border:1px solid var(--bd);padding:20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.03)}
.card-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #f1f5f9;font-weight:600}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.fg{display:flex;flex-direction:column;gap:6px}.fc{width:100%;height:38px;border:1px solid var(--bd);border-radius:6px;padding:0 12px;outline:none}
.fc:focus{border-color:var(--p)}
.pills{display:flex;border:1px solid var(--bd);border-radius:6px;overflow:hidden;height:38px}
.pill{flex:1;border:none;background:#f8fafc;cursor:pointer;font-size:13px;font-weight:500}.pill.act{background:var(--p);color:#fff;font-weight:600}
.drop{border:2px dashed #cbd5e1;border-radius:8px;padding:20px;text-align:center;cursor:pointer;background:#f8fafc;margin-bottom:16px}
.drop.drag{border-color:var(--p);background:#dcfce7}
.file-list{display:none;flex-direction:column;gap:8px;margin-bottom:16px}
.file-item{display:flex;align-items:center;justify-content:space-between;background:#f8fafc;border:1px solid var(--bd);padding:8px 12px;border-radius:6px;font-size:13px;cursor:pointer}
.file-item.active{background:#f0fdf4;border-color:#bbf7d0}
.del-btn{background:#fee2e2;color:var(--d);border:1px solid #fca5a5;padding:3px 8px;border-radius:4px;font-size:12px;cursor:pointer}
.prev-box{background:#e2e8f0;border-radius:8px;padding:16px;display:flex;justify-content:center;align-items:center;min-height:260px}
.paper{background:#fff;box-shadow:0 4px 12px rgba(0,0,0,.1);border-radius:4px;display:flex;justify-content:center;align-items:center;overflow:hidden;transition:.3s;position:relative}
.paper.p{width:200px;height:283px}.paper.l{width:283px;height:200px}
.paper iframe{width:100%;height:100%;border:none}
.st-row{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:#f8fafc;border-radius:6px;margin-bottom:8px;border:1px solid #edf2f7}
.badge{font-size:12px;padding:3px 10px;border-radius:12px;font-weight:600}
.badge-idle{background:#dcfce7;color:#15803d}.badge-busy{background:#e0f2fe;color:#0369a1}.badge-warn{background:#fef9c3;color:#854d0e}.badge-error{background:#fee2e2;color:#b91c1c}
.tray{background:#f8fafc;border:1px solid #edf2f7;border-radius:6px;padding:8px 12px;margin-bottom:6px;display:flex;gap:8px;font-size:12px;color:#475569}
.sub-btn{width:100%;height:44px;background:var(--p);color:#fff;border:none;border-radius:6px;font-size:15px;font-weight:600;cursor:pointer}
.modal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.4);display:none;justify-content:center;align-items:center;z-index:99}
.m-box{background:#fff;width:480px;max-width:92%;border-radius:12px;padding:24px}
</style></head>
<body>
<header class="nav">
  <div style="display:flex;align-items:center;gap:16px">
    <div style="font-size:18px;font-weight:700">🖨️ CUPS 打印扫描终端</div>
    <div class="tab-group">
      <button class="tab-btn active" id="tabPrintBtn" onclick="switchTab('print')">📄 快速打印</button>
      <button class="tab-btn" id="tabScanBtn" onclick="switchTab('scan')">📠 扫描仪</button>
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
    <div class="card-h"><span>🖨️ 打印机与多文件队列</span><a href="javascript:openModal()" style="font-size:12px;color:var(--b);text-decoration:none">➕ 安装驱动</a></div>
    <div class="fg" style="margin-bottom:16px"><select id="selP" class="fc" onchange="syncP()"></select></div>
    
    <div class="drop" id="dz"><input type="file" id="fi" multiple style="display:none"><div style="font-size:28px">📑</div><div style="font-weight:600">点击或将【多张照片/多个PDF】拖入此处</div><div style="font-size:12px;color:var(--mu)">支持批量上传与连续打印，点击列表中文件可切换预览</div></div>

    <div class="file-list" id="flist"></div>
  </div>
  
  <div class="card">
    <div class="card-h"><span>⚲ 打印参数</span></div>
    <div class="row">
      <div class="fg"><label>颜色模式</label><div class="pills"><button type="button" class="pill act" id="bCol" onclick="setOpt('c','color')">🌈 彩色</button><button type="button" class="pill" id="bGray" onclick="setOpt('c','gray')">⚪ 黑白</button></div></div>
      <div class="fg"><label>打印方向</label><div class="pills"><button type="button" class="pill act" id="bPor" onclick="setOpt('o','portrait')">▯ 纵向</button><button type="button" class="pill" id="bLan" onclick="setOpt('o','landscape')">▭ 横向</button></div></div>
    </div>
    <div class="row">
      <div class="fg"><label>双面</label><select id="dup" class="fc"><option value="one-sided">单面</option><option value="two-sided-long-edge">双面(长边)</option><option value="two-sided-short-edge">双面(短边)</option></select></div>
      <div class="fg"><label>份数</label><input type="number" id="cop" class="fc" value="1" min="1" max="99"></div>
    </div>
    <div class="row">
      <div class="fg"><label>纸张大小</label><select id="med" class="fc"><option value="A4">A4 (210×297mm)</option><option value="A5">A5</option><option value="A6">A6</option><option value="B5">B5</option></select></div>
      <div class="fg"><label>纸张类型</label><select id="ptype" class="fc"><option value="plain">普通纸</option><option value="photo">相片纸</option></select></div>
    </div>
    <div class="row">
      <div class="fg"><label>缩放</label><select id="scale" class="fc"><option value="fit-to-page">适应纸张</option><option value="actual">实际大小</option></select></div>
      <div class="fg"><label>页面范围</label><input type="text" id="prange" class="fc" placeholder="留空=全部"></div>
    </div>
    <div class="fg" style="margin-bottom:16px"><label style="display:flex;gap:6px;cursor:pointer"><input type="checkbox" id="mirr" style="accent-color:var(--p)"> <span>[⇋] 水平镜像翻转</span></label></div>
    
    <div class="fg" style="margin-bottom:18px">
      <label>👀 纸张仿真预览 <span id="prevName" style="font-weight:normal;color:var(--mu);font-size:12px"></span></label>
      <div class="prev-box">
        <div class="paper p" id="psheet">
          <div id="ph" style="text-align:center;color:var(--mu)"><div style="font-size:32px">🖼️</div>请拖入文件预览排版</div>
          <img id="pimg" style="display:none;max-width:100%;max-height:100%;object-fit:contain">
          <iframe id="ppdf" style="display:none"></iframe>
        </div>
      </div>
    </div>
    <button class="sub-btn" id="submitBtn" onclick="submitPrint()">🖨️ 提交打印 (0个文件)</button>
  </div>
</section>
<section>
  <div class="card">
    <div class="card-h"><span>📈 打印机状态</span><button class="btn btn-o" style="padding:2px 8px;font-size:11px" onclick="loadPrinters()">🔄</button></div>
    <div class="st-row"><span>ℹ️ 打印机状态</span><span class="badge badge-idle" id="stB">空闲</span></div>
    <div class="st-row"><span>📊 队列任务数</span><b id="stJ">0</b></div>
    <div class="st-row"><span>🕒 状态持续</span><span id="stU" style="color:var(--mu)">-</span></div>
    <div style="margin-top:14px;font-weight:600;font-size:13px;margin-bottom:6px">📚 纸盒信息</div>
    <div class="tray"><input type="checkbox" checked disabled> iso_a4_210x297mm (默认进纸)</div>
    <div class="tray"><input type="checkbox" disabled> iso_a6_105x148mm (相片纸槽)</div>
    <div class="tray"><input type="checkbox" disabled> iso_a5_148x210mm (半页票据)</div>
  </div>
  <div class="card"><div class="card-h"><span>🕒 打印记录</span></div><div id="hlist" style="color:var(--mu);text-align:center;padding:12px">暂无记录</div></div>
</section>
</main>

<main class="box" id="scanView" style="display:none">
<section>
  <div class="card">
    <div class="card-h"><span>📠 扫描仪设备与参数</span><button class="btn btn-p" style="padding:3px 10px;font-size:12px" onclick="scanHardwareScanners()">🔍 重新探测扫描仪</button></div>
    <div class="fg" style="margin-bottom:14px">
      <label>选择扫描仪设备</label>
      <select id="scannerSelect" class="fc"><option value="">正在检测 SANE 扫描仪...</option></select>
    </div>
    <div class="row">
      <div class="fg"><label>色彩模式</label>
        <select id="scanMode" class="fc">
          <option value="Color">彩色 (Color)</option>
          <option value="Gray">灰度 (Gray)</option>
          <option value="Lineart">黑白线条 (Lineart)</option>
        </select>
      </div>
      <div class="fg"><label>分辨率 (DPI)</label>
        <select id="scanResolution" class="fc">
          <option value="300" selected>300 DPI (高清推荐)</option>
          <option value="150">150 DPI (快速浏览)</option>
          <option value="600">600 DPI (超高清)</option>
        </select>
      </div>
    </div>
    <div class="row">
      <div class="fg"><label>输出格式</label>
        <select id="scanFormat" class="fc">
          <option value="pdf">PDF 文档 (.pdf)</option>
          <option value="jpg">高清图片 (.jpg)</option>
        </select>
      </div>
      <div class="fg"><label>智能处理</label>
        <label style="display:flex;align-items:center;gap:8px;height:38px;cursor:pointer">
          <input type="checkbox" id="scanAutoWhiten" checked style="accent-color:var(--p);width:16px;height:16px">
          <span>自动纯白去底噪与纠偏</span>
        </label>
      </div>
    </div>
    <button class="sub-btn" id="startScanBtn" onclick="doStartScan()">🚀 开始扫描并生成文件</button>
  </div>

  <div class="card">
    <div class="card-h">
      <span>👀 扫描预览与复印</span>
      <div id="scanActionToolbar" style="display:none;gap:8px;align-items:center">
        <button class="btn btn-p" onclick="printCurrentScan()">🖨️ 立即打印此件</button>
        <a id="scanDownloadBtn" class="btn btn-o" download style="font-size:12px;padding:5px 10px">⬇️ 下载</a>
      </div>
    </div>
    
    <div class="prev-box" style="min-height:360px">
      <div class="paper p" id="scanPaperSheet" style="width:240px;height:340px">
        <div id="scanEmptyTip" style="text-align:center;color:var(--mu)">
          <div style="font-size:36px">📠</div>
          <div style="font-weight:600;margin-top:6px">就绪状态</div>
          <div style="font-size:12px;margin-top:2px">点击上方“开始扫描”后在此预览</div>
        </div>
        <img id="scanPrevImg" style="display:none;max-width:100%;max-height:100%;object-fit:contain">
        <iframe id="scanPrevPdf" style="display:none;width:100%;height:100%;border:none"></iframe>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="card">
    <div class="card-h"><span>📁 扫描历史文件库 (/scans)</span><button class="btn btn-o" style="padding:2px 8px;font-size:11px" onclick="loadScanFiles()">🔄 刷新</button></div>
    <div id="scanFileList" style="display:flex;flex-direction:column;gap:8px">
      <div style="text-align:center;color:var(--mu);padding:14px">正在加载扫描文件...</div>
    </div>
  </div>
</section>
</main>

<div class="modal" id="mD"><div class="m-box">
  <div style="display:flex;justify-content:space-between;margin-bottom:14px"><b style="font-size:16px">➕ 添加打印机驱动</b><span style="cursor:pointer;font-size:20px" onclick="closeModal()">&times;</span></div>
  <div class="fg" style="margin-bottom:10px"><label>1. 扫描物理硬件</label><div style="display:flex;gap:6px"><select id="devs" class="fc" onchange="devUri.value=this.value"></select><button class="btn btn-p" onclick="scanDevs()">扫描</button></div></div>
  <div class="fg" style="margin-bottom:10px"><label>设备 URI</label><input type="text" id="devUri" class="fc" placeholder="usb://..."></div>
  <div class="fg" style="margin-bottom:10px"><label>打印机标识</label><input type="text" id="pName" class="fc" placeholder="如 HP_1020"></div>
  <div class="fg" style="margin-bottom:14px"><label>驱动选择</label>
    <div style="display:flex;gap:12px;margin-bottom:6px"><label><input type="radio" name="pm" value="e" checked onchange="togPpd()"> 复用631驱动</label><label><input type="radio" name="pm" value="u" onchange="togPpd()"> 上传新PPD</label></div>
    <select id="ePpd" class="fc"></select><input type="file" id="uPpd" class="fc" accept=".ppd" style="display:none;padding-top:6px">
  </div>
  <button class="sub-btn" onclick="submitAddP()">🚀 立即绑定驱动</button>
</div></div>

<script>
document.getElementById('cupsL').href='http://'+location.hostname+':631';
let filesArr = [], curPrevIdx = 0, cfg={c:'color',o:'portrait'}, pData=[];
let currentScanFile = null;

const dz=document.getElementById('dz'), fi=document.getElementById('fi'), pimg=document.getElementById('pimg'), ppdf=document.getElementById('ppdf'), ph=document.getElementById('ph'), psheet=document.getElementById('psheet'), flist=document.getElementById('flist');

function switchTab(t){
  document.getElementById('tabPrintBtn').classList.toggle('active', t==='print');
  document.getElementById('tabScanBtn').classList.toggle('active', t==='scan');
  document.getElementById('printView').style.display = (t==='print') ? 'grid' : 'none';
  document.getElementById('scanView').style.display = (t==='scan') ? 'grid' : 'none';
  if(t==='scan'){ scanHardwareScanners(); loadScanFiles(); }
}

['dragenter','dragover','dragleave','drop'].forEach(e=>{window.addEventListener(e,ev=>{ev.preventDefault();ev.stopPropagation()});dz.addEventListener(e,ev=>{ev.preventDefault();ev.stopPropagation()})});
['dragenter','dragover'].forEach(e=>dz.addEventListener(e,()=>dz.classList.add('drag')));
['dragleave','drop'].forEach(e=>dz.addEventListener(e,()=>dz.classList.remove('drag')));
dz.onclick=()=>fi.click();

dz.ondrop=e=>{if(e.dataTransfer?.files.length)addFiles(e.dataTransfer.files)};
fi.onchange=function(){if(this.files.length)addFiles(this.files)};

function addFiles(fl){
  for(let f of fl){
    if(!filesArr.some(x=>x.name===f.name && x.size===f.size)){
      filesArr.push(f);
    }
  }
  curPrevIdx = filesArr.length - 1;
  renderFileList();
  showPreview(curPrevIdx);
}

function removeFile(idx, e){
  e.stopPropagation();
  filesArr.splice(idx, 1);
  if(curPrevIdx >= filesArr.length) curPrevIdx = filesArr.length - 1;
  renderFileList();
  showPreview(curPrevIdx);
}

function renderFileList(){
  if(!filesArr.length){
    flist.style.display='none';
    document.getElementById('submitBtn').textContent = '🖨️ 提交打印 (0个文件)';
    return;
  }
  flist.style.display='flex';
  flist.innerHTML = filesArr.map((f, i)=>`
    <div class="file-item ${i===curPrevIdx?'active':''}" onclick="switchPreview(${i})">
      <div style="display:flex;align-items:center;gap:8px;overflow:hidden">
        <span>${f.name.toLowerCase().endsWith('.pdf')?'📑':'🖼️'}</span>
        <span style="font-weight:500;white-space:nowrap;text-overflow:ellipsis;overflow:hidden">${f.name}</span>
        <span style="color:var(--mu);font-size:11px">(${(f.size/1048576).toFixed(2)}MB)</span>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        ${i===curPrevIdx?'<span style="color:var(--p);font-weight:600;font-size:12px">预览中</span>':''}
        <button class="del-btn" onclick="removeFile(${i}, event)">❌ 删除</button>
      </div>
    </div>
  `).join('');
  document.getElementById('submitBtn').textContent = `🖨️ 提交打印 (${filesArr.length}个文件)`;
}

function switchPreview(idx){
  curPrevIdx = idx;
  renderFileList();
  showPreview(curPrevIdx);
}

function showPreview(idx){
  if(idx < 0 || !filesArr[idx]){
    pimg.style.display='none'; ppdf.style.display='none'; ph.style.display='block';
    document.getElementById('prevName').textContent = '';
    return;
  }
  const f = filesArr[idx];
  document.getElementById('prevName').textContent = `— 正在预览: ${f.name}`;
  ph.style.display='none';

  if(f.name.toLowerCase().endsWith('.pdf') || f.type === 'application/pdf'){
    pimg.style.display='none';
    const blobUrl = URL.createObjectURL(f);
    ppdf.src = blobUrl + '#toolbar=0&navpanes=0';
    ppdf.style.display='block';
  } else if(f.type.startsWith('image/') || /\.(jpg|jpeg|png|bmp|webp)$/i.test(f.name)){
    ppdf.style.display='none';
    const r = new FileReader();
    r.onload = e => {
      pimg.src = e.target.result;
      pimg.style.display='block';
      updM();
    };
    r.readAsDataURL(f);
  } else {
    pimg.style.display='none'; ppdf.style.display='none';
    ph.style.display='block';
    ph.innerHTML = `<div style="font-size:28px">📄</div>${f.name}<div style="font-size:11px;color:var(--mu);margin-top:4px">该格式直接出纸打印</div>`;
  }
}

mirr.onchange=updM; function updM(){pimg.style.transform=mirr.checked?'scaleX(-1)':'none'}
function setOpt(k,v){
  cfg[k]=v; if(k==='c'){bCol.classList.toggle('act',v==='color');bGray.classList.toggle('act',v==='gray')}
  else{bPor.classList.toggle('act',v==='portrait');bLan.classList.toggle('act',v==='landscape');psheet.className=v==='portrait'?'paper p':'paper l'}
}
function syncP(){
  const t=pData.find(p=>p.name===selP.value); if(!t)return;
  stB.textContent=t.status; stB.className='badge badge-'+t.status_type; stJ.textContent=t.jobs;
}
async function loadPrinters(){
  try{
    const r=await(await fetch('/api/printers?_t='+Date.now())).json(); pData=r.printers||[]; selP.innerHTML='';
    pData.forEach(p=>{const o=document.createElement('option');o.value=p.name;o.textContent=`${p.name} [${p.status}]`;if(p.name===r.default)o.selected=true;selP.appendChild(o)});
    if(pData.length)syncP(); if(r.uptime)stU.textContent=r.uptime;
  }catch{}
}
async function loadHistory(){
  try{
    const h=await(await fetch('/api/history?_t='+Date.now())).json();
    if(h.length)hlist.innerHTML=h.map(i=>`<div class="st-row"><div><b>${i.filename}</b><div style="font-size:11px;color:var(--mu)">${i.printer} ·${i.time}</div></div><span class="badge ${i.status==='已出纸'?'badge-idle':'badge-busy'}">${i.status}</span></div>`).join('');
  }catch{}
}

async function submitPrint(){
  if(!filesArr.length) return alert('请先拖入或选择文件！');
  const fd=new FormData();
  for(let f of filesArr) fd.append('files', f);
  fd.append('printer',selP.value); fd.append('color',cfg.c); fd.append('orient',cfg.o);
  fd.append('duplex',dup.value); fd.append('copies',cop.value); fd.append('media',med.value); fd.append('paperType',ptype.value);
  fd.append('scale',scale.value); fd.append('pageRange',prange.value); fd.append('mirror',mirr.checked?'true':'false');
  
  const res=await(await fetch('/api/print',{method:'POST',body:fd})).json();
  if(res.code===0){
    alert(`🎉 成功提交 ${filesArr.length} 个打印任务！`);
    filesArr = []; fi.value=''; renderFileList(); showPreview(-1);
    loadHistory(); loadPrinters();
  } else alert('失败:'+res.msg);
}

// ==================== 扫描仪前端逻辑 ====================
async function scanHardwareScanners(){
  const sel = document.getElementById('scannerSelect');
  sel.innerHTML = '<option value="">正在检测 SANE 扫描仪硬件...</option>';
  try{
    const res = await fetch('/api/sane_scanners?_t='+Date.now());
    const list = await res.json();
    sel.innerHTML = '';
    if(!list.length){
      sel.innerHTML = '<option value="">未发现扫描仪 (请检查 USB 连接或 SANE 驱动)</option>';
      return;
    }
    list.forEach(item => {
      const opt = document.createElement('option');
      opt.value = item.device;
      opt.textContent = `📠 ${item.name} (${item.device})`;
      sel.appendChild(opt);
    });
  }catch(e){
    sel.innerHTML = '<option value="">探测请求失败</option>';
  }
}

async function doStartScan(){
  const dev = document.getElementById('scannerSelect').value;
  if(!dev) return alert('请先确认已连接并选择了扫描仪！');
  const btn = document.getElementById('startScanBtn');
  btn.disabled = true;
  btn.textContent = '⏳ 正在扫描中，请稍候机械回位 (约10-25秒)...';

  const fd = new FormData();
  fd.append('device', dev);
  fd.append('resolution', document.getElementById('scanResolution').value);
  fd.append('mode', document.getElementById('scanMode').value);
  fd.append('format', document.getElementById('scanFormat').value);
  fd.append('auto_whiten', document.getElementById('scanAutoWhiten').checked ? 'true' : 'false');

  try{
    const res = await fetch('/api/scan_job', { method:'POST', body:fd });
    const ret = await res.json();
    if(ret.code === 0){
      currentScanFile = ret.filename;
      alert('🎉 扫描成功完成！');
      
      document.getElementById('scanActionToolbar').style.display = 'flex';
      const dlBtn = document.getElementById('scanDownloadBtn');
      dlBtn.href = ret.url;
      dlBtn.download = ret.filename;

      document.getElementById('scanEmptyTip').style.display = 'none';
      const pimg = document.getElementById('scanPrevImg');
      const ppdf = document.getElementById('scanPrevPdf');

      if(ret.url.toLowerCase().endsWith('.pdf')){
        pimg.style.display = 'none';
        ppdf.src = ret.url + '#toolbar=0&navpanes=0';
        ppdf.style.display = 'block';
      }else{
        ppdf.style.display = 'none';
        pimg.src = ret.url + '?_t=' + Date.now();
        pimg.style.display = 'block';
      }
      loadScanFiles();
    }else{
      alert('扫描失败: ' + ret.msg);
    }
  }catch(e){
    alert('请求异常: ' + e);
  }finally{
    btn.disabled = false;
    btn.textContent = '🚀 开始扫描并生成文件';
  }
}

async function printCurrentScan(){
  if(!currentScanFile) return alert('当前没有可打印的扫描文件！');
  printScanByName(currentScanFile);
}

async function printScanByName(filename){
  const selPrinter = document.getElementById('selP').value;
  if(!confirm(`确认使用打印机 [${selPrinter \vert{}\vert{} '默认设备'}] 打印文件 "${filename}" 吗？`)) return;
  
  const fd = new FormData();
  fd.append('scan_file', filename);
  fd.append('printer', selPrinter);
  
  try{
    const res = await (await fetch('/api/print_scan_file', { method: 'POST', body: fd })).json();
    if(res.code === 0){
      alert('🎉 已提交打印机出纸！');
      loadHistory();
    } else {
      alert('提交失败: ' + res.msg);
    }
  }catch(e){
    alert('请求异常: ' + e);
  }
}

async function loadScanFiles(){
  const box = document.getElementById('scanFileList');
  try{
    const res = await fetch('/api/scan_files?_t='+Date.now());
    const list = await res.json();
    if(!list.length){
      box.innerHTML = '<div style="text-align:center;color:var(--mu);padding:14px">暂无历史扫描文件</div>';
      return;
    }
    box.innerHTML = list.map(f => `
      <div class="st-row">
        <div style="overflow:hidden">
          <a href="${f.url}" target="_blank" style="font-weight:600;color:var(--b);text-decoration:none">${f.name}</a>
          <div style="font-size:11px;color:var(--mu)">${f.time} ·${(f.size/1048576).toFixed(2)}MB</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <button class="btn btn-p" style="padding:2px 8px;font-size:11px" onclick="printScanByName('${f.name}')">🖨️ 打印</button>
          <a href="${f.url}" download class="btn btn-o" style="padding:2px 8px;font-size:11px">下载</a>
          <button class="del-btn" onclick="deleteScanFile('${f.name}')">删除</button>
        </div>
      </div>
    `).join('');
  }catch(e){}
}

async function deleteScanFile(fn){
  if(!confirm(`确定删除扫描文件 ${fn} 吗？`)) return;
  const fd = new FormData();
  fd.append('filename', fn);
  const r = await(await fetch('/api/delete_scan_file', { method:'POST', body:fd })).json();
  if(r.code === 0) loadScanFiles();
}

function openModal(){mD.style.display='flex';scanDevs();loadEp();}
function closeModal(){mD.style.display='none'}
function togPpd(){const u=document.querySelector('input[name="pm"]:checked').value==='u';ePpd.style.display=u?'none':'block';uPpd.style.display=u?'block':'none'}
async function scanDevs(){
  devs.innerHTML='<option>扫描中...</option>';
  try{
    const l=await(await fetch('/api/scan_devices?_t='+Date.now())).json(); devs.innerHTML='';
    if(!l.length){devs.innerHTML='<option>未检测到设备</option>';return}
    l.forEach((d,i)=>{const o=document.createElement('option');o.value=d.uri;o.textContent=d.name;devs.appendChild(o);if(i===0){devUri.value=d.uri;pName.value=d.name.replace(/[^a-zA-Z0-9_]/g,'_')}});
  }catch{}
}
async function loadEp(){
  try{
    const l=await(await fetch('/api/installed_ppds?_t='+Date.now())).json(); ePpd.innerHTML='';
    if(!l.length){ePpd.innerHTML='<option value="">(无现有驱动，请选上传)</option>';return}
    l.forEach(p=>{const o=document.createElement('option');o.value=p.filename;o.textContent=`📄 ${p.name}`;ePpd.appendChild(o)});
  }catch{}
}
async function submitAddP(){
  if(!pName.value.trim()||!devUri.value.trim())return alert('请填写名称与URI');
  const fd=new FormData(); fd.append('name',pName.value); fd.append('uri',devUri.value);
  if(document.querySelector('input[name="pm"]:checked').value==='e') fd.append('existing_ppd',ePpd.value);
  else if(uPpd.files.length) fd.append('ppd',uPpd.files[0]);
  const r=await(await fetch('/api/add_printer',{method:'POST',body:fd})).json();
  if(r.code===0){alert('🎉 驱动绑定成功！');closeModal();loadPrinters()}else alert('失败:'+r.msg);
}
function loadAll(){loadPrinters();loadHistory();loadScanFiles()}
loadAll(); setInterval(loadPrinters,4000); setInterval(loadHistory,6000);
</script></body></html>"""

class MainH(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html; charset=UTF-8")
        self.set_header("Cache-Control", "no-cache, no-store")
        self.write(HTML)

class PrnH(tornado.web.RequestHandler):
    def get(self):
        r, d = [], ""
        try:
            dp = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=3)
            if "destination: " in dp.stdout:
                d = dp.stdout.split("destination: ")[-1].strip()
            ps = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=3)
            for l in ps.stdout.splitlines():
                if l.startswith("printer "):
                    n = l.split()[1].strip()
                    st, stype, j = diagnose_printer(n)
                    r.append({"name": n, "status": st, "status_type": stype, "jobs": j})
        except Exception:
            pass
        self.write(json.dumps({"printers": r, "default": d, "uptime": get_uptime_str()}))

class DevsH(tornado.web.RequestHandler):
    def get(self):
        devs = []
        try:
            r = subprocess.run(["lpinfo", "-v"], capture_output=True, text=True, timeout=5)
            for l in r.stdout.splitlines():
                parts = l.split(maxsplit=1)
                if len(parts) == 2 and any(parts[1].startswith(p) for p in ["usb://", "ipp://", "dnssd://", "socket://"]):
                    devs.append({"name": parts[1].split("/")[-1].replace("%20", " "), "uri": parts[1].strip()})
        except Exception:
            pass
        self.write(json.dumps(devs))

class PpdH(tornado.web.RequestHandler):
    def get(self):
        l = [{"name": os.path.splitext(f)[0], "filename": f} for f in os.listdir(PPD_DIR) if f.endswith(".ppd")] if os.path.exists(PPD_DIR) else []
        self.write(json.dumps(l))

class AddH(tornado.web.RequestHandler):
    def post(self):
        n = re.sub(r'[^a-zA-Z0-9_-]', '_', self.get_argument("name", "").strip())
        u = self.get_argument("uri", "").strip()
        ep = self.get_argument("existing_ppd", "").strip()
        if not n or not u:
            return self.write(json.dumps({"code": 1, "msg": "参数缺失"}))

        ppd = os.path.join(PPD_DIR, f"{n}.ppd")
        if 'ppd' in self.request.files:
            with open(ppd, 'wb') as f:
                f.write(self.request.files['ppd'][0]['body'])
        elif ep and os.path.exists(os.path.join(PPD_DIR, ep)):
            ppd = os.path.join(PPD_DIR, ep)
        else:
            ppd = None

        cmd = ["lpadmin", "-p", n, "-E", "-v", u]
        cmd.extend(["-P", ppd] if ppd else ["-m", "everywhere"])
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                subprocess.run(["cupsaccept", n])
                subprocess.run(["cupsenable", n])
                subprocess.run(["lpadmin", "-d", n])
                self.write(json.dumps({"code": 0}))
            else:
                self.write(json.dumps({"code": 1, "msg": res.stderr.strip()}))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": str(e)}))

class HistoryH(tornado.web.RequestHandler):
    def get(self):
        self.write(json.dumps(PRINT_HISTORY))

class PrintH(tornado.web.RequestHandler):
    def post(self):
        uploaded_files = []
        if 'files' in self.request.files:
            uploaded_files = self.request.files['files']
        elif 'file' in self.request.files:
            uploaded_files = [self.request.files['file'][0]]

        if not uploaded_files:
            return self.write(json.dumps({"code": 1, "msg": "未选文件"}))

        printer = self.get_argument("printer", "")
        copies = self.get_argument("copies", "1")
        media = self.get_argument("media", "A4")
        scale = self.get_argument("scale", "fit-to-page")
        orient = self.get_argument("orient", "portrait")
        color = self.get_argument("color", "color")
        paper_type = self.get_argument("paperType", "plain")
        mirror = self.get_argument("mirror", "false")
        duplex = self.get_argument("duplex", "one-sided")
        page_range = self.get_argument("pageRange", "").strip()

        success_count = 0
        last_err = ""

        for f_obj in uploaded_files:
            fname = f_obj['filename']
            fp = os.path.join(UPLOAD_DIR, fname)
            ext = os.path.splitext(fname)[1].lower()
            with open(fp, 'wb') as out:
                out.write(f_obj['body'])

            if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
                auto_process_image(fp)
            elif ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]:
                pdf = convert_office_to_pdf(fp)
                if pdf:
                    fp = pdf

            cmd = ["lp"]
            if printer:
                cmd.extend(["-d", printer])
            cmd.extend([
                "-n", str(copies),
                "-o", f"media={media}",
                "-o", "Resolution=600dpi", "-o", "pdftops-renderer=gs", "-o", "print-quality=5"
            ])
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
            if page_range:
                cmd.extend(["-o", f"page-ranges={page_range}"])
            cmd.append(fp)

            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    success_count += 1
                    PRINT_HISTORY.insert(0, {
                        "filename": fname,
                        "printer": printer or "默认",
                        "time": time.strftime("%m/%d %H:%M"),
                        "status": "已出纸"
                    })
                else:
                    last_err = r.stderr.strip()
            except Exception as e:
                last_err = str(e)

        if len(PRINT_HISTORY) > 30:
            del PRINT_HISTORY[30:]

        if success_count > 0:
            self.write(json.dumps({"code": 0, "msg": f"成功提交 {success_count} 个任务"}))
        else:
            self.write(json.dumps({"code": 1, "msg": last_err or "提交打印失败"}))

# ==================== SANE 扫描仪专属路由 ====================
class SaneScannersApiHandler(tornado.web.RequestHandler):
    def get(self):
        scanners = []
        try:
            env = dict(os.environ, LC_ALL="C")
            res = subprocess.run(["scanimage", "-L"], capture_output=True, text=True, env=env, timeout=10)
            for line in res.stdout.splitlines():
                m = re.search(r"device `([^']+)' is a (.*)", line)
                if m:
                    dev_id = m.group(1).strip()
                    desc = m.group(2).strip()
                    scanners.append({"device": dev_id, "name": desc})
        except Exception as e:
            print(f"扫描仪检测异常: {e}")
        self.write(json.dumps(scanners))

class ScanJobApiHandler(tornado.web.RequestHandler):
    def post(self):
        device = self.get_argument("device", "").strip()
        resolution = self.get_argument("resolution", "300")
        mode = self.get_argument("mode", "Color")
        fmt = self.get_argument("format", "pdf").lower()
        auto_whiten = (self.get_argument("auto_whiten", "true") == "true")

        if not device:
            return self.write(json.dumps({"code": 1, "msg": "未指定扫描仪设备"}))

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        raw_tiff = os.path.join(UPLOAD_DIR, f"scan_{timestamp}.tiff")
        target_name = f"Scan_{timestamp}.{fmt}"
        target_file = os.path.join(SCAN_DIR, target_name)

        cmd = [
            "scanimage",
            "-d", device,
            "--format=tiff",
            f"--resolution={resolution}",
            f"--mode={mode}",
            "-o", raw_tiff
        ]

        try:
            env = dict(os.environ, LC_ALL="C")
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
            if res.returncode != 0 or not os.path.exists(raw_tiff):
                err = res.stderr.strip() or "扫描仪未响应或机械故障"
                return self.write(json.dumps({"code": 1, "msg": err}))

            if auto_whiten:
                auto_process_image(raw_tiff)

            from PIL import Image
            with Image.open(raw_tiff) as im:
                rgb_im = im.convert("RGB")
                if fmt == "pdf":
                    rgb_im.save(target_file, "PDF", resolution=float(resolution))
                else:
                    rgb_im.save(target_file, "JPEG", quality=95)

            if os.path.exists(raw_tiff):
                os.remove(raw_tiff)

            self.write(json.dumps({
                "code": 0,
                "msg": "成功",
                "filename": target_name,
                "url": f"/scans/{target_name}"
            }))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": str(e)}))

class PrintScanFileApiHandler(tornado.web.RequestHandler):
    def post(self):
        fn = self.get_argument("scan_file", "").strip()
        printer = self.get_argument("printer", "").strip()
        if not fn or "/" in fn or "\\" in fn:
            return self.write(json.dumps({"code": 1, "msg": "文件名非法"}))
        
        fp = os.path.join(SCAN_DIR, fn)
        if not os.path.exists(fp):
            return self.write(json.dumps({"code": 1, "msg": "扫描文件不存在"}))

        cmd = ["lp"]
        if printer:
            cmd.extend(["-d", printer])
        cmd.extend([
            "-n", "1",
            "-o", "media=A4",
            "-o", "fit-to-page",
            "-o", "Resolution=600dpi",
            "-o", "pdftops-renderer=gs",
            "-o", "print-quality=5"
        ])
        cmd.append(fp)

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if r.returncode == 0:
                PRINT_HISTORY.insert(0, {
                    "filename": f"[复印] {fn}",
                    "printer": printer or "默认设备",
                    "time": time.strftime("%m/%d %H:%M"),
                    "status": "已出纸"
                })
                if len(PRINT_HISTORY) > 30:
                    del PRINT_HISTORY[30:]
                self.write(json.dumps({"code": 0, "msg": "成功"}))
            else:
                self.write(json.dumps({"code": 1, "msg": r.stderr.strip()}))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": str(e)}))

class ScanFilesApiHandler(tornado.web.RequestHandler):
    def get(self):
        file_list = []
        if os.path.exists(SCAN_DIR):
            for fname in sorted(os.listdir(SCAN_DIR), reverse=True):
                fp = os.path.join(SCAN_DIR, fname)
                if os.path.isfile(fp) and not fname.startswith('.'):
                    stat = os.stat(fp)
                    file_list.append({
                        "name": fname,
                        "size": stat.st_size,
                        "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                        "url": f"/scans/{fname}"
                    })
        self.write(json.dumps(file_list))

class DeleteScanFileApiHandler(tornado.web.RequestHandler):
    def post(self):
        fn = self.get_argument("filename", "").strip()
        if not fn or "/" in fn or "\\" in fn:
            return self.write(json.dumps({"code": 1, "msg": "文件名非法"}))
        fp = os.path.join(SCAN_DIR, fn)
        if os.path.exists(fp):
            try:
                os.remove(fp)
                return self.write(json.dumps({"code": 0}))
            except Exception as e:
                return self.write(json.dumps({"code": 1, "msg": str(e)}))
        self.write(json.dumps({"code": 1, "msg": "文件不存在"}))

def make_app():
    return tornado.web.Application([
        (r"/?", MainH),
        (r"/api/printers", PrnH),
        (r"/api/scan_devices", DevsH),
        (r"/api/installed_ppds", PpdH),
        (r"/api/add_printer", AddH),
        (r"/api/history", HistoryH),
        (r"/api/print", PrintH),
        (r"/api/sane_scanners", SaneScannersApiHandler),
        (r"/api/scan_job", ScanJobApiHandler),
        (r"/api/print_scan_file", PrintScanFileApiHandler),
        (r"/api/scan_files", ScanFilesApiHandler),
        (r"/api/delete_scan_file", DeleteScanFileApiHandler),
        (r"/scans/(.*)", tornado.web.StaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(PORT, address="0.0.0.0")
    tornado.ioloop.IOLoop.current().start()
