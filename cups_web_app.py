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

def get_printer_model(name):
    """从 PPD 驱动文件或 CUPS 详情中提取真实的硬件型号"""
    ppd_file = os.path.join(PPD_DIR, f"{name}.ppd")
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
        r = subprocess.run(["lpstat", "-l", "-p", name], capture_output=True, text=True, timeout=2)
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("Description:"):
                desc = line.split(":", 1)[1].strip()
                if desc:
                    return desc
    except Exception:
        pass
    return ""

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
<title>CUPS 智能控制台</title>
<style>
:root{--p:#00c065;--b:#0284c7;--d:#ef4444;--bg:#f4f6f8;--c:#fff;--bd:#e2e8f0;--tx:#1e293b;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}
.nav{background:var(--c);border-bottom:1px solid var(--bd);padding:10px 24px;display:flex;justify-content:space-between;align-items:center}
.tab-group{display:flex;gap:4px;background:#e2e8f0;padding:3px;border-radius:8px}
.tab-btn{border:none;background:transparent;padding:6px 16px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;color:var(--mu)}
.tab-btn.active{background:#fff;color:var(--tx);box-shadow:0 1px 3px rgba(0,0,0,.08)}
.btn{border:none;padding:6px 14px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:4px}
.btn-p{background:var(--p);color:#fff}.btn-b{background:var(--b);color:#fff}.btn-o{border:1px solid var(--bd);background:var(--c);color:var(--tx)}
.box{max-width:1280px;margin:20px auto;padding:0 20px;display:grid;grid-template-columns:1.55fr 1fr;gap:20px}
@media(max-width:900px){.box{grid-template-columns:1fr}}
.card{background:var(--c);border-radius:10px;border:1px solid var(--bd);padding:20px;margin-bottom:20px}
.card-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #f1f5f9;font-weight:600}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.fg{display:flex;flex-direction:column;gap:6px}.fc{width:100%;height:38px;border:1px solid var(--bd);border-radius:6px;padding:0 12px;outline:none}
.pills{display:flex;border:1px solid var(--bd);border-radius:6px;overflow:hidden;height:38px}
.pill{flex:1;border:none;background:#f8fafc;cursor:pointer;font-size:13px;font-weight:500}.pill.act{background:var(--p);color:#fff;font-weight:600}
.drop{border:2px dashed #cbd5e1;border-radius:8px;padding:20px;text-align:center;cursor:pointer;background:#f8fafc;margin-bottom:16px}
.file-list{display:none;flex-direction:column;gap:8px;margin-bottom:16px}
.file-item{display:flex;align-items:center;justify-content:space-between;background:#f8fafc;border:1px solid var(--bd);padding:8px 12px;border-radius:6px}
.del-btn{background:#fee2e2;color:var(--d);border:1px solid #fca5a5;padding:3px 8px;border-radius:4px;font-size:12px;cursor:pointer}
.st-row{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:#f8fafc;border-radius:6px;margin-bottom:8px;border:1px solid #edf2f7}
.badge{font-size:12px;padding:3px 10px;border-radius:12px;font-weight:600}
.badge-idle{background:#dcfce7;color:#15803d}.badge-busy{background:#e0f2fe;color:#0369a1}.badge-warn{background:#fef9c3;color:#854d0e}.badge-error{background:#fee2e2;color:#b91c1c}
.sub-btn{width:100%;height:44px;background:var(--p);color:#fff;border:none;border-radius:6px;font-size:15px;font-weight:600;cursor:pointer}
.modal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.45);display:none;justify-content:center;align-items:center;z-index:999}
.m-box{background:#fff;width:480px;max-width:92%;border-radius:12px;padding:24px;box-shadow:0 8px 24px rgba(0,0,0,.15)}
</style></head>
<body>
<header class="nav">
  <div style="display:flex;align-items:center;gap:16px">
    <div style="font-size:18px;font-weight:700">🖨️ CUPS 智能打印与扫描控制台</div>
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
    <div class="card-h">
      <span>🖨️ 打印机与上传队列</span>
      <a href="javascript:openModal()" style="font-size:12px;color:var(--b);text-decoration:none">➕ 安装新驱动</a>
    </div>
    <div class="fg" style="margin-bottom:16px"><select id="selP" class="fc" onchange="syncP()"></select></div>
    <div class="drop" id="dz"><input type="file" id="fi" multiple style="display:none"><div style="font-size:28px">📑</div><div>点击或拖入照片/PDF文件 (支持多文件批量)</div></div>
    <div class="file-list" id="flist"></div>
  </div>
  <div class="card">
    <div class="card-h"><span>⚲ 打印参数</span></div>
    <div class="row">
      <div class="fg"><label>色彩</label><div class="pills"><button class="pill act" id="bCol" onclick="setOpt('c','color')">彩色</button><button class="pill" id="bGray" onclick="setOpt('c','gray')">黑白</button></div></div>
      <div class="fg"><label>方向</label><div class="pills"><button class="pill act" id="bPor" onclick="setOpt('o','portrait')">纵向</button><button class="pill" id="bLan" onclick="setOpt('o','landscape')">横向</button></div></div>
    </div>
    <div class="row">
      <div class="fg"><label>份数</label><input type="number" id="cop" class="fc" value="1" min="1" max="99"></div>
      <div class="fg"><label>纸张</label><select id="med" class="fc"><option value="A4">A4</option><option value="A5">A5</option><option value="A6">A6</option></select></div>
    </div>
    <button class="sub-btn" id="submitBtn" onclick="submitPrint()">🖨️ 立即出纸打印</button>
  </div>
</section>
<section>
  <div class="card">
    <div class="card-h"><span>📈 打印机状态</span></div>
    <div class="st-row"><span>硬件型号</span><span id="stM" style="font-weight:600;color:var(--b);max-width:60%;text-align:right;word-break:break-all">-</span></div>
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
    <div class="card-h"><span>📠 扫描仪设备与参数</span><button class="btn btn-p" onclick="scanHardwareScanners()">🔍 重新探测</button></div>
    <div class="fg" style="margin-bottom:14px">
      <label>选择扫描仪设备</label>
      <select id="scannerSelect" class="fc"><option value="">探测中...</option></select>
    </div>
    <div class="row">
      <div class="fg"><label>分辨率 (DPI)</label>
        <select id="scanResolution" class="fc">
          <option value="300" selected>300 DPI (推荐)</option>
          <option value="150">150 DPI (快速)</option>
          <option value="600">600 DPI (高清)</option>
        </select>
      </div>
      <div class="fg"><label>色彩模式</label>
        <select id="scanMode" class="fc">
          <option value="Color">彩色 (Color)</option>
          <option value="Gray">灰度 (Gray)</option>
          <option value="Lineart">黑白线条</option>
        </select>
      </div>
    </div>
    <div class="row">
      <div class="fg"><label>格式</label>
        <select id="scanFormat" class="fc">
          <option value="pdf">PDF 文档 (.pdf)</option>
          <option value="jpg">JPG 图片 (.jpg)</option>
        </select>
      </div>
      <div class="fg"><label>画质优化</label>
        <label style="display:flex;align-items:center;gap:8px;height:38px;cursor:pointer">
          <input type="checkbox" id="scanAutoWhiten" checked style="accent-color:var(--p);width:16px;height:16px">
          <span>底噪消除与纠偏</span>
        </label>
      </div>
    </div>
    <button class="sub-btn" id="startScanBtn" onclick="doStartScan()">🚀 开始扫描</button>
  </div>
  
  <div class="card">
    <div class="card-h">
      <span>👀 扫描预览与复印</span>
      <div id="scanActionToolbar" style="display:none;gap:8px;align-items:center">
        <button class="btn btn-p" onclick="printCurrentScan()">🖨️ 立即打印此件</button>
        <a id="scanDownloadBtn" class="btn btn-o" download style="font-size:12px;padding:5px 10px">⬇️ 下载</a>
      </div>
    </div>
    <div style="background:#e2e8f0;border-radius:8px;padding:16px;display:flex;justify-content:center;align-items:center;min-height:280px">
      <div style="background:#fff;border-radius:4px;overflow:hidden;width:220px;height:310px;display:flex;justify-content:center;align-items:center" id="scanPaperSheet">
        <div id="scanEmptyTip" style="text-align:center;color:var(--mu)"><div style="font-size:36px">📠</div>就绪等待扫描</div>
        <img id="scanPrevImg" style="display:none;max-width:100%;max-height:100%;object-fit:contain">
        <iframe id="scanPrevPdf" style="display:none;width:100%;height:100%;border:none"></iframe>
      </div>
    </div>
  </div>
</section>
<section>
  <div class="card">
    <div class="card-h"><span>📁 扫描文件库 (/scans)</span><button class="btn btn-o" onclick="loadScanFiles()">刷新</button></div>
    <div id="scanFileList" style="display:flex;flex-direction:column;gap:8px"></div>
  </div>
</section>
</main>

<div class="modal" id="mD">
  <div class="m-box">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <b style="font-size:16px">➕ 添加与绑定打印机驱动</b>
      <span style="cursor:pointer;font-size:22px;color:var(--mu)" onclick="closeModal()">&times;</span>
    </div>
    <div class="fg" style="margin-bottom:10px">
      <label>1. 探测物理硬件 (USB/网络)</label>
      <div style="display:flex;gap:6px">
        <select id="devs" class="fc" onchange="devUri.value=this.value"></select>
        <button class="btn btn-p" onclick="scanDevs()">探测</button>
      </div>
    </div>
    <div class="fg" style="margin-bottom:10px">
      <label>设备 URI (硬件地址)</label>
      <input type="text" id="devUri" class="fc" placeholder="usb://HP/LaserJet%201020...">
    </div>
    <div class="fg" style="margin-bottom:10px">
      <label>打印机英文标识 (勿填中文)</label>
      <input type="text" id="pName" class="fc" placeholder="如 HP_1020">
    </div>
    <div class="fg" style="margin-bottom:14px">
      <label>驱动选择模式</label>
      <div style="display:flex;gap:12px;margin-bottom:6px">
        <label><input type="radio" name="pm" value="e" checked onchange="togPpd()"> 复用现有 PPD 驱动</label>
        <label><input type="radio" name="pm" value="u" onchange="togPpd()"> 上传本地 .ppd 驱动</label>
      </div>
      <select id="ePpd" class="fc"></select>
      <input type="file" id="uPpd" class="fc" accept=".ppd" style="display:none;padding-top:6px">
    </div>
    <button class="sub-btn" onclick="submitAddP()">🚀 立即绑定并启用打印机</button>
  </div>
</div>

<script>
document.getElementById('cupsL').href='http://'+location.hostname+':631';
let filesArr = [], cfg={c:'color',o:'portrait'}, pData=[], currentScanFile=null;
const dz=document.getElementById('dz'), fi=document.getElementById('fi'), flist=document.getElementById('flist'), mD=document.getElementById('mD');

function switchTab(t){
  document.getElementById('tabPrintBtn').classList.toggle('active', t==='print');
  document.getElementById('tabScanBtn').classList.toggle('active', t==='scan');
  document.getElementById('printView').style.display = (t==='print') ? 'grid' : 'none';
  document.getElementById('scanView').style.display = (t==='scan') ? 'grid' : 'none';
  if(t==='scan'){ scanHardwareScanners(); loadScanFiles(); }
}

dz.onclick=()=>fi.click();
fi.onchange=function(){for(let f of this.files) filesArr.push(f); renderFiles()};

function renderFiles(){
  if(!filesArr.length){ flist.style.display='none'; return; }
  flist.style.display='flex';
  flist.innerHTML = filesArr.map((f, i)=>`<div class="file-item"><span>${f.name}</span><button class="del-btn" onclick="filesArr.splice(${i},1);renderFiles()">删除</button></div>`).join('');
}

function setOpt(k,v){
  cfg[k]=v;
  if(k==='c'){ bCol.classList.toggle('act',v==='color'); bGray.classList.toggle('act',v==='gray'); }
  else { bPor.classList.toggle('act',v==='portrait'); bLan.classList.toggle('act',v==='landscape'); }
}

function syncP(){
  const t=pData.find(p=>p.name===selP.value); if(!t)return;
  document.getElementById('stM').textContent = t.model || t.name;
  document.getElementById('stB').textContent = t.status;
  document.getElementById('stB').className = 'badge badge-' + t.status_type;
  document.getElementById('stJ').textContent = t.jobs;
}

async function loadPrinters(){
  try{
    const r=await(await fetch('/api/printers?_t='+Date.now())).json(); pData=r.printers||[]; selP.innerHTML='';
    if(!pData.length){
      selP.innerHTML = '<option value="">(尚未添加打印机，请点击上方添加驱动)</option>';
      document.getElementById('stM').textContent = '未添加设备';
      document.getElementById('stB').textContent = '无设备';
      document.getElementById('stB').className = 'badge badge-warn';
      return;
    }
    pData.forEach(p=>{
      const o=document.createElement('option');
      o.value=p.name;
      o.textContent=`${p.display_name} [${p.status}]`;
      if(p.name===r.default) o.selected=true;
      selP.appendChild(o);
    });
    syncP();
    if(r.uptime) document.getElementById('stU').textContent=r.uptime;
  }catch{}
}

async function loadHistory(){
  try{
    const h=await(await fetch('/api/history?_t='+Date.now())).json();
    if(h.length) document.getElementById('hlist').innerHTML=h.map(i=>`<div class="st-row"><div><b>${i.filename}</b><div style="font-size:11px;color:var(--mu)">${i.printer} ·${i.time}</div></div><span class="badge ${i.status==='已出纸'?'badge-idle':'badge-busy'}">${i.status}</span></div>`).join('');
  }catch{}
}

async function submitPrint(){
  if(!filesArr.length) return alert('请先选择或拖入文件！');
  const fd=new FormData();
  for(let f of filesArr) fd.append('files', f);
  fd.append('printer',selP.value); fd.append('color',cfg.c); fd.append('orient',cfg.o);
  fd.append('copies',cop.value); fd.append('media',med.value);
  const res=await(await fetch('/api/print',{method:'POST',body:fd})).json();
  if(res.code===0){ alert('🎉 提交打印成功！'); filesArr=[]; renderFiles(); loadHistory(); loadPrinters(); }
  else alert('失败:'+res.msg);
}

// 驱动添加弹窗逻辑
function openModal(){ mD.style.display='flex'; scanDevs(); loadEp(); }
function closeModal(){ mD.style.display='none'; }
function togPpd(){ const u=document.querySelector('input[name="pm"]:checked').value==='u'; document.getElementById('ePpd').style.display=u?'none':'block'; document.getElementById('uPpd').style.display=u?'block':'none'; }

async function scanDevs(){
  const devs = document.getElementById('devs');
  devs.innerHTML='<option>扫描物理硬件中...</option>';
  try{
    const l=await(await fetch('/api/scan_devices?_t='+Date.now())).json(); devs.innerHTML='';
    if(!l.length){ devs.innerHTML='<option value="">未检测到物理设备(请检查USB)</option>'; return; }
    l.forEach((d,i)=>{
      const o=document.createElement('option'); o.value=d.uri; o.textContent=d.name; devs.appendChild(o);
      if(i===0){ document.getElementById('devUri').value=d.uri; document.getElementById('pName').value=d.name.replace(/[^a-zA-Z0-9_]/g,'_'); }
    });
  }catch{}
}

async function loadEp(){
  const ePpd = document.getElementById('ePpd');
  try{
    const l=await(await fetch('/api/installed_ppds?_t='+Date.now())).json(); ePpd.innerHTML='';
    if(!l.length){ ePpd.innerHTML='<option value="">(无现有驱动，请勾选上传)</option>'; return; }
    l.forEach(p=>{ const o=document.createElement('option'); o.value=p.filename; o.textContent=`📄 ${p.name}`; ePpd.appendChild(o); });
  }catch{}
}

async function submitAddP(){
  const pName = document.getElementById('pName').value.trim();
  const devUri = document.getElementById('devUri').value.trim();
  if(!pName || !devUri) return alert('请先填写名称与URI');
  const fd=new FormData();
  fd.append('name', pName);
  fd.append('uri', devUri);
  if(document.querySelector('input[name="pm"]:checked').value==='e') {
    fd.append('existing_ppd', document.getElementById('ePpd').value);
  } else if(document.getElementById('uPpd').files.length) {
    fd.append('ppd', document.getElementById('uPpd').files[0]);
  }
  const r=await(await fetch('/api/add_printer',{method:'POST',body:fd})).json();
  if(r.code===0){ alert('🎉 驱动绑定并启用成功！'); closeModal(); loadPrinters(); }
  else alert('驱动安装失败: ' + r.msg);
}

// 扫描仪逻辑
async function scanHardwareScanners(){
  const s=document.getElementById('scannerSelect'); s.innerHTML='<option>探测硬件中...</option>';
  try{
    const list=await(await fetch('/api/sane_scanners?_t='+Date.now())).json(); s.innerHTML='';
    if(!list.length){ s.innerHTML='<option value="">未找到扫描仪 (请检查 USB 连接)</option>'; return; }
    list.forEach(i=>{const o=document.createElement('option');o.value=i.device;o.textContent=`${i.name} (${i.device})`;s.appendChild(o);});
  }catch{ s.innerHTML='<option value="">探测请求失败</option>'; }
}

async function doStartScan(){
  const d=document.getElementById('scannerSelect').value;
  if(!d) return alert('请先确认已连接并探测到扫描仪设备！');
  const btn=document.getElementById('startScanBtn');
  btn.disabled=true; btn.textContent='⏳ 正在扫描并传输数据 (约10-25秒)...';
  
  const fd=new FormData();
  fd.append('device', d);
  fd.append('resolution', document.getElementById('scanResolution').value);
  fd.append('mode', document.getElementById('scanMode').value);
  fd.append('format', document.getElementById('scanFormat').value);
  fd.append('auto_whiten', document.getElementById('scanAutoWhiten').checked?'true':'false');
  
  try{
    const res=await(await fetch('/api/scan_job',{method:'POST',body:fd})).json();
    if(res.code===0){
      currentScanFile = res.filename;
      alert('🎉 扫描成功！');
      document.getElementById('scanActionToolbar').style.display='flex';
      const dlBtn=document.getElementById('scanDownloadBtn'); dlBtn.href=res.url; dlBtn.download=res.filename;
      document.getElementById('scanEmptyTip').style.display='none';
      const pimg=document.getElementById('scanPrevImg'), ppdf=document.getElementById('scanPrevPdf');
      if(res.url.toLowerCase().endsWith('.pdf')){ pimg.style.display='none'; ppdf.src=res.url+'#toolbar=0'; ppdf.style.display='block'; }
      else { ppdf.style.display='none'; pimg.src=res.url+'?_t='+Date.now(); pimg.style.display='block'; }
      loadScanFiles();
    } else { alert('扫描失败: '+res.msg); }
  }catch(e){ alert('通信异常: '+e); }
  finally{ btn.disabled=false; btn.textContent='🚀 开始扫描'; }
}

async function printCurrentScan(){
  if(!currentScanFile) return alert('当前没有可打印的扫描文件！');
  const selPrinter = document.getElementById('selP').value;
  if(!confirm(`确认使用打印机 [${selPrinter \vert{}\vert{} '默认设备'}] 打印文件 "${currentScanFile}" 吗？`)) return;
  const fd = new FormData();
  fd.append('scan_file', currentScanFile);
  fd.append('printer', selPrinter);
  try{
    const res = await (await fetch('/api/print_scan_file', { method: 'POST', body: fd })).json();
    if(res.code === 0){ alert('🎉 已提交打印机出纸！'); loadHistory(); }
    else { alert('打印失败: ' + res.msg); }
  }catch(e){ alert('请求异常: ' + e); }
}

async function loadScanFiles(){
  const box=document.getElementById('scanFileList');
  try{
    const l=await(await fetch('/api/scan_files?_t='+Date.now())).json();
    if(!l.length){ box.innerHTML='<div style="text-align:center;padding:12px;color:var(--mu)">暂无文件</div>'; return; }
    box.innerHTML=l.map(f=>`
      <div class="st-row">
        <div>
          <a href="${f.url}" target="_blank" style="font-weight:600;color:var(--b);text-decoration:none">${f.name}</a>
          <div style="font-size:11px;color:var(--mu)">${f.time} ·${(f.size/1048576).toFixed(2)}MB</div>
        </div>
        <div style="display:flex;gap:6px">
          <a href="${f.url}" download class="btn btn-o" style="padding:2px 8px;font-size:11px">下载</a>
          <button class="del-btn" onclick="deleteScanFile('${f.name}')">删除</button>
        </div>
      </div>
    `).join('');
  }catch{}
}

async function deleteScanFile(fn){
  if(!confirm(`确定删除扫描文件 ${fn} 吗？`)) return;
  const fd = new FormData(); fd.append('filename', fn);
  const r = await(await fetch('/api/delete_scan_file', { method:'POST', body:fd })).json();
  if(r.code === 0) loadScanFiles();
}

function loadAll(){loadPrinters();loadHistory();loadScanFiles();}
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
                    model = get_printer_model(n)
                    disp = f"{n} ({model})" if model and model != n else n
                    st, stype, j = diagnose_printer(n)
                    r.append({"name": n, "model": model or n, "display_name": disp, "status": st, "status_type": stype, "jobs": j})
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
        files = self.request.files.get('files', [])
        if not files: return self.write(json.dumps({"code":1,"msg":"无文件"}))
        p = self.get_argument("printer", "")
        copies = self.get_argument("copies", "1")
        media = self.get_argument("media", "A4")
        orient = self.get_argument("orient", "portrait")
        color = self.get_argument("color", "color")
        
        for f_obj in files:
            fp = os.path.join(UPLOAD_DIR, f_obj['filename'])
            with open(fp, "wb") as f: f.write(f_obj['body'])
            cmd = ["lp"]
            if p: cmd.extend(["-d", p])
            cmd.extend(["-n", str(copies), "-o", f"media={media}", "-o", "fit-to-page"])
            if orient == "landscape": cmd.extend(["-o", "orientation-requested=4"])
            if color == "gray": cmd.extend(["-o", "ColorModel=Gray"])
            cmd.append(fp)
            subprocess.run(cmd, capture_output=True, timeout=30)
            PRINT_HISTORY.insert(0, {"filename": f_obj['filename'], "printer": p or "默认", "time": time.strftime("%H:%M"), "status": "已出纸"})
        self.write(json.dumps({"code":0}))

class SaneH(tornado.web.RequestHandler):
    def get(self):
        scanners = []
        try:
            env = dict(os.environ, LC_ALL="C")
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
        fmt = self.get_argument("format", "pdf").lower()
        auto_whiten = (self.get_argument("auto_whiten", "true") == "true")

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        os.makedirs(SCAN_DIR, exist_ok=True)

        t = time.strftime("%Y%m%d_%H%M%S")
        target_name = f"Scan_{t}.{fmt}"
        target = os.path.join(SCAN_DIR, target_name)
        raw = os.path.join(UPLOAD_DIR, f"raw_{t}.tiff")

        env = dict(os.environ, LC_ALL="C")
        cmd = ["scanimage", "--format=tiff", f"--resolution={resolution}", f"--mode={mode}", "-o", raw]
        if dev: cmd.extend(["-d", dev])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
            if res.returncode != 0 or not os.path.exists(raw) or os.path.getsize(raw) == 0:
                err_msg = res.stderr.strip() or res.stdout.strip() or "扫描仪未响应或连接中断"
                return self.write(json.dumps({"code": 1, "msg": f"硬件扫描失败: {err_msg}"}))

            if auto_whiten:
                auto_process_image(raw)

            from PIL import Image
            with Image.open(raw) as im:
                rgb_im = im.convert("RGB")
                if fmt == "pdf":
                    rgb_im.save(target, "PDF", resolution=float(resolution))
                else:
                    rgb_im.save(target, "JPEG", quality=95)

            if os.path.exists(raw):
                os.remove(raw)

            self.write(json.dumps({"code": 0, "msg": "成功", "filename": target_name, "url": f"/scans/{target_name}"}))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": f"系统处理异常: {str(e)}"}))

class PrintScanFileH(tornado.web.RequestHandler):
    def post(self):
        fn = self.get_argument("scan_file", "").strip()
        printer = self.get_argument("printer", "").strip()
        if not fn or "/" in fn or "\\" in fn:
            return self.write(json.dumps({"code": 1, "msg": "文件名非法"}))
        fp = os.path.join(SCAN_DIR, fn)
        if not os.path.exists(fp):
            return self.write(json.dumps({"code": 1, "msg": "扫描文件不存在"}))

        cmd = ["lp"]
        if printer: cmd.extend(["-d", printer])
        cmd.extend(["-n", "1", "-o", "media=A4", "-o", "fit-to-page"])
        cmd.append(fp)

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if r.returncode == 0:
                PRINT_HISTORY.insert(0, {"filename": f"[复印] {fn}", "printer": printer or "默认设备", "time": time.strftime("%H:%M"), "status": "已出纸"})
                self.write(json.dumps({"code": 0}))
            else:
                self.write(json.dumps({"code": 1, "msg": r.stderr.strip()}))
        except Exception as e:
            self.write(json.dumps({"code": 1, "msg": str(e)}))

class ScanFilesH(tornado.web.RequestHandler):
    def get(self):
        l = []
        if os.path.exists(SCAN_DIR):
            for fn in sorted(os.listdir(SCAN_DIR), reverse=True):
                if not fn.startswith('.'):
                    fp = os.path.join(SCAN_DIR, fn)
                    stat = os.stat(fp)
                    l.append({"name": fn, "size": stat.st_size, "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)), "url": f"/scans/{fn}"})
        self.write(json.dumps(l))

class DeleteScanFileH(tornado.web.RequestHandler):
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
        (r"/api/sane_scanners", SaneH),
        (r"/api/scan_job", ScanJobH),
        (r"/api/print_scan_file", PrintScanFileH),
        (r"/api/scan_files", ScanFilesH),
        (r"/api/delete_scan_file", DeleteScanFileH),
        (r"/scans/(.*)", tornado.web.StaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(PORT, address="0.0.0.0")
    tornado.ioloop.IOLoop.current().start()
