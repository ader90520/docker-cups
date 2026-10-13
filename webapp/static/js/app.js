/**
 * CUPS 智能工作台前端核心驱动
 * 闭环参数与预览联动、端侧 AI 兼容加载与运维控制
 */

function logToConsole(msg, level = "info") {
    const consoleBox = document.getElementById("ai-diag-console");
    if (!consoleBox) return;
    const time = new Date().toTimeString().split(' ')[0];
    let color = "#38bdf8";
    if (level === "error") color = "#f87171";
    if (level === "warn") color = "#fbbf24";
    if (level === "success") color = "#4ade80";

    const row = document.createElement("div");
    row.style.marginBottom = "3px";
    row.innerHTML = `<span style="color:#64748b;">[${time}]</span> <span style="color:${color};">${msg}</span>`;
    consoleBox.appendChild(row);
    consoleBox.scrollTop = consoleBox.scrollHeight;
}

function switchNavTab(tabName) {
    document.querySelectorAll(".tab-panel-container").forEach(el => el.style.display = "none");
    document.querySelectorAll(".top-navbar .nav-btn").forEach(btn => btn.classList.remove("active"));

    const targetPanel = document.getElementById(`panel-${tabName}`);
    if (targetPanel) targetPanel.style.display = "block";

    const navBtns = document.querySelectorAll(".top-navbar .nav-btn");
    const map = { "print": 0, "scan": 1, "devices": 2, "mail": 3 };
    if (navBtns[map[tabName]]) navBtns[map[tabName]].classList.add("active");

    if (tabName === "devices") {
        DashboardService.loadPrinters(false);
        DocAI.fetchDeployedModels();
    }
}

// ==================== 1. 响应式实时渲染引擎 ====================
window.EnhanceEngine = {
    canvas: null,
    ctx: null,
    currentRawImage: null,
    pdfDoc: null,
    currentPage: 1,
    totalPages: 0,
    zoomScale: 1.0,
    isLocked: true,

    init: function() {
        this.canvas = document.getElementById("canvas-preview");
        if (this.canvas) {
            this.ctx = this.canvas.getContext("2d");
        }
    },

    toggleLock: function() {
        this.isLocked = !this.isLocked;
        const badge = document.getElementById("preview-lock-badge");
        const lockText = document.getElementById("lock-text");
        if (badge) {
            badge.className = `preview-lock-badge ${this.isLocked ? "locked" : "unlocked"}`;
        }
        if (lockText) {
            lockText.innerText = this.isLocked ? "点击激活操作" : "缩放已解锁";
        }
    },

    onViewportClick: function() {
        if (this.isLocked) {
            this.toggleLock();
        }
    },

    adjustZoom: function(delta) {
        this.zoomScale = Math.max(0.5, Math.min(2.5, this.zoomScale + delta));
        this.render();
    },

    resetZoom: function() {
        this.zoomScale = 1.0;
        this.render();
    },

    changePage: function(delta) {
        if (!this.pdfDoc) return;
        const target = this.currentPage + delta;
        if (target >= 1 && target <= this.totalPages) {
            this.currentPage = target;
            this.renderPdfPage();
        }
    },

    render: function() {
        if (!this.canvas || !this.ctx) return;
        if (!this.currentRawImage && !this.pdfDoc) return;

        const tip = document.getElementById("viewport-empty-tip");
        if (tip) tip.style.display = "none";

        const media = (document.getElementById("p-media")?.value || "A4").toUpperCase();
        const orientation = PrintManager.orientation;
        const colorMode = PrintManager.colorMode;
        const enhance = (document.getElementById("p-enhance")?.value === "true");

        // 调整外层 A4 视窗形态
        const viewport = document.getElementById("a4-viewport");
        if (viewport) {
            viewport.className = `sheet-a4-viewport media-${media.toLowerCase()} ${orientation === 'landscape' ? 'landscape' : ''}`;
        }

        if (this.pdfDoc) {
            this.renderPdfPage();
            return;
        }

        const img = this.currentRawImage;
        const canvas = this.canvas;
        const ctx = this.ctx;

        let targetW = 1240;
        let targetH = 1754;
        if (media === "A5") { targetW = 874; targetH = 1240; }
        if (media === "B5") { targetW = 1037; targetH = 1476; }

        if (orientation === "landscape") {
            const tmp = targetW; targetW = targetH; targetH = tmp;
        }

        canvas.width = targetW;
        canvas.height = targetH;

        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, targetW, targetH);

        const scale = Math.min((targetW * 0.95) / img.width, (targetH * 0.95) / img.height) * this.zoomScale;
        const drawW = img.width * scale;
        const drawH = img.height * scale;
        const posX = (targetW - drawW) / 2;
        const posY = (targetH - drawH) / 2;

        const offCanvas = document.createElement("canvas");
        offCanvas.width = drawW;
        offCanvas.height = drawH;
        const offCtx = offCanvas.getContext("2d");
        offCtx.drawImage(img, 0, 0, drawW, drawH);

        const imgData = offCtx.getImageData(0, 0, drawW, drawH);
        const data = imgData.data;

        // 像素级滤镜流水线：白底除法增强与色彩折算
        for (let i = 0; i < data.length; i += 4) {
            let r = data[i];
            let g = data[i + 1];
            let b = data[i + 2];

            if (enhance) {
                const maxVal = Math.max(r, g, b);
                const bgFactor = maxVal > 110 ? 255 / (maxVal + 15) : 1.0;
                r = Math.min(255, r * bgFactor);
                g = Math.min(255, g * bgFactor);
                b = Math.min(255, b * bgFactor);
            }

            if (colorMode === "monochrome") {
                const gray = 0.299 * r + 0.587 * g + 0.114 * b;
                const ink = gray < 185 ? Math.max(0, gray * 0.7 - 10) : 255;
                data[i] = ink;
                data[i + 1] = ink;
                data[i + 2] = ink;
            } else {
                data[i] = r;
                data[i + 1] = g;
                data[i + 2] = b;
            }
        }

        offCtx.putImageData(imgData, 0, 0);
        ctx.drawImage(offCanvas, posX, posY);
    },

    renderPdfPage: function() {
        if (!this.pdfDoc) return;
        this.pdfDoc.getPage(this.currentPage).then(page => {
            const viewportDef = page.getViewport({ scale: 1.5 });
            const canvas = this.canvas;
            const ctx = this.ctx;

            canvas.width = viewportDef.width;
            canvas.height = viewportDef.height;

            const renderContext = { canvasContext: ctx, viewport: viewportDef };
            page.render(renderContext).promise.then(() => {
                if (PrintManager.colorMode === "monochrome") {
                    const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                    const data = imgData.data;
                    for (let i = 0; i < data.length; i += 4) {
                        const gray = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
                        data[i] = gray;
                        data[i + 1] = gray;
                        data[i + 2] = gray;
                    }
                    ctx.putImageData(imgData, 0, 0);
                }
            });

            const counter = document.getElementById("page-counter-lbl");
            if (counter) counter.innerText = `${this.currentPage} / ${this.totalPages}`;
        });
    }
};

// ==================== 2. 打印交互与参数管理 ====================
window.PrintManager = {
    colorMode: "monochrome",
    orientation: "portrait",
    selectedFiles: [],
    currentSubTab: "normal",

    init: function() {
        const dropZone = document.getElementById("drag-drop-zone-normal");
        if (dropZone) {
            dropZone.addEventListener("click", () => document.getElementById("file-input-normal").click());
            dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
            dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
            dropZone.addEventListener("drop", (e) => {
                e.preventDefault();
                dropZone.classList.remove("drag-over");
                this.onFilesSelected(e.dataTransfer.files);
            });
        }
    },

    switchSubTab: function(tab) {
        this.currentSubTab = tab;
        document.getElementById("tab-sub-normal")?.classList.toggle("active", tab === "normal");
        document.getElementById("tab-sub-idcard")?.classList.toggle("active", tab === "idcard");
        document.getElementById("sec-upload-normal").style.display = (tab === "normal" ? "block" : "none");
        document.getElementById("sec-upload-idcard").style.display = (tab === "idcard" ? "block" : "none");
        if (tab === "idcard") {
            IdCardManager.updateCanvasPreview();
        } else {
            EnhanceEngine.render();
        }
    },

    setColorMode: function(mode) {
        this.colorMode = mode;
        document.getElementById("btn-color-mono")?.classList.toggle("active", mode === "monochrome");
        document.getElementById("btn-color-rgb")?.classList.toggle("active", mode === "color");
        logToConsole(`颜色模式切换为: ${mode === "monochrome" ? "黑白" : "彩色"}`);
        EnhanceEngine.render();
    },

    setOrientation: function(orient) {
        this.orientation = orient;
        document.getElementById("btn-orient-port")?.classList.toggle("active", orient === "portrait");
        document.getElementById("btn-orient-land")?.classList.toggle("active", orient === "landscape");
        logToConsole(`打印排版方向切换为: ${orient === "portrait" ? "纵向" : "横向"}`);
        EnhanceEngine.render();
    },

    onPaperSizeChange: function() {
        const size = document.getElementById("p-media")?.value;
        logToConsole(`纸张介质切换为: ${size}`);
        EnhanceEngine.render();
    },

    onEnhanceChange: function() {
        const state = document.getElementById("p-enhance")?.value;
        logToConsole(`去黑底全能王增强: ${state === "true" ? "开启" : "关闭"}`);
        EnhanceEngine.render();
    },

    onMergeChange: function() {
        const merge = document.getElementById("chk-normal-merge")?.checked;
        logToConsole(`多图同页拼版模式: ${merge ? "开启" : "关闭"}`);
    },

    onFilesSelected: function(files) {
        if (!files || files.length === 0) return;
        this.selectedFiles = Array.from(files);
        this.updateFileListUI();

        const firstFile = files[0];
        const ext = firstFile.name.split('.').pop().toLowerCase();

        logToConsole(`已加载文件: ${firstFile.name} (${(firstFile.size/1024).toFixed(1)} KB)`);

        if (ext === "pdf") {
            const reader = new FileReader();
            reader.onload = function(e) {
                const typedarray = new Uint8Array(e.target.result);
                pdfjsLib.getDocument(typedarray).promise.then(pdf => {
                    EnhanceEngine.pdfDoc = pdf;
                    EnhanceEngine.currentPage = 1;
                    EnhanceEngine.totalPages = pdf.numPages;
                    EnhanceEngine.currentRawImage = null;
                    EnhanceEngine.renderPdfPage();
                });
            };
            reader.readAsArrayBuffer(firstFile);
        } else {
            EnhanceEngine.pdfDoc = null;
            const reader = new FileReader();
            reader.onload = function(e) {
                const img = new Image();
                img.onload = function() {
                    EnhanceEngine.currentRawImage = img;
                    EnhanceEngine.render();
                };
                img.src = e.target.result;
            };
            reader.readAsDataURL(firstFile);
        }
    },

    updateFileListUI: function() {
        const box = document.getElementById("file-list-normal");
        if (!box) return;
        if (this.selectedFiles.length === 0) {
            box.style.display = "none";
            box.innerHTML = "";
            return;
        }
        box.style.display = "flex";
        box.innerHTML = this.selectedFiles.map((f, i) => `
            <div style="display:flex; justify-content:space-between; align-items:center; padding:5px 8px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:4px; font-size:11px;">
                <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1;">📄 ${f.name}</div>
                <button type="button" style="background:none; border:none; color:#ef4444; font-weight:700; cursor:pointer;" onclick="PrintManager.removeFile(${i})">×</button>
            </div>
        `).join('');
    },

    removeFile: function(idx) {
        this.selectedFiles.splice(idx, 1);
        this.updateFileListUI();
        if (this.selectedFiles.length > 0) {
            this.onFilesSelected(this.selectedFiles);
        } else {
            EnhanceEngine.currentRawImage = null;
            EnhanceEngine.pdfDoc = null;
            const ctx = EnhanceEngine.ctx;
            if (ctx) ctx.clearRect(0, 0, EnhanceEngine.canvas.width, EnhanceEngine.canvas.height);
            document.getElementById("viewport-empty-tip").style.display = "block";
        }
    },

    clearFiles: function() {
        this.selectedFiles = [];
        this.updateFileListUI();
        EnhanceEngine.currentRawImage = null;
        EnhanceEngine.pdfDoc = null;
        const ctx = EnhanceEngine.ctx;
        if (ctx) ctx.clearRect(0, 0, EnhanceEngine.canvas.width, EnhanceEngine.canvas.height);
        document.getElementById("viewport-empty-tip").style.display = "block";
    },

    submitJob: async function() {
        const printer = document.getElementById("printer-select")?.value;
        const alertBox = document.getElementById("print-alert");
        const btn = document.getElementById("btn-submit");

        if (!printer) {
            alertBox.innerHTML = '<span style="color:#ef4444;">未选择可用打印机</span>';
            return;
        }

        const fd = new FormData();
        fd.append("printer", printer);
        fd.append("copies", document.getElementById("p-copies")?.value || "1");
        fd.append("duplex", document.getElementById("p-duplex")?.value || "none");
        fd.append("color_mode", this.colorMode);
        fd.append("media", document.getElementById("p-media")?.value || "A4");
        fd.append("enhance", document.getElementById("p-enhance")?.value || "true");
        fd.append("page_ranges", document.getElementById("p-page-ranges")?.value || "");
        fd.append("multi_merge", document.getElementById("chk-normal-merge")?.checked ? "true" : "false");

        if (this.currentSubTab === "normal") {
            if (this.selectedFiles.length === 0) {
                alertBox.innerHTML = '<span style="color:#ef4444;">请选取待打印文档或图片</span>';
                return;
            }
            this.selectedFiles.forEach(f => fd.append("file", f));
        } else {
            const blob = await IdCardManager.getMergedBlob();
            if (!blob) {
                alertBox.innerHTML = '<span style="color:#ef4444;">身份证正反面尚未准备齐全</span>';
                return;
            }
            fd.append("file", blob, "idcard_merged.jpg");
        }

        btn.disabled = true;
        btn.innerText = "⏳ 正在传输打印作业...";
        alertBox.innerHTML = "";
        logToConsole("正在下发打印作业到 CUPS 管道...");

        try {
            const resp = await fetch("/api/print", { method: "POST", body: fd });
            const res = await resp.json();
            if (res.success) {
                alertBox.innerHTML = `<span style="color:#10b981;">✔ ${res.msg}</span>`;
                logToConsole(`✔ 作业派发成功: ${res.job || res.msg}`, "success");
            } else {
                alertBox.innerHTML = `<span style="color:#ef4444;">❌ 失败: ${res.msg}</span>`;
                logToConsole(`❌ CUPS 拒绝: ${res.msg}`, "error");
            }
        } catch (e) {
            alertBox.innerHTML = `<span style="color:#ef4444;">网络异常: ${e.message}</span>`;
            logToConsole(`通讯异常: ${e.message}`, "error");
        } finally {
            btn.disabled = false;
            btn.innerText = "🖨️ 提交打印";
        }
    }
};

// ==================== 3. 身份证双面排版引擎 ====================
window.IdCardManager = {
    persons: [{ front: null, back: null }],

    addPerson: function() {
        if (this.persons.length >= 3) {
            alert("单张 A4 纸最多排版 3 人身份证！");
            return;
        }
        const idx = this.persons.length;
        this.persons.push({ front: null, back: null });

        const container = document.getElementById("idcard-person-container");
        const card = document.createElement("div");
        card.style.background = "#f8fafc";
        card.style.border = "1px solid #e2e8f0";
        card.style.borderRadius = "6px";
        card.style.padding = "10px";
        card.style.marginBottom = "8px";
        card.id = `idcard-person-${idx}`;
        card.innerHTML = `
            <div style="font-size:12px; font-weight:700; color:#334155; margin-bottom:8px;">第 ${idx + 1} 人身份证信息</div>
            <div class="idcard-ratio-grid">
                <div class="idcard-ratio-box" id="idcard-slot-front-${idx}">
                    <div class="idcard-ratio-placeholder" id="placeholder-front-${idx}">
                        <div class="icon">👤</div>
                        <div class="title">正面 (人像面)</div>
                        <div class="sub">点击上传</div>
                    </div>
                    <img id="preview-front-${idx}" class="idcard-ratio-img" style="display:none;" alt="正面">
                    <input type="file" id="file-idcard-front-${idx}" accept="image/*" style="display:none;" onchange="IdCardManager.onSideUploaded(${idx}, 'front', this.files)">
                </div>
                <div class="idcard-ratio-box" id="idcard-slot-back-${idx}">
                    <div class="idcard-ratio-placeholder" id="placeholder-back-${idx}">
                        <div class="icon">🇨🇳</div>
                        <div class="title">反面 (国徽面)</div>
                        <div class="sub">点击上传</div>
                    </div>
                    <img id="preview-back-${idx}" class="idcard-ratio-img" style="display:none;" alt="反面">
                    <input type="file" id="file-idcard-back-${idx}" accept="image/*" style="display:none;" onchange="IdCardManager.onSideUploaded(${idx}, 'back', this.files)">
                </div>
            </div>
        `;
        container.appendChild(card);
        this.bindBoxEvents(idx);
    },

    bindBoxEvents: function(idx) {
        ['front', 'back'].forEach(side => {
            const box = document.getElementById(`idcard-slot-${side}-${idx}`);
            const input = document.getElementById(`file-idcard-${side}-${idx}`);
            if (box && input) {
                box.addEventListener('click', () => input.click());
            }
        });
    },

    onSideUploaded: function(idx, side, files) {
        if (!files || files.length === 0) return;
        const file = files[0];
        const reader = new FileReader();
        reader.onload = (e) => {
            const img = new Image();
            img.onload = () => {
                this.persons[idx][side] = img;
                document.getElementById(`placeholder-${side}-${idx}`).style.display = "none";
                const pImg = document.getElementById(`preview-${side}-${idx}`);
                pImg.src = e.target.result;
                pImg.style.display = "block";
                logToConsole(`第 ${idx + 1} 人 [${side === 'front' ? '正面' : '反面'}] 照片就绪`);
                this.updateCanvasPreview();
            };
            img.src = e.target.result;
        };
        reader.readAsDataURL(file);
    },

    updateCanvasPreview: function() {
        const canvas = EnhanceEngine.canvas;
        const ctx = EnhanceEngine.ctx;
        if (!canvas || !ctx) return;

        document.getElementById("viewport-empty-tip").style.display = "none";

        canvas.width = 1654;
        canvas.height = 2338;
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        const cardW = 674;
        const cardH = 425;

        this.persons.forEach((p, i) => {
            const baseY = 250 + i * 650;
            if (p.front) {
                const posX = (canvas.width - cardW) / 2;
                ctx.drawImage(p.front, posX, baseY, cardW, cardH);
            }
            if (p.back) {
                const posX = (canvas.width - cardW) / 2;
                ctx.drawImage(p.back, posX, baseY + cardH + 40, cardW, cardH);
            }
        });
    },

    getMergedBlob: function() {
        return new Promise(resolve => {
            EnhanceEngine.canvas.toBlob(blob => resolve(blob), "image/jpeg", 0.92);
        });
    },

    clearAll: function() {
        this.persons = [{ front: null, back: null }];
        document.getElementById("idcard-person-container").innerHTML = "";
        ['front', 'back'].forEach(s => {
            document.getElementById(`placeholder-${s}-0`).style.display = "block";
            document.getElementById(`preview-${s}-0`).style.display = "none";
        });
        const ctx = EnhanceEngine.ctx;
        if (ctx) ctx.clearRect(0, 0, EnhanceEngine.canvas.width, EnhanceEngine.canvas.height);
        document.getElementById("viewport-empty-tip").style.display = "block";
    }
};

// ==================== 4. 设备管理服务 ====================
window.DashboardService = {
    loadPrinters: async function(showToast = false) {
        try {
            const resp = await fetch("/api/devices");
            const res = await resp.json();
            const select = document.getElementById("printer-select");
            const statusPill = document.getElementById("dash-status-pill");
            const queueCount = document.getElementById("dash-queue-count");

            if (!select) return;
            select.innerHTML = "";
            const printers = res.printers || [];

            if (printers.length === 0) {
                select.innerHTML = '<option value="">无可用打印机</option>';
                if (statusPill) statusPill.innerText = "离线";
                return;
            }

            printers.forEach(p => {
                const opt = document.createElement("option");
                opt.value = p.name;
                opt.text = `${p.name} (${p.status || '就绪'})`;
                select.appendChild(opt);
            });

            if (statusPill) statusPill.innerText = printers[0].status || "就绪";
            if (queueCount) queueCount.innerText = printers[0].jobs_count || "0";
            if (showToast) logToConsole("打印机列表与状态刷新完成", "success");
        } catch (e) {
            logToConsole(`打印机列表获取失败: ${e.message}`, "error");
        }
    },

    onPrinterChange: function() {
        const p = document.getElementById("printer-select")?.value;
        logToConsole(`当前目标打印机切换为: ${p}`);
    }
};

document.addEventListener("DOMContentLoaded", () => {
    EnhanceEngine.init();
    PrintManager.init();
    IdCardManager.bindBoxEvents(0);
    DashboardService.loadPrinters(false);
});
