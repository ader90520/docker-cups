#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, time, json, subprocess
import tornado.ioloop, tornado.web

try:
    from mail_print import auto_process_image, convert_office_to_pdf
except ImportError:
    auto_process_image = lambda x: True
    convert_office_to_pdf = lambda x: None

PORT = int(os.getenv("WEB_PORT", "8088"))
UPLOAD_DIR, PPD_DIR, SCAN_DIR = "/tmp/cups_web_uploads", "/etc/cups/ppd", os.getenv("SCAN_DIR", "/scans")
for d in (UPLOAD_DIR, PPD_DIR, SCAN_DIR): os.makedirs(d, exist_ok=True)
PRINT_HISTORY = []

def get_uptime_str():
    try:
        with open('/proc/uptime') as f:
            sec = float(f.readline().split()[0])
            d, h = int(sec // 86400), int((sec % 86400) // 3600)
            return f"{d}天{h}小时" if d > 0 else f"{h}小时{int((sec % 3600) // 60)}分钟"
    except: return "1小时内"

def diagnose_printer(name):
    env = dict(os.environ, LC_ALL="C")
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
    except: pass
    try:
        q = subprocess.run(["lpstat", "-o", name], capture_output=True, text=True, env=env, timeout=3)
        jobs = len([l for l in q.stdout.splitlines() if l.strip()])
    except: jobs = 0
    return status, stype, jobs

HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>CUPS 智能控制台</title>
<style>
:root{--p:#00c065;--ph:#00a858;--b:#0284c7;--d:#ef4444;--bg:#f4f6f8;--c:#fff;--bd:#e2e8f0;--tx:#1e293b;--mu:#64748b}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:var(--bg);color:var(--tx);font-size:14px}
.nav{background:var(--c);border-bottom:1px solid var(--bd);padding:12px 28px;display:flex;justify-content:space-between;align-items:center}
.btn{border:none;padding:6px 14px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;display:inline-flex;align-items:center;gap:4px;text-decoration:none}
.btn-p{background:var(--p);color:#fff}.btn-b{background:var(--b);color:#fff}.btn-o{border:1px solid var(--bd);background:var(--c);color:var(--tx)}
.box{max-width:1280px;margin:24px auto;padding:0 20px;display:grid;grid-template-columns:1.55fr 1fr;gap:24px}
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
.uinfo{display:none;align-items:center;justify-content:space-between;background:#f0fdf4;border:1px solid #bbf7d0;padding:10px 14px;border-radius:6px;margin-bottom:16px}
.del-btn{background:#fee2e2;color:var(--d);border:1px solid #fca5a5;padding:3px 8px;border-radius:4px;font-size:12px;cursor:pointer}
.prev-box{background:#e2e8f0;border-radius:8px;padding:16px;display:flex;justify-content:center;align-items:center;min-height:240px}
.paper{background:#fff;box-shadow:0 4px 12px rgba(0,0,0,.1);border-radius:4px;display:flex;justify-content:center;align-items:center;overflow:hidden;transition:.3s}
.paper.p{width:190px;height:268px}.paper.l{width:268px;height:190px}
.st-row{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:#f8fafc;border-radius:6px;margin-bottom:8px;border:1px solid #edf2f7}
.badge{font-size:12px;padding:3px 10px;border-radius:12px;font-weight:600}
.badge-idle{background:#dcfce7;color:#15803d}.badge-busy{background:#e0f2fe;color:#0369a1}.badge-warn{background:#fef9c3;color:#854d0e}.badge-error{background:#fee2e2;color:#b91c1c}
.tray{background:#f8fafc;border:1px solid #edf2f7;border-radius:6px;padding:8px 12px;margin-bottom:6px;display:flex;gap:8px;font-size:12px;color:#475569}
.sub-btn{width:100%;height:44px;background:var(--p);color:#fff;border:none;border-radius:6px;font-size:15px;font-weight:600;cursor:pointer}
.modal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.4);display:none;justify-content:center;align-items:center;z-index:99}
.m-box{background:#fff;width:480px;max-width:92%;border-radius:12px;padding:24px}
</style></head>
<body>
<header class="nav"><div style="font-size:18px;font-weight:700">🖨️ CUPS 控制台 <span style="font-size:12px;color:var(--mu)">admin</span></div>
<div style="display:flex;gap:10px"><button class="btn btn-b" onclick="openModal()">➕ 添加驱动</button><button class="btn btn-p" onclick="loadAll()">🔄 刷新</button><a id="cupsL" target="_blank" class="btn btn-o">⚙️ 631后台</a></div>
</header>
<main class="box">
<section>
  <div class="card">
    <div class="card-h"><span>🖨️ 打印机与文档</span><a href="javascript:openModal()" style="font-size:12px;color:var(--b);text-decoration:none">➕ 安装驱动</a></div>
    <div class="fg" style="margin-bottom:16px"><select id="selP" class="fc" onchange="syncP()"></select></div>
    <div class="drop" id="dz"><input type="file" id="fi" style="display:none"><div style="font-size:28px">📄</div><div style="font-weight:600">点击或将微信文件/照片拖入此处</div><div style="font-size:12px;color:var(--mu)">支持 Word, Excel, PDF, JPG, PNG</div></div>
    <div class="uinfo" id="uib"><div><b id="fn" style="color:#15803d"></b> <span id="fs" style="color:var(--mu)"></span></div><div style="display:flex;gap:8px;align-items:center"><span style="color:var(--p);font-weight:600">✓ 就绪</span><button class="del-btn" onclick="clearF()">❌ 删除</button></div></div>
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
    <div class="fg" style="margin-bottom:18px"><label>👀 纸张仿真预览</label><div class="prev-box"><div class="paper p" id="psheet"><div id="ph" style="text-align:center;color:var(--mu)"><div style="font-size:32px">🖼️</div>预览区</div><img id="pimg" style="display:none;max-width:100%;max-height:100%;object-fit:contain"></div></div></div>
    <button class="sub-btn" onclick="submitPrint()">🖨️ 提交打印</button>
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
let sFile=null, cfg={c:'color',o:'portrait'}, pData=[];
const dz=document.getElementById('dz'), fi=document.getElementById('fi'), pimg=document.getElementById('pimg'), ph=document.getElementById('ph'), psheet=document.getElementById('psheet');
['dragenter','dragover','dragleave','drop'].forEach(e=>{window.addEventListener(e,ev=>{ev.preventDefault();ev.stopPropagation()});dz.addEventListener(e,ev=>{ev.preventDefault();ev.stopPropagation()})});
['dragenter','dragover'].forEach(e=>dz.addEventListener(e,()=>dz.classList.add('drag')));
['dragleave','drop'].forEach(e=>dz.addEventListener(e,()=>dz.classList.remove('drag')));
dz.onclick=()=>fi.click();
dz.ondrop=e=>{if(e.dataTransfer?.files.length)pickF(e.dataTransfer.files[0])};
fi.onchange=function(){if(this.files.length)pickF(this.files[0])};
function pickF(f){
  sFile=f; fn.textContent=f.name; fs.textContent=(f.size/1048576).toFixed(2)+' MB'; uib.style.display='flex';
  if(f.type.startsWith('image/')||/\.(jpg|jpeg|png|bmp|webp)$/i.test(f.name)){
    const r=new FileReader(); r.onload=e=>{pimg.src=e.target.result;pimg.style.display='block';ph.style.display='none';updM()}; r.readAsDataURL(f);
  }else{pimg.style.display='none';ph.style.display='block';ph.innerHTML='<div style="font-size:28px">📑</div>'+f.name}
}
function clearF(){sFile=null;fi.value='';uib.style.display='none';pimg.src='';pimg.style.display='none';ph.style.display='block';ph.innerHTML='<div style="font-size:32px">🖼️</div>预览区'}
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
    if(h.length)hlist.innerHTML=h.map(i=>`<div class="st-row"><div><b>${i.filename}</b><div style="font-size:11px;color:var(--mu)">${i.printer} · ${i.time}</div></div><span class="badge ${i.status==='已出纸'?'badge-idle':'badge-busy'}">${i.status}</span></div>`).join('');
  }catch{}
}
async function submitPrint(){
  if(!sFile)return alert('请先拖入文件！');
  const fd=new FormData();
  fd.append('file',sFile); fd.append('printer',selP.value); fd.append('color',cfg.c); fd.append('orient',cfg.o);
  fd.append('duplex',dup.value); fd.append('copies',cop.value); fd.append('media',med.value); fd.append('paperType',ptype.value);
  fd.append('scale',scale.value); fd.append('pageRange',prange.value); fd.append('mirror',mirr.checked?'true':'false');
  const res=await(await fetch('/api/print',{method:'POST',body:fd})).json();
  if(res.code===0){alert('🎉 打印任务已提交！');clearF();loadHistory();loadPrinters()}else alert('失败:'+res.msg);
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
function loadAll(){loadPrinters();loadHistory()}
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
            if "destination: " in dp.stdout: d = dp.stdout.split("destination: ")[-1].strip()
            ps = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=3)
            for l in ps.stdout.splitlines():
                if l.startswith("printer "):
                    n = l.split()[1].strip()
                    st, stype, j = diagnose_printer(n)
                    r.append({"name": n, "status": st, "status_type": stype, "jobs": j})
        except: pass
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
        except: pass
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
        if not n or not u: return self.write(json.dumps({"code": 1, "msg": "参数缺失"}))
        ppd = os.path.join(PPD_DIR, f"{n}.ppd")
        if 'ppd' in self.request.files:
            with open(ppd, 'wb') as f: f.write(self.request.files['ppd'][0]['body'])
        elif ep and os.path.exists(os.path.join(PPD_DIR, ep)):
            ppd = os.path.join(PPD_DIR, ep)
        else: ppd = None
        cmd = ["lpadmin", "-p", n, "-E", "-v", u]
        cmd.extend(["-P", ppd] if ppd else ["-m", "everywhere"])
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                subprocess.run(["cupsaccept", n]); subprocess.run(["cupsenable", n]); subprocess.run(["lpadmin", "-d", n])
                self.write(json.dumps({"code": 0}))
            else: self.write(json.dumps({"code": 1, "msg": res.stderr.strip()}))
        except Exception as e: self.write(json.dumps({"code": 1, "msg": str(e)}))

class PrintH(tornado.web.RequestHandler):
    def post(self):
        if 'file' not in self.request.files: return self.write(json.dumps({"code": 1, "msg": "未选文件"}))
        f = self.request.files['file'][0]
        fp = os.path.join(UPLOAD_DIR, f['filename'])
        ext = os.path.splitext(f['filename'])[1].lower()
        with open(fp, 'wb') as out: out.write(f['body'])
        if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]: auto_process_image(fp)
        elif ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]:
            pdf = convert_office_to_pdf(fp)
            if pdf: fp = pdf
        p = self.get_argument("printer", "")
        cmd = ["lp"]
        if p: cmd.extend(["-d", p])
        cmd.extend([
            "-n", self.get_argument("copies", "1"),
            "-o", f"media={self.get_argument('media', 'A4')}",
            "-o", "Resolution=600dpi", "-o", "pdftops-renderer=gs", "-o", "print-quality=5"
        ])
        if self.get_argument("scale", "fit-to-page") == "fit-to-page": cmd.extend(["-o", "fit-to-page"])
        if self.get_argument("orient", "portrait") == "landscape": cmd.extend(["-o", "orientation-requested=4"])
        if self.get_argument("color", "color") == "gray": cmd.extend(["-o", "ColorModel=Gray"])
        if self.get_argument("paperType", "plain") == "photo": cmd.extend(["-o", "MediaType=Photo"])
        if self.get_argument("mirror", "false") == "true": cmd.extend(["-o", "mirror"])
        if self.get_argument("duplex", "one-sided") != "one-sided": cmd.extend(["-o", f"sides={self.get_argument('duplex')}"])
        pr = self.get_argument("pageRange", "").strip()
        if pr: cmd.extend(["-o", f"page-ranges={pr}"])
        cmd.append(fp)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if r.returncode == 0:
                PRINT_HISTORY.insert(0, {"filename": f['filename'], "printer": p or "默认", "time": time.strftime("%m/%d %H:%M"), "status": "已出纸"})
                if len(PRINT_HISTORY) > 20: PRINT_HISTORY.pop()
                self.write(json.dumps({"code": 0}))
            else: self.write(json.dumps({"code": 1, "msg": r.stderr.strip()}))
        except Exception as e: self.write(json.dumps({"code": 1, "msg": str(e)}))

def make_app():
    return tornado.web.Application([
        (r"/?", MainH), (r"/api/printers", PrnH), (r"/api/scan_devices", DevsH),
        (r"/api/installed_ppds", PpdH), (r"/api/add_printer", AddH),
        (r"/api/history", PrintH), (r"/api/print", PrintH),
        (r"/scans/(.*)", tornado.web.StaticFileHandler, {"path": SCAN_DIR}),
    ])

if __name__ == "__main__":
    app = make_app()
    app.listen(PORT, address="0.0.0.0")
    tornado.ioloop.IOLoop.current().start()
