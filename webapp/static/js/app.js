/**
 * CUPS 智能工作台前端核心交互逻辑 (精简收敛版)
 */

const AppConfig = {
    currentColorMode: 'monochrome',
    currentOrientation: 'portrait',
    currentSubTab: 'normal',
    activePrinter: ''
};

// 顶栏面板切换
function switchNavTab(tabName) {
    document.querySelectorAll('.nav-tabs .nav-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-panel-container').forEach(panel => panel.classList.remove('active'));

    const btn = event.currentTarget;
    if (btn) btn.classList.add('active');

    const targetPanel = document.getElementById(`panel-${tabName}`);
    if (targetPanel) targetPanel.classList.add('active');

    if (tabName === 'devices') {
        DashboardService.loadPrinters(true);
    }
}

// 打印业务管理器 (收敛发票独立逻辑，统一由 normal 队列与智能拼版调度)
const PrintManager = {
    files: {
        normal: []
    },

    switchSubTab: function(tabKey) {
        AppConfig.currentSubTab = tabKey;
        document.querySelectorAll('.sub-type-nav .type-item').forEach(el => el.classList.remove('active'));
        const tabEl = document.getElementById(`tab-sub-${tabKey}`);
        if (tabEl) tabEl.classList.add('active');

        const secNormal = document.getElementById('sec-upload-normal');
        const secIdCard = document.getElementById('sec-upload-idcard');
        if (secNormal) secNormal.style.display = (tabKey === 'normal') ? 'block' : 'none';
        if (secIdCard) secIdCard.style.display = (tabKey === 'idcard') ? 'block' : 'none';

        if (tabKey === 'idcard') {
            IdCardManager.renderPreview();
        } else {
            EnhanceEngine.renderPreview();
        }
    },

    setColorMode: function(mode) {
        AppConfig.currentColorMode = mode;
        const btnMono = document.getElementById('btn-color-mono');
        const btnRgb = document.getElementById('btn-color-rgb');
        if (mode === 'monochrome') {
            btnMono.classList.add('active');
            btnRgb.classList.remove('active');
        } else {
            btnRgb.classList.add('active');
            btnMono.classList.remove('active');
        }
        EnhanceEngine.invalidateCache();
        EnhanceEngine.renderPreview();
    },

    setOrientation: function(orient) {
        AppConfig.currentOrientation = orient;
        const btnPort = document.getElementById('btn-orient-port');
        const btnLand = document.getElementById('btn-orient-land');
        if (orient === 'portrait') {
            btnPort.classList.add('active');
            btnLand.classList.remove('active');
        } else {
            btnLand.classList.add('active');
            btnPort.classList.remove('active');
        }
        EnhanceEngine.renderPreview();
    },

    onFilesSelected: function(type, fileList) {
        if (!fileList || fileList.length === 0) return;
        this.files.normal = Array.from(fileList);
        const listContainer = document.getElementById('file-list-normal');
        if (listContainer) {
            listContainer.style.display = 'block';
            listContainer.innerHTML = this.files.normal.map(f => `<div style="font-size:11px; padding:2px 0;">📄 ${f.name} (${Math.round(f.size/1024)} KB)</div>`).join('');
        }
        EnhanceEngine.currentPage = 0;
        EnhanceEngine.renderPreview();
    },

    clearFiles: function(type) {
        if (type === 'idcard') {
            IdCardManager.clearAll();
            return;
        }
        this.files.normal = [];
        const listContainer = document.getElementById('file-list-normal');
        if (listContainer) {
            listContainer.style.display = 'none';
            listContainer.innerHTML = '';
        }
        const fileInput = document.getElementById('file-input-normal');
        if (fileInput) fileInput.value = '';
        EnhanceEngine.clearViewport();
    },

    onMergeChange: function() {
        EnhanceEngine.renderPreview();
    },

    onPaperSizeChange: function() {
        EnhanceEngine.renderPreview();
    },

    submitJob: async function() {
        const printer = document.getElementById('printer-select').value;
        const alertBox = document.getElementById('print-alert');
        if (!printer) {
            alertBox.innerHTML = '<span style="color:#ef4444;">请先选择可用打印机！</span>';
            return;
        }

        const fd = new FormData();
        fd.append('printer', printer);
        fd.append('copies', document.getElementById('p-copies').value || '1');
        fd.append('duplex', document.getElementById('p-duplex').value || 'none');
        fd.append('color_mode', AppConfig.currentColorMode);
        fd.append('media', document.getElementById('p-media').value || 'A4');
        fd.append('enhance', document.getElementById('p-enhance').value || 'true');
        fd.append('page_ranges', document.getElementById('p-page-ranges').value || '');

        let fileQueue = [];
        if (AppConfig.currentSubTab === 'normal') {
            fileQueue = this.files.normal;
            const isMerge = document.getElementById('chk-normal-merge').checked;
            fd.append('multi_merge', isMerge ? 'true' : 'false');
        } else if (AppConfig.currentSubTab === 'idcard') {
            const idCardBlob = await IdCardManager.generateA4CanvasBlob();
            if (!idCardBlob) {
                alertBox.innerHTML = '<span style="color:#ef4444;">请至少上传一张身份证人像面或国徽面！</span>';
                return;
            }
            fileQueue = [new File([idCardBlob], 'idcard_merged.jpg', { type: 'image/jpeg' })];
            fd.append('multi_merge', 'false');
        }

        if (fileQueue.length === 0) {
            alertBox.innerHTML = '<span style="color:#ef4444;">请先选取待打印文档或图片！</span>';
            return;
        }

        fileQueue.forEach(f => fd.append('file', f));
        alertBox.innerHTML = '<span style="color:#0284c7;">正在提交任务至 CUPS 队列...</span>';

        try {
            const resp = await fetch('/api/print', { method: 'POST', body: fd });
            const res = await resp.json();
            if (res.success) {
                alertBox.innerHTML = `<span style="color:#10b981;">✔ 打印任务已下发 (作业 ID: ${res.job || '已就绪'})</span>`;
                DashboardService.loadPrinters(false);
            } else {
                alertBox.innerHTML = `<span style="color:#ef4444;">下发失败: ${res.msg}</span>`;
            }
        } catch (e) {
            alertBox.innerHTML = `<span style="color:#ef4444;">通信异常: ${e.message}</span>`;
        }
    }
};

// 身份证管理对象 (等比例 85.6:54 卡片画幅管理与合成)
const IdCardManager = {
    persons: [
        { front: null, back: null }
    ],

    onSideUploaded: function(index, side, files) {
        if (!files || files.length === 0) return;
        const file = files[0];
        if (!this.persons[index]) {
            this.persons[index] = { front: null, back: null };
        }
        this.persons[index][side] = file;

        const reader = new FileReader();
        reader.onload = function(e) {
            const img = document.getElementById(`preview-${side}-${index}`);
            const ph = document.getElementById(`placeholder-${side}-${index}`);
            if (img && ph) {
                img.src = e.target.result;
                img.style.display = 'block';
                ph.style.display = 'none';
            }
            IdCardManager.renderPreview();
        };
        reader.readAsDataURL(file);
    },

    addPerson: function() {
        if (this.persons.length >= 3) {
            alert('单张 A4 纸最多支持合并打印 3 人身份证！');
            return;
        }
        const index = this.persons.length;
        this.persons.push({ front: null, back: null });

        const container = document.getElementById('idcard-person-container');
        if (!container) return;

        const card = document.createElement('div');
        card.className = 'idcard-person-card';
        card.id = `idcard-person-${index}`;
        card.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <div class="idcard-person-title">第 ${index + 1} 人身份证信息</div>
                <button type="button" class="btn-del" onclick="IdCardManager.removePerson(${index})">删除此人</button>
            </div>
            <div class="idcard-ratio-grid">
                <div class="idcard-ratio-box" id="idcard-slot-front-${index}" onclick="document.getElementById('file-idcard-front-${index}').click()">
                    <div class="idcard-ratio-placeholder" id="placeholder-front-${index}">
                        <div class="idcard-ratio-icon">👤</div>
                        <div class="idcard-ratio-text">正面 (人像面)</div>
                        <div class="idcard-ratio-sub">点击或拖拽上传正面</div>
                    </div>
                    <img id="preview-front-${index}" class="idcard-ratio-img" style="display:none;" alt="正面">
                    <input type="file" id="file-idcard-front-${index}" accept="image/*" style="display:none;" onchange="IdCardManager.onSideUploaded(${index}, 'front', this.files)">
                </div>

                <div class="idcard-ratio-box" id="idcard-slot-back-${index}" onclick="document.getElementById('file-idcard-back-${index}').click()">
                    <div class="idcard-ratio-placeholder" id="placeholder-back-${index}">
                        <div class="idcard-ratio-icon">🇨🇳</div>
                        <div class="idcard-ratio-text">反面 (国徽面)</div>
                        <div class="idcard-ratio-sub">点击或拖拽上传反面</div>
                    </div>
                    <img id="preview-back-${index}" class="idcard-ratio-img" style="display:none;" alt="反面">
                    <input type="file" id="file-idcard-back-${index}" accept="image/*" style="display:none;" onchange="IdCardManager.onSideUploaded(${index}, 'back', this.files)">
                </div>
            </div>
        `;
        container.appendChild(card);
    },

    removePerson: function(index) {
        const card = document.getElementById(`idcard-person-${index}`);
        if (card) card.remove();
        this.persons.splice(index, 1);
        this.renderPreview();
    },

    clearAll: function() {
        this.persons = [{ front: null, back: null }];
        const container = document.getElementById('idcard-person-container');
        if (container) container.innerHTML = '';
        ['front', 'back'].forEach(side => {
            const img = document.getElementById(`preview-${side}-0`);
            const ph = document.getElementById(`placeholder-${side}-0`);
            const fi = document.getElementById(`file-idcard-${side}-0`);
            if (img) img.style.display = 'none';
            if (ph) ph.style.display = 'block';
            if (fi) fi.value = '';
        });
        EnhanceEngine.clearViewport();
    },

    generateA4CanvasBlob: function() {
        return new Promise((resolve) => {
            const validSides = [];
            this.persons.forEach(p => {
                if (p.front) validSides.push(p.front);
                if (p.back) validSides.push(p.back);
            });
            if (validSides.length === 0) {
                resolve(null);
                return;
            }

            const canvas = document.createElement('canvas');
            canvas.width = 1654;
            canvas.height = 2338;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, canvas.width, canvas.height);

            const cardW = 674;
            const cardH = 425;
            const posX = (canvas.width - cardW) / 2;

            let loaded = 0;
            validSides.forEach((file, idx) => {
                const img = new Image();
                img.onload = () => {
                    const posY = 350 + idx * (cardH + 120);
                    if (posY + cardH < canvas.height) {
                        ctx.drawImage(img, posX, posY, cardW, cardH);
                    }
                    loaded++;
                    if (loaded === validSides.length) {
                        canvas.toBlob(blob => resolve(blob), 'image/jpeg', 0.95);
                    }
                };
                img.src = URL.createObjectURL(file);
            });
        });
    },

    renderPreview: async function() {
        const blob = await this.generateA4CanvasBlob();
        if (!blob) {
            EnhanceEngine.clearViewport();
            return;
        }
        const img = new Image();
        img.onload = () => {
            const canvas = document.getElementById('canvas-preview');
            const tip = document.getElementById('viewport-empty-tip');
            if (canvas && tip) {
                tip.style.display = 'none';
                canvas.style.display = 'block';
                canvas.width = img.width;
                canvas.height = img.height;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
            }
        };
        img.src = URL.createObjectURL(blob);
    }
};

// 视窗渲染与图像增强调度引擎
const EnhanceEngine = {
    currentPage: 0,
    totalPages: 0,

    clearViewport: function() {
        const canvas = document.getElementById('canvas-preview');
        const pdfCanvas = document.getElementById('pdf-canvas-render');
        const tip = document.getElementById('viewport-empty-tip');
        if (canvas) canvas.style.display = 'none';
        if (pdfCanvas) pdfCanvas.style.display = 'none';
        if (tip) tip.style.display = 'block';
        document.getElementById('page-counter-lbl').innerText = '0 / 0';
    },

    invalidateCache: function() {},

    renderPreview: async function() {
        const files = PrintManager.files.normal;
        if (!files || files.length === 0) {
            this.clearViewport();
            return;
        }

        const isMerge = document.getElementById('chk-normal-merge').checked;

        // 如果开启了多图智能拼版且上传了超过1张图片，直接走前端网格多图拼接预览
        if (isMerge && files.length > 1) {
            this.totalPages = 1;
            document.getElementById('page-counter-lbl').innerText = '1 / 1';
            this.renderMultiMergePreview(files);
            return;
        }

        const currentFile = files[this.currentPage] || files[0];
        this.totalPages = files.length;
        document.getElementById('page-counter-lbl').innerText = `${this.currentPage + 1} / ${this.totalPages}`;

        const isEnhance = document.getElementById('p-enhance').value === 'true';
        let uploadPayload = currentFile;

        if (isEnhance && window.DocAI && window.DocAI.readyStatus && window.DocAI.readyStatus["corner"]) {
            try {
                const cropped = await window.DocAI.processDocument(currentFile);
                if (cropped) uploadPayload = cropped;
            } catch (e) {
                console.warn('端侧 AI 切边跳过:', e);
            }
        }

        const fd = new FormData();
        fd.append('file', uploadPayload, currentFile.name);
        fd.append('color_mode', AppConfig.currentColorMode);
        if (uploadPayload !== currentFile) fd.append('client_cropped', 'true');

        try {
            const resp = await fetch('/api/preview/enhance', { method: 'POST', body: fd });
            if (!resp.ok) throw new Error('服务端预览异常');
            const blob = await resp.blob();

            const img = new Image();
            img.onload = () => {
                const canvas = document.getElementById('canvas-preview');
                const tip = document.getElementById('viewport-empty-tip');
                if (canvas && tip) {
                    tip.style.display = 'none';
                    canvas.style.display = 'block';
                    canvas.width = img.width;
                    canvas.height = img.height;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
                }
            };
            img.src = URL.createObjectURL(blob);
        } catch (e) {
            const reader = new FileReader();
            reader.onload = (ev) => {
                const img = new Image();
                img.onload = () => {
                    const canvas = document.getElementById('canvas-preview');
                    const tip = document.getElementById('viewport-empty-tip');
                    if (canvas && tip) {
                        tip.style.display = 'none';
                        canvas.style.display = 'block';
                        canvas.width = img.width;
                        canvas.height = img.height;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(img, 0, 0);
                    }
                };
                img.src = ev.target.result;
            };
            reader.readAsDataURL(currentFile);
        }
    },

    renderMultiMergePreview: function(files) {
        const canvas = document.getElementById('canvas-preview');
        const tip = document.getElementById('viewport-empty-tip');
        if (!canvas) return;

        canvas.width = 1654;
        canvas.height = 2338;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        const count = files.length;
        let cols = 1, rows = 2;
        if (count in [3, 4]) { cols = 2; rows = 2; }
        else if (count in [5, 6]) { cols = 2; rows = 3; }
        else if (count > 6) { cols = 2; rows = 4; }

        const cellW = canvas.width / cols;
        const cellH = canvas.height / rows;
        const margin = 40;

        let loaded = 0;
        files.slice(0, cols * rows).forEach((f, idx) => {
            const img = new Image();
            img.onload = () => {
                const r = Math.min((cellW - margin * 2) / img.width, (cellH - margin * 2) / img.height);
                const targetW = img.width * r;
                const targetH = img.height * r;
                const col = idx % cols;
                const row = Math.floor(idx / cols);
                const posX = col * cellW + (cellW - targetW) / 2;
                const posY = row * cellH + (cellH - targetH) / 2;
                ctx.drawImage(img, posX, posY, targetW, targetH);
                loaded++;
                if (loaded === Math.min(files.length, cols * rows)) {
                    if (tip) tip.style.display = 'none';
                    canvas.style.display = 'block';
                }
            };
            img.src = URL.createObjectURL(f);
        });
    },

    changePage: function(delta) {
        if (this.totalPages <= 1) return;
        this.currentPage = Math.max(0, Math.min(this.totalPages - 1, this.currentPage + delta));
        this.renderPreview();
    },

    adjustZoom: function(step) {},
    resetZoom: function() {}
};

// 状态看板与设备通信服务
const DashboardService = {
    loadPrinters: async function(isManual) {
        try {
            const res = await fetch('/api/printers').then(r => r.json());
            const select = document.getElementById('printer-select');
            if (select && res.printers) {
                select.innerHTML = res.printers.map(p => `<option value="${p.name}" ${p.is_default?'selected':''}>${p.name} (${p.state||'空闲'})</option>`).join('');
                if (res.printers.length > 0) {
                    AppConfig.activePrinter = select.value;
                }
            }
            if (res.printers && res.printers.length > 0) {
                document.getElementById('dash-status-pill').innerText = res.printers[0].state || '就绪';
            }
        } catch (e) {
            console.warn('获取打印机列表失败:', e);
        }
    },

    onPrinterChange: function() {
        AppConfig.activePrinter = document.getElementById('printer-select').value;
    },

    loadDiscoveredDevices: async function() {
        const sel = document.getElementById('discovered-device-select');
        sel.innerHTML = '<option value="">正在全总线扫描物理打印机...</option>';
        try {
            const res = await fetch('/api/devices/discover').then(r => r.json());
            if (res.devices && res.devices.length > 0) {
                sel.innerHTML = res.devices.map(d => `<option value="${d.uri}">${d.info} (${d.device_class})</option>`).join('');
            } else {
                sel.innerHTML = '<option value="">未探测到 USB/网络直连物理打印机</option>';
            }
        } catch (e) {
            sel.innerHTML = '<option value="">端口扫描超时</option>';
        }
    },

    searchDrivers: async function() {
        const kw = document.getElementById('driver-search-kw').value.trim();
        const box = document.getElementById('driver-search-results');
        if (!kw) return;
        box.style.display = 'block';
        box.innerHTML = '<div style="padding:6px; color:#64748b;">正在检索 CUPS 631 驱动库...</div>';
        try {
            const res = await fetch(`/api/drivers/search?q=${encodeURIComponent(kw)}`).then(r => r.json());
            if (res.drivers && res.drivers.length > 0) {
                box.innerHTML = res.drivers.map(drv => `<div style="padding:6px; border-bottom:1px solid #f1f5f9; cursor:pointer;" onclick="DashboardService.selectDriver('${drv.id}')">🖨️ ${drv.name}</div>`).join('');
            } else {
                box.innerHTML = '<div style="padding:6px; color:#ef4444;">未检索到匹配驱动，可上传 PPD 或安装插件</div>';
            }
        } catch (e) {
            box.innerHTML = '<div style="padding:6px; color:#ef4444;">检索失败</div>';
        }
    },

    uploadPpd: async function() {},
    uploadPlugin: async function() {},
    resetUsb: async function() {
        if (!confirm('确认发送 USB 总线硬复位信号？')) return;
        await fetch('/api/system/reset_usb', { method: 'POST' });
        alert('USB 总线复位指令已下发！');
    },
    cleanJobs: async function() {
        await fetch('/api/jobs/clear', { method: 'POST' });
        alert('队列已清空！');
        this.loadPrinters(false);
    },
    cleanDisk: async function() {
        await fetch('/api/system/clean_tmp', { method: 'POST' });
        alert('临时闪存与图片缓存已自洁！');
    }
};

// 硬件扫描模块
const ScanService = {
    startScan: async function() {},
    printCurrentFile: async function() {}
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    DashboardService.loadPrinters(false);
    if (window.DocAI && window.DocAI.init) {
        window.DocAI.init();
    }
});
