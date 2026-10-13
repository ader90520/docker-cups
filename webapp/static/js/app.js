/**
 * CUPS 智能工作台前端核心驱动
 * 闭环参数联动、状态迁移、身份证增减人自适应与不重叠网格排版
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
    } else if (tabName === "scan") {
        ScanService.loadScanDevices();
        ScanService.loadScanList();
    } else if (tabName === "mail") {
        MailService.loadConfig();
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
    rotationDeg: 0,

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
        this.rotationDeg = 0;
        this.render();
    },

    rotate: function(angleDelta) {
        this.rotationDeg = (this.rotationDeg + angleDelta + 360) % 360;
        logToConsole(`画幅旋转: ${this.rotationDeg}°`);
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
        if (PrintManager.currentSubTab === "idcard") {
            IdCardManager.updateCanvasPreview();
            return;
        }

        if (PrintManager.isMultiMergeEnabled() && PrintManager.loadedImages.length > 1) {
            this.renderMultiImagesGrid();
            return;
        }

        if (!this.currentRawImage && !this.pdfDoc) return;

        const tip = document.getElementById("viewport-empty-tip");
        if (tip) tip.style.display = "none";

        const media = (document.getElementById("p-media")?.value || "A4").toUpperCase();
        const orientation = PrintManager.orientation;
        const colorMode = PrintManager.colorMode;
        const enhance = (document.getElementById("p-enhance")?.value === "true");

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

        const rad = (this.rotationDeg * Math.PI) / 180;
        const isSwapped = (this.rotationDeg === 90 || this.rotationDeg === 270);
        const bboxW = isSwapped ? img.height : img.width;
        const bboxH = isSwapped ? img.width : img.height;

        const scale = Math.min((targetW * 0.95) / bboxW, (targetH * 0.95) / bboxH) * this.zoomScale;
        const drawW = img.width * scale;
        const drawH = img.height * scale;

        ctx.save();
        ctx.translate(targetW / 2, targetH / 2);
        ctx.rotate(rad);

        const offCanvas = document.createElement("canvas");
        offCanvas.width = drawW;
        offCanvas.height = drawH;
        const offCtx = offCanvas.getContext("2d");
        offCtx.drawImage(img, 0, 0, drawW, drawH);

        const imgData = offCtx.getImageData(0, 0, drawW, drawH);
        const data = imgData.data;

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
        ctx.drawImage(offCanvas, -drawW / 2, -drawH / 2);
        ctx.restore();
    },

    // 多图拼版网格绝对隔离排版 (杜绝图块重叠)
    renderMultiImagesGrid: function() {
        const imgs = PrintManager.loadedImages;
        const canvas = this.canvas;
        const ctx = this.ctx;
        const count = imgs.length;

        const targetW = 1240;
        const targetH = 1754;
        canvas.width = targetW;
        canvas.height = targetH;

        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, targetW, targetH);

        let cols = 1, rows = 2;
        if (count >= 3 && count <= 4) { cols = 2; rows = 2; }
        else if (count >= 5 && count <= 6) { cols = 2; rows = 3; }
        else if (count > 6) { cols = 2; rows = 4; }

        const cellW = targetW / cols;
        const cellH = targetH / rows;
        const padding = 20;

        imgs.slice(0, cols * rows).forEach((item, idx) => {
            const col = idx % cols;
            const row = Math.floor(idx / cols);
            const img = item.img;

            const maxBoxW = cellW - padding * 2;
            const maxBoxH = cellH - padding * 2;
            const ratio = Math.min(maxBoxW / img.width, maxBoxH / img.height);
            const drawW = img.width * ratio;
            const drawH = img.height * ratio;

            const posX = col * cellW + (cellW - drawW) / 2;
            const posY = row * cellH + (cellH - drawH) / 2;

            ctx.drawImage(img, posX, posY, drawW, drawH);
            ctx.strokeStyle = "#cbd5e1";
            ctx.lineWidth = 1;
            ctx.strokeRect(posX, posY, drawW, drawH);
        });
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

// ==================== 2. 打印交互与追加管理 ====================
window.PrintManager = {
    colorMode: "monochrome",
    orientation: "portrait",
    selectedFiles: [],
    loadedImages: [],
    currentActiveIndex: 0,
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

    isMultiMergeEnabled: function() {
        return document.getElementById("chk-normal-merge")?.checked || false;
    },

    switchSubTab: function(tab) {
        this.currentSubTab = tab;
        document.getElementById("tab-sub-normal")?.classList.toggle("active", tab === "normal");
        document.getElementById("tab-sub-idcard")?.classList.toggle("active", tab === "idcard");
        document.getElementById("sec-upload-normal").style.display = (tab === "normal" ? "block" : "none");
        document.getElementById("sec-upload-idcard").style.display = (tab === "idcard" ? "block" : "none");

        // 动态隐藏/展示去黑底增强与页面范围表单项
        const enhanceBox = document.getElementById("item-enhance-box");
        const pageRangesBox = document.getElementById("item-page-ranges-box");
        if (tab === "idcard") {
            if (enhanceBox) enhanceBox.style.display = "none";
            if (pageRangesBox) pageRangesBox.style.display = "none";
            IdCardManager.updateCanvasPreview();
        } else {
            if (enhanceBox) enhanceBox.style.display = "flex";
            if (pageRangesBox) pageRangesBox.style.display = "flex";
            EnhanceEngine.render();
        }
    },

    setColorMode: function(mode) {
        this.colorMode = mode;
        document.getElementById("btn-color-mono")?.classList.toggle("active", mode === "monochrome");
        document.getElementById("btn-color-rgb")?.classList.toggle("active", mode === "color");
        logToConsole(`颜色模式切换: ${mode === "monochrome" ? "黑白" : "彩色"}`);
        EnhanceEngine.render();
    },

    setOrientation: function(orient) {
        this.orientation = orient;
        document.getElementById("btn-orient-port")?.classList.toggle("active", orient === "portrait");
        document.getElementById("btn-orient-land")?.classList.toggle("active", orient === "landscape");
        logToConsole(`排版方向切换: ${orient === "portrait" ? "纵向" : "横向"}`);
        EnhanceEngine.render();
    },

    onPaperSizeChange: function() {
        EnhanceEngine.render();
    },

    onEnhanceChange: function() {
        EnhanceEngine.render();
    },

    onMergeChange: function() {
        logToConsole(`多图同页拼版: ${this.isMultiMergeEnabled() ? "开启" : "关闭"}`);
        EnhanceEngine.render();
    },

    onFilesSelected: function(files) {
        if (!files || files.length === 0) return;
        const incoming = Array.from(files);

        incoming.forEach(newF => {
            if (!this.selectedFiles.some(f => f.name === newF.name && f.size === newF.size)) {
                this.selectedFiles.push(newF);
            }
        });

        this.updateFileListUI();
        this.loadSelectedFile(this.selectedFiles.length - 1);
    },

    loadSelectedFile: function(idx) {
        if (idx < 0 || idx >= this.selectedFiles.length) return;
        this.currentActiveIndex = idx;
        const file = this.selectedFiles[idx];
        const ext = file.name.split('.').pop().toLowerCase();

        this.updateFileListUI();
        logToConsole(`装载预览: ${file.name}`);

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
            reader.readAsArrayBuffer(file);
        } else {
            EnhanceEngine.pdfDoc = null;
            const reader = new FileReader();
            reader.onload = (e) => {
                const img = new Image();
                img.onload = () => {
                    EnhanceEngine.currentRawImage = img;
                    if (!this.loadedImages.some(item => item.name === file.name)) {
                        this.loadedImages.push({ name: file.name, img: img });
                    }
                    EnhanceEngine.render();
                };
                img.src = e.target.result;
            };
            reader.readAsDataURL(file);
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
            <div style="display:flex; justify-content:space-between; align-items:center; padding:5px 8px; background:${i === this.currentActiveIndex ? '#eff6ff' : '#f8fafc'}; border:1px solid ${i === this.currentActiveIndex ? '#3b82f6' : '#e2e8f0'}; border-radius:4px; font-size:11px; cursor:pointer;" onclick="PrintManager.loadSelectedFile(${i})">
                <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; font-weight:${i === this.currentActiveIndex ? '700' : '400'}; color:${i === this.currentActiveIndex ? '#1d4ed8' : '#334155'};">
                    ${i === this.currentActiveIndex ? '👉 ' : ''}📄 [${i + 1}] ${f.name}
                </div>
                <button type="button" style="background:none; border:none; color:#ef4444; font-weight:700; cursor:pointer; padding:0 4px;" onclick="event.stopPropagation(); PrintManager.removeFile(${i})">×</button>
            </div>
        `).join('');
    },

    removeFile: function(idx) {
        const removed = this.selectedFiles.splice(idx, 1)[0];
        this.loadedImages = this.loadedImages.filter(item => item.name !== removed.name);
        if (this.selectedFiles.length > 0) {
            const nextIdx = Math.min(this.selectedFiles.length - 1, idx);
            this.loadSelectedFile(nextIdx);
        } else {
            this.clearFiles();
        }
    },

    clearFiles: function() {
        this.selectedFiles = [];
        this.loadedImages = [];
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
        fd.append("multi_merge", this.isMultiMergeEnabled() ? "true" : "false");
        fd.append("rotation", EnhanceEngine.rotationDeg);

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

// ==================== 3. 身份证标准规范排布排版引擎 ====================
// 单人身份证：正反面严格上下摆放；多人身份证：每人正反面左右并排摆放
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
        this.updateCanvasPreview();
    },

    removePerson: function() {
        if (this.persons.length <= 1) {
            alert("至少保留 1 人身份证信息！");
            return;
        }
        const lastIdx = this.persons.length - 1;
        this.persons.pop();
        const targetEl = document.getElementById(`idcard-person-${lastIdx}`);
        if (targetEl) targetEl.remove();
        logToConsole(`已减少至 ${this.persons.length} 人身份证`);
        this.updateCanvasPreview();
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

        // A4 200 DPI 标准像素画幅: 1654 x 2338
        canvas.width = 1654;
        canvas.height = 2338;
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        // 身份证 85.6mm x 54mm 对应像素尺寸: 674 x 425
        const cardW = 674;
        const cardH = 425;
        const count = this.persons.length;

        if (count === 1) {
            // 单人身份证模式：严格纵向上下垂直居中摆放
            const p = this.persons[0];
            const gapY = 240;
            const startY = (canvas.height - (cardH * 2 + gapY)) / 2;
            const posX = (canvas.width - cardW) / 2;

            if (p.front) {
                ctx.drawImage(p.front, posX, startY, cardW, cardH);
                ctx.strokeStyle = "#cbd5e1";
                ctx.strokeRect(posX, startY, cardW, cardH);
            }
            if (p.back) {
                ctx.drawImage(p.back, posX, startY + cardH + gapY, cardW, cardH);
                ctx.strokeStyle = "#cbd5e1";
                ctx.strokeRect(posX, startY + cardH + gapY, cardW, cardH);
            }
        } else {
            // 多人身份证模式 (2~3人)：同一人正反面左右并排摆放，多人垂直向下平铺
            const gapX = 120;
            const gapY = 160;
            const totalRowW = cardW * 2 + gapX;
            const startX = (canvas.width - totalRowW) / 2;
            const totalH = count * cardH + (count - 1) * gapY;
            const startY = (canvas.height - totalH) / 2;

            this.persons.forEach((p, idx) => {
                const currentY = startY + idx * (cardH + gapY);
                if (p.front) {
                    ctx.drawImage(p.front, startX, currentY, cardW, cardH);
                    ctx.strokeStyle = "#cbd5e1";
                    ctx.strokeRect(startX, currentY, cardW, cardH);
                }
                if (p.back) {
                    ctx.drawImage(p.back, startX + cardW + gapX, currentY, cardW, cardH);
                    ctx.strokeStyle = "#cbd5e1";
                    ctx.strokeRect(startX + cardW + gapX, currentY, cardW, cardH);
                }
            });
        }
    },

    getMergedBlob: function() {
        return new Promise(resolve => {
            EnhanceEngine.canvas.toBlob(blob => resolve(blob), "image/jpeg", 0.95);
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

// ==================== 4. 扫描硬件交互服务 ====================
window.ScanService = {
    loadScanDevices: async function() {
        try {
            const resp = await fetch("/api/scan/devices");
            const res = await resp.json();
            const select = document.getElementById("scan-dev-select");
            select.innerHTML = "";
            const devs = res.data?.devices || [];
            if (devs.length === 0) {
                select.innerHTML = '<option value="">未发现扫描仪</option>';
                return;
            }
            devs.forEach(d => {
                const opt = document.createElement("option");
                opt.value = d.id;
                opt.text = d.name;
                select.appendChild(opt);
            });
        } catch (e) {
            logToConsole(`获取扫描仪失败: ${e.message}`, "error");
        }
    },

    loadScanList: async function() {
        try {
            const resp = await fetch("/api/scan/list");
            const res = await resp.json();
            const listContainer = document.getElementById("scan-file-list");
            const files = res.files || [];
            if (files.length === 0) {
                listContainer.innerHTML = '<div style="font-size:11px; color:#94a3b8;">暂无扫描件历史</div>';
                return;
            }
            listContainer.innerHTML = files.map(f => `
                <div style="background:#fff; border:1px solid #e2e8f0; border-radius:4px; padding:6px 8px; display:flex; justify-content:space-between; align-items:center; font-size:11px;">
                    <div><b>📄 ${f.filename}</b> <span style="color:#64748b;">(${f.size})</span></div>
                    <div style="display:flex; gap:4px;">
                        <button type="button" class="nav-btn" style="border:1px solid #cbd5e1; padding:2px 6px;" onclick="ScanService.viewScan('${f.url}')">查看</button>
                        <a href="${f.download_url}" class="nav-btn" style="border:1px solid #cbd5e1; padding:2px 6px; text-decoration:none;">下载</a>
                    </div>
                </div>
            `).join('');
        } catch (e) {
            logToConsole(`获取扫描存档失败: ${e.message}`, "error");
        }
    },

    viewScan: function(url) {
        const liveImg = document.getElementById("scan-live-img");
        document.getElementById("scan-empty-tip").style.display = "none";
        liveImg.src = url;
        liveImg.style.display = "block";
    },

    startScan: async function() {
        const device = document.getElementById("scan-dev-select")?.value;
        const resVal = document.getElementById("scan-res")?.value;
        const mode = document.getElementById("scan-mode")?.value;
        const alertBox = document.getElementById("scan-alert");
        const btn = document.getElementById("btn-start-scan");

        btn.disabled = true;
        btn.innerText = "⏳ 正在物理扫描，请勿移动纸张...";
        alertBox.innerHTML = "";
        logToConsole("下发硬件扫描指令...");

        const fd = new FormData();
        fd.append("device", device);
        fd.append("resolution", resVal);
        fd.append("mode", mode);

        try {
            const resp = await fetch("/api/scan", { method: "POST", body: fd });
            const res = await resp.json();
            if (res.success) {
                alertBox.innerHTML = `<span style="color:#10b981;">✔ ${res.msg}</span>`;
                this.viewScan(res.data.url);
                this.loadScanList();
                logToConsole("✔ 扫描作业成功完成", "success");
            } else {
                alertBox.innerHTML = `<span style="color:#ef4444;">❌ 扫描失败: ${res.msg}</span>`;
                logToConsole(`扫描错误: ${res.msg}`, "error");
            }
        } catch (e) {
            alertBox.innerHTML = `<span style="color:#ef4444;">通讯异常: ${e.message}</span>`;
        } finally {
            btn.disabled = false;
            btn.innerText = "📷 启动硬件扫描并呈现图像";
        }
    }
};

// ==================== 5. 设备管理与运维服务 ====================
window.DashboardService = {
    loadPrinters: async function(showToast = false) {
        try {
            const resp = await fetch("/api/devices");
            const res = await resp.json();
            const select = document.getElementById("printer-select");
            const mailSelect = document.getElementById("mail-default-printer");
            const statusTag = document.getElementById("printer-status-tag");
            const queueCount = document.getElementById("dash-queue-count");
            const cardList = document.getElementById("device-card-list");

            if (!select) return;
            select.innerHTML = "";
            if (mailSelect) mailSelect.innerHTML = "";
            const printers = res.printers || [];

            if (printers.length === 0) {
                select.innerHTML = '<option value="">无可用打印机</option>';
                if (mailSelect) mailSelect.innerHTML = '<option value="">无可用设备</option>';
                if (statusTag) {
                    statusTag.className = "printer-status-badge badge-offline";
                    statusTag.innerText = "打印机状态: 离线";
                }
                if (queueCount) queueCount.innerText = "0";
                if (cardList) cardList.innerHTML = '<div style="font-size:11px; color:#94a3b8;">暂未安装打印机</div>';
                return;
            }

            printers.forEach(p => {
                const opt = document.createElement("option");
                opt.value = p.name;
                opt.text = `${p.name} (${p.status || '就绪'})`;
                select.appendChild(opt);
                if (mailSelect) {
                    const optM = document.createElement("option");
                    optM.value = p.name;
                    optM.text = p.name;
                    mailSelect.appendChild(optM);
                }
            });

            const currentStatus = printers[0].status || "就绪";
            if (statusTag) {
                statusTag.className = `printer-status-badge ${currentStatus.includes("忙") ? 'badge-busy' : 'badge-idle'}`;
                statusTag.innerText = `打印机状态: ${currentStatus}`;
            }
            if (queueCount) queueCount.innerText = printers[0].jobs_count || "0";

            if (cardList) {
                cardList.innerHTML = printers.map(p => `
                    <div style="background:#fff; border:1px solid #e2e8f0; border-radius:6px; padding:8px 10px; display:flex; justify-content:space-between; align-items:center;">
                        <div><b>🖨️ ${p.name}</b> <span style="font-size:11px; color:#10b981;">● ${p.status || '就绪'}</span></div>
                        <button type="button" class="nav-btn" style="border:1px solid #cbd5e1; padding:2px 8px; font-size:11px;" onclick="DashboardService.testPage('${p.name}')">测试页</button>
                    </div>
                `).join('');
            }

            if (showToast) logToConsole("打印机列表刷新完成", "success");
        } catch (e) {
            logToConsole(`打印机列表获取失败: ${e.message}`, "error");
        }
    },

    onPrinterChange: function() {
        const p = document.getElementById("printer-select")?.value;
        logToConsole(`当前目标打印机切换为: ${p}`);
    },

    testPage: async function(pName) {
        logToConsole(`向打印机 [${pName}] 派发测试页...`);
        try {
            const resp = await fetch("/api/printer_admin", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: `action=test_page&printer=${encodeURIComponent(pName)}`
            });
            const res = await resp.json();
            logToConsole(res.msg, res.success ? "success" : "error");
        } catch (e) {
            logToConsole(`测试页下发异常: ${e.message}`, "error");
        }
    },

    loadDiscoveredDevices: async function() {
        logToConsole("正在扫描物理端口与设备 URI...");
        const select = document.getElementById("discovered-device-select");
        try {
            const resp = await fetch("/api/devices/discover");
            const res = await resp.json();
            select.innerHTML = '<option value="">-- 请选择硬件端口 --</option>';
            (res.devices || []).forEach(d => {
                const opt = document.createElement("option");
                opt.value = d.uri;
                opt.text = `${d.info} (${d.uri})`;
                select.appendChild(opt);
            });
            logToConsole(`发现 ${res.devices ? res.devices.length : 0} 个端口`, "success");
        } catch (e) {
            logToConsole(`扫描端口失败: ${e.message}`, "error");
        }
    },

    onDiscoveredDeviceSelect: function(sel) {
        const uri = sel.value;
        if (!uri) return;
        const nameInput = document.getElementById("add-printer-name");
        if (nameInput && !nameInput.value) {
            nameInput.value = uri.split("/").pop().split("?")[0].replace(/[^a-zA-Z0-9_-]/g, "_");
        }
    },

    searchDrivers: async function() {
        const kw = document.getElementById("driver-search-kw")?.value.trim();
        const box = document.getElementById("driver-search-results");
        if (!kw) return;
        logToConsole(`检索驱动: ${kw}...`);
        try {
            const resp = await fetch(`/api/drivers/search?q=${encodeURIComponent(kw)}`);
            const res = await resp.json();
            box.style.display = "block";
            box.innerHTML = (res.drivers || []).map(d => `
                <div style="padding:4px 6px; border-bottom:1px solid #f1f5f9; cursor:pointer;" onclick="DashboardService.pickDriver('${d.id}')">
                    <b>${d.id}</b> - <span style="color:#64748b;">${d.info}</span>
                </div>
            `).join('') || '<div style="padding:6px; color:#94a3b8;">未找到匹配驱动</div>';
        } catch (e) {
            logToConsole(`驱动搜索失败: ${e.message}`, "error");
        }
    },

    pickDriver: function(drvId) {
        document.getElementById("driver-input-val").value = drvId;
        document.getElementById("driver-search-results").style.display = "none";
        logToConsole(`已选定驱动: ${drvId}`);
    },

    submitAddPrinter: async function() {
        const name = document.getElementById("add-printer-name")?.value.trim();
        const uri = document.getElementById("discovered-device-select")?.value;
        const driver = document.getElementById("driver-input-val")?.value.trim();
        const alertBox = document.getElementById("add-printer-alert");

        if (!name || !uri) {
            alertBox.innerHTML = '<span style="color:#ef4444;">设备名称与连接端口不能为空</span>';
            return;
        }

        logToConsole(`正在创建打印机队列 [${name}]...`);
        const fd = new FormData();
        fd.append("action", "add_printer");
        fd.append("printer", name);
        fd.append("device_uri", uri);
        fd.append("driver", driver);

        try {
            const resp = await fetch("/api/printer_admin", { method: "POST", body: fd });
            const res = await resp.json();
            alertBox.innerHTML = `<span style="color:${res.success ? '#10b981' : '#ef4444'};">${res.msg}</span>`;
            if (res.success) {
                this.loadPrinters(true);
                logToConsole(`打印机 [${name}] 部署成功`, "success");
            }
        } catch (e) {
            logToConsole(`添加异常: ${e.message}`, "error");
        }
    },

    resetUsb: async function() {
        logToConsole("⚡ 正在触发 USB 总线重置...");
        await fetch("/api/system/reset_usb", { method: "POST" });
        logToConsole("✔ USB 总线已复位", "success");
    },

    cleanJobs: async function() {
        logToConsole("🗑️ 正在清空等待队列...");
        await fetch("/api/jobs/clear", { method: "POST" });
        logToConsole("✔ 队列已清空", "success");
    },

    cleanDisk: async function() {
        logToConsole("🧹 正在释放临时缓存...");
        await fetch("/api/system/clean_tmp", { method: "POST" });
        logToConsole("✔ 磁盘临时垃圾已清理", "success");
    }
};

// ==================== 6. 云邮箱服务 ====================
window.MailService = {
    loadConfig: async function() {
        try {
            const resp = await fetch("/api/mail/config");
            const res = await resp.json();
            const cfg = res.data || {};
            document.getElementById("mail-enable").checked = !!cfg.enable;
            document.getElementById("mail-server").value = cfg.server || "imap.qq.com";
            document.getElementById("mail-port").value = cfg.port || 993;
            document.getElementById("mail-user").value = cfg.user || "";
            document.getElementById("mail-pwd").value = cfg.password || "";
            document.getElementById("mail-keyword").value = cfg.keyword || "";
            document.getElementById("mail-whitelist").value = cfg.whitelist || "";
            document.getElementById("mail-pushplus-token").value = cfg.pushplus_token || "";
            if (cfg.default_printer) {
                document.getElementById("mail-default-printer").value = cfg.default_printer;
            }
        } catch (e) {
            logToConsole(`读取邮箱配置异常: ${e.message}`, "error");
        }
    },

    saveConfig: async function() {
        const alertBox = document.getElementById("mail-alert");
        const body = {
            enable: document.getElementById("mail-enable")?.checked,
            server: document.getElementById("mail-server")?.value.trim(),
            port: document.getElementById("mail-port")?.value.trim(),
            user: document.getElementById("mail-user")?.value.trim(),
            password: document.getElementById("mail-pwd")?.value.trim(),
            keyword: document.getElementById("mail-keyword")?.value.trim(),
            whitelist: document.getElementById("mail-whitelist")?.value.trim(),
            default_printer: document.getElementById("mail-default-printer")?.value,
            pushplus_token: document.getElementById("mail-pushplus-token")?.value.trim()
        };

        try {
            const resp = await fetch("/api/mail/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body)
            });
            const res = await resp.json();
            alertBox.innerHTML = `<span style="color:${res.success ? '#10b981' : '#ef4444'};">${res.msg}</span>`;
            logToConsole(`云邮箱配置更新: ${res.msg}`, res.success ? "success" : "error");
        } catch (e) {
            alertBox.innerHTML = `<span style="color:#ef4444;">保存失败: ${e.message}</span>`;
        }
    }
};

document.addEventListener("DOMContentLoaded", () => {
    EnhanceEngine.init();
    PrintManager.init();
    IdCardManager.bindBoxEvents(0);
    DashboardService.loadPrinters(false);
});
