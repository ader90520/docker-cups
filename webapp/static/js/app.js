/**
 * app.js - CUPS 智能工作台前端核心交互逻辑 (集成视口防误触锁与黄金比例响应)
 */

const AppConfig = {
    currentNav: 'print',
    currentPrintType: 'normal',
    currentColorMode: 'monochrome',
    currentOrientation: 'portrait',
    currentPreviewIndex: 0,
    zoomLevel: 1.0,
    panX: 0,
    panY: 0,
    isDragging: false,
    startX: 0,
    startY: 0,
    isViewerActive: false, // 专属交互锁状态：默认锁定
    globalPrintersData: [],
    isPollingActive: false,
    currentLiveScanUrl: null,
    currentScannedFilename: null,
    safeRevoke(url) {
        if (url && typeof url === 'string' && url.startsWith('blob:')) {
            try { URL.revokeObjectURL(url); } catch (e) {}
        }
    },
    getMsg(d, defSuccess = '操作成功', defFail = '操作失败') {
        if (!d) return defFail;
        return d.msg || d.message || (d.success ? defSuccess : defFail);
    }
};

const EnhanceEngine = {
    enhancedCache: {},

    setViewerState(active) {
        AppConfig.isViewerActive = active;
        const vpBox = document.getElementById('viewport-box');
        const lockBadge = document.getElementById('preview-lock-badge');
        const lockIcon = document.getElementById('lock-icon');
        const lockText = document.getElementById('lock-text');

        if (!vpBox || !lockBadge) return;

        if (active) {
            vpBox.classList.remove('locked');
            vpBox.classList.add('unlocked');
            lockBadge.className = 'preview-lock-badge unlocked';
            if (lockIcon) lockIcon.innerText = '🔓';
            if (lockText) lockText.innerText = '已激活漫游';
        } else {
            vpBox.classList.remove('unlocked');
            vpBox.classList.add('locked');
            lockBadge.className = 'preview-lock-badge locked';
            if (lockIcon) lockIcon.innerText = '🔒';
            if (lockText) lockText.innerText = '点击激活操作';
            AppConfig.isDragging = false;
        }
    },

    initViewerInteraction() {
        const vpBox = document.getElementById('viewport-box');
        const lockBadge = document.getElementById('preview-lock-badge');
        if (!vpBox) return;

        // 1. 点击锁标切换锁状态
        if (lockBadge) {
            lockBadge.addEventListener('click', (e) => {
                e.stopPropagation();
                this.setViewerState(!AppConfig.isViewerActive);
            });
        }

        // 2. 点击视口内部任意位置直接激活
        vpBox.addEventListener('click', () => {
            if (!AppConfig.isViewerActive) {
                this.setViewerState(true);
            }
        });

        // 3. 点击视口外部任意区域，自动恢复锁定防误触
        document.addEventListener('click', (e) => {
            if (!vpBox.contains(e.target) && (!lockBadge || !lockBadge.contains(e.target))) {
                if (AppConfig.isViewerActive) {
                    this.setViewerState(false);
                }
            }
        });

        // 4. 鼠标拖拽平移 (仅激活态允许)
        vpBox.addEventListener('mousedown', (e) => {
            if (!AppConfig.isViewerActive) return;
            AppConfig.isDragging = true;
            AppConfig.startX = e.clientX - AppConfig.panX;
            AppConfig.startY = e.clientY - AppConfig.panY;
        });

        window.addEventListener('mousemove', (e) => {
            if (!AppConfig.isDragging || !AppConfig.isViewerActive) return;
            AppConfig.panX = e.clientX - AppConfig.startX;
            AppConfig.panY = e.clientY - AppConfig.startY;
            this.updateTransform();
        });

        window.addEventListener('mouseup', () => { AppConfig.isDragging = false; });

        // 5. 触摸屏拖拽
        vpBox.addEventListener('touchstart', (e) => {
            if (!AppConfig.isViewerActive) return;
            if (e.touches.length === 1) {
                AppConfig.isDragging = true;
                AppConfig.startX = e.touches[0].clientX - AppConfig.panX;
                AppConfig.startY = e.touches[0].clientY - AppConfig.panY;
            }
        });

        window.addEventListener('touchmove', (e) => {
            if (!AppConfig.isDragging || !AppConfig.isViewerActive || e.touches.length !== 1) return;
            AppConfig.panX = e.touches[0].clientX - AppConfig.startX;
            AppConfig.panY = e.touches[0].clientY - AppConfig.startY;
            this.updateTransform();
        });

        window.addEventListener('touchend', () => { AppConfig.isDragging = false; });

        // 6. 核心拦截：滚轮缩放 (未激活时放行给页面，激活时拦截并缩放)
        vpBox.addEventListener('wheel', (e) => {
            if (!AppConfig.isViewerActive) {
                // 锁定态：顺畅滚动浏览器页面
                return;
            }
            e.preventDefault();
            const delta = e.deltaY > 0 ? -0.15 : 0.15;
            this.adjustZoom(delta);
        }, { passive: false });
    },

    updateTransform() {
        const vp = document.getElementById('a4-viewport');
        if (vp) vp.style.transform = `translate(${AppConfig.panX}px, ${AppConfig.panY}px) scale(${AppConfig.zoomLevel})`;
    },

    adjustZoom(delta) {
        AppConfig.zoomLevel = Math.max(0.5, Math.min(4.0, AppConfig.zoomLevel + delta));
        this.updateTransform();
    },

    resetZoom() {
        AppConfig.zoomLevel = 1.0;
        AppConfig.panX = 0;
        AppConfig.panY = 0;
        this.updateTransform();
    },

    changePage(delta) {
        let files = (AppConfig.currentPrintType === 'normal') ? PrintManager.normalFiles : PrintManager.invoiceFiles;
        if (!files.length) return;
        const target = AppConfig.currentPreviewIndex + delta;
        if (target >= 0 && target < files.length) {
            AppConfig.currentPreviewIndex = target;
            this.renderPreview();
        }
    },

    invalidateCache() {
        Object.keys(this.enhancedCache).forEach(k => {
            if (this.enhancedCache[k]) URL.revokeObjectURL(this.enhancedCache[k]);
        });
        this.enhancedCache = {};
    },

    async renderPreview() {
        const canvas = document.getElementById('canvas-preview');
        const pdfCanvas = document.getElementById('pdf-canvas-render');
        const tip = document.getElementById('viewport-empty-tip');
        const counterLbl = document.getElementById('page-counter-lbl');
        const vp = document.getElementById('a4-viewport');
        if (!canvas || !vp) return;
        const ctx = canvas.getContext('2d');

        pdfCanvas.style.display = 'none';

        if (AppConfig.currentPrintType === 'idcard') {
            canvas.style.display = 'none';
            tip.style.display = 'none';
            counterLbl.innerText = "1 / 1";
            vp.style.width = '300px';
            vp.style.height = '424px';
            IdCardManager.renderPreviewGrid();
            return;
        }

        let files = (AppConfig.currentPrintType === 'normal') ? PrintManager.normalFiles : PrintManager.invoiceFiles;
        let isMerge = (AppConfig.currentPrintType === 'normal') ? document.getElementById('chk-normal-merge').checked : document.getElementById('chk-invoice-merge').checked;

        if (files.length === 0) {
            canvas.style.display = 'none';
            tip.style.display = 'block';
            tip.innerText = "上传文件后实时呈现效果";
            counterLbl.innerText = "0 / 0";
            this.invalidateCache();
            return;
        }

        if (AppConfig.currentPreviewIndex >= files.length) {
            AppConfig.currentPreviewIndex = 0;
        }

        counterLbl.innerText = `${AppConfig.currentPreviewIndex + 1} / ${files.length}`;
        tip.style.display = 'none';
        canvas.style.display = 'block';

        const isLand = (AppConfig.currentOrientation === 'landscape');
        const baseW = isLand ? 424 : 300;
        const baseH = isLand ? 300 : 424;
        vp.style.width = baseW + 'px';
        vp.style.height = baseH + 'px';

        const cw = isLand ? 1131 : 800;
        const ch = isLand ? 800 : 1131;
        canvas.width = cw;
        canvas.height = ch;
        canvas.style.width = baseW + 'px';
        canvas.style.height = baseH + 'px';

        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, cw, ch);

        const currentFile = files[AppConfig.currentPreviewIndex];
        const isPdf = currentFile.name.toLowerCase().endsWith('.pdf');

        if (isPdf) {
            this.renderPdfPreview(currentFile, cw, ch, baseW, baseH);
            return;
        }

        const isEnhance = (AppConfig.currentPrintType === 'normal') && (document.getElementById('p-enhance').value === 'true');

        if (isEnhance && !isMerge) {
            const fileKey = `${currentFile.name}_${currentFile.size}_${AppConfig.currentColorMode}`;

            if (this.enhancedCache[fileKey]) {
                this.drawImageToCanvas(ctx, this.enhancedCache[fileKey], cw, ch);
            } else {
                tip.innerText = "⏳ 正在识别纸张轮廓、拉平并保留彩色细节...";
                tip.style.display = 'block';

                let uploadPayload = currentFile;
                if (window.DocAI && window.DocAI.readyStatus && window.DocAI.readyStatus["corner"]) {
                    const cropped = await window.DocAI.processDocument(currentFile);
                    if (cropped) uploadPayload = cropped;
                }

                const fd = new FormData();
                fd.append('file', uploadPayload, currentFile.name);
                fd.append('color_mode', AppConfig.currentColorMode);
                if (uploadPayload !== currentFile) fd.append('client_cropped', 'true');

                fetch('/api/preview/enhance', { method: 'POST', body: fd })
                    .then(r => {
                        if (!r.ok) return fetch('/api/preview_enhance', { method: 'POST', body: fd });
                        return r;
                    })
                    .then(r => {
                        if (!r.ok) throw new Error('后端处理未响应');
                        return r.blob();
                    })
                    .then(blob => {
                        tip.style.display = 'none';
                        this.enhancedCache[fileKey] = blob;
                        this.drawImageToCanvas(ctx, blob, cw, ch);
                    })
                    .catch(err => {
                        console.warn("Enhance fallback:", err);
                        tip.style.display = 'none';
                        this.drawImageToCanvas(ctx, currentFile, cw, ch);
                    });
            }

        } else if (isMerge && files.length > 1) {
            const count = files.length;
            let cols = (count <= 2) ? (isLand ? 2 : 1) : 2;
            let rows = (count <= 2) ? (isLand ? 1 : 2) : (count <= 4 ? 2 : 3);
            const cellW = cw / cols;
            const cellH = ch / rows;
            const margin = 20;

            files.slice(0, cols * rows).forEach((f, idx) => {
                const img = new Image();
                const tempUrl = URL.createObjectURL(f);
                img.onload = () => {
                    URL.revokeObjectURL(tempUrl);
                    const ratio = Math.min((cellW - margin * 2) / img.width, (cellH - margin * 2) / img.height);
                    const tw = img.width * ratio;
                    const th = img.height * ratio;
                    const col = idx % cols;
                    const row = Math.floor(idx / cols);
                    const x = col * cellW + (cellW - tw) / 2;
                    const y = row * cellH + (cellH - th) / 2;
                    ctx.drawImage(img, x, y, tw, th);
                };
                img.src = tempUrl;
            });

        } else {
            this.drawImageToCanvas(ctx, currentFile, cw, ch);
        }
    },

    drawImageToCanvas(ctx, source, cw, ch) {
        const img = new Image();
        const srcUrl = (source instanceof Blob || source instanceof File) ? URL.createObjectURL(source) : source;
        img.onload = () => {
            if (source instanceof Blob || source instanceof File) {
                URL.revokeObjectURL(srcUrl);
            }
            const ratio = Math.min((cw * 0.96) / img.width, (ch * 0.96) / img.height);
            const tw = img.width * ratio;
            const th = img.height * ratio;
            const x = (cw - tw) / 2;
            const y = (ch - th) / 2;

            ctx.drawImage(img, x, y, tw, th);

            const isMono = (AppConfig.currentColorMode === 'monochrome');
            const isEnhance = (AppConfig.currentPrintType === 'normal') && (document.getElementById('p-enhance').value === 'true');
            if (isMono && !isEnhance) {
                const imgData = ctx.getImageData(x, y, tw, th);
                const d = imgData.data;
                for (let i = 0; i < d.length; i += 4) {
                    const luma = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
                    d[i] = luma; d[i + 1] = luma; d[i + 2] = luma;
                }
                ctx.putImageData(imgData, x, y);
            }
        };
        img.src = srcUrl;
    },

    renderPdfPreview(file, cw, ch, baseW, baseH) {
        const reader = new FileReader();
        reader.onload = function(e) {
            const typedarray = new Uint8Array(e.target.result);
            if (typeof pdfjsLib !== 'undefined') {
                pdfjsLib.getDocument(typedarray).promise.then(pdf => {
                    pdf.getPage(1).then(page => {
                        const canvas = document.getElementById('pdf-canvas-render');
                        const ctx = canvas.getContext('2d');
                        const viewport = page.getViewport({ scale: 1.5 });
                        canvas.width = viewport.width;
                        canvas.height = viewport.height;
                        canvas.style.width = baseW + 'px';
                        canvas.style.height = baseH + 'px';
                        canvas.style.display = 'block';
                        document.getElementById('canvas-preview').style.display = 'none';

                        const renderContext = { canvasContext: ctx, viewport: viewport };
                        page.render(renderContext);
                    });
                });
            }
        };
        reader.readAsArrayBuffer(file);
    }
};

const IdCardManager = {
    persons: [
        { front: null, back: null, rot_f: 0, rot_b: 0, zoom_f: 1.0, zoom_b: 1.0, url_f: null, url_b: null }
    ],

    init() { this.rebuildDOM(); },
    pick(idx, side) { document.getElementById(`id-${side}-${idx}`).click(); },

    onFile(idx, side, input) {
        if (input.files && input.files[0]) {
            this.persons[idx][side] = input.files[0];
            this.persons[idx][`rot_${side}`] = 0;
            this.persons[idx][`zoom_${side}`] = 1.0;
            this.updateSlotView(idx, side, input.files[0]);
            this.renderPreviewGrid();
        }
    },

    onZoomChange(idx, side, val) {
        this.persons[idx][`zoom_${side}`] = parseFloat(val);
        const img = document.getElementById(`img-${side}-${idx}`);
        if (img) img.style.transform = `scale(${val})`;
        this.renderPreviewGrid();
    },

    updateSlotView(idx, side, file) {
        const upImg = document.getElementById(`img-${side}-${idx}`);
        const lbl = document.getElementById(`lbl-${side}-${idx}`);
        const urlKey = `url_${side}`;
        if (this.persons[idx][urlKey]) {
            AppConfig.safeRevoke(this.persons[idx][urlKey]);
            this.persons[idx][urlKey] = null;
        }

        if (file) {
            const u = URL.createObjectURL(file);
            this.persons[idx][urlKey] = u;
            upImg.src = u;
            upImg.style.display = 'block';
            lbl.style.display = 'none';
        } else {
            upImg.src = '';
            upImg.style.display = 'none';
            lbl.style.display = 'block';
        }
    },

    rotate(idx, side) {
        const file = this.persons[idx][side];
        if (!file) return;

        const img = new Image();
        const tempU = URL.createObjectURL(file);
        img.onload = () => {
            URL.revokeObjectURL(tempU);
            const off = document.createElement('canvas');
            off.width = img.height;
            off.height = img.width;
            const oCtx = off.getContext('2d');
            oCtx.translate(off.width / 2, off.height / 2);
            oCtx.rotate(90 * Math.PI / 180);
            oCtx.drawImage(img, -img.width / 2, -img.height / 2);

            off.toBlob(blob => {
                const rotated = new File([blob], file.name, { type: 'image/jpeg' });
                this.persons[idx][side] = rotated;
                this.updateSlotView(idx, side, rotated);
                this.renderPreviewGrid();
            }, 'image/jpeg', 0.95);
        };
        img.src = tempU;
    },

    deletePhoto(idx, side) {
        this.persons[idx][side] = null;
        this.persons[idx][`zoom_${side}`] = 1.0;
        this.updateSlotView(idx, side, null);
        const fInput = document.getElementById(`id-${side}-${idx}`);
        if (fInput) fInput.value = '';
        this.renderPreviewGrid();
    },

    clearPerson(idx) {
        this.deletePhoto(idx, 'f');
        this.deletePhoto(idx, 'b');
    },

    addPerson() {
        if (this.persons.length >= 3) {
            alert('单张 A4 纸最多排版 3 人的身份证！');
            return;
        }
        this.persons.push({ front: null, back: null, rot_f: 0, rot_b: 0, zoom_f: 1.0, zoom_b: 1.0, url_f: null, url_b: null });
        this.rebuildDOM();
        this.renderPreviewGrid();
    },

    removePerson(idx) {
        if (this.persons.length <= 1) {
            this.clearPerson(0);
            return;
        }
        const p = this.persons.splice(idx, 1)[0];
        AppConfig.safeRevoke(p.url_f);
        AppConfig.safeRevoke(p.url_b);
        this.rebuildDOM();
        this.renderPreviewGrid();
    },

    rebuildDOM() {
        const box = document.getElementById('idcard-person-container');
        if (!box) return;
        box.innerHTML = '';

        this.persons.forEach((p, idx) => {
            const div = document.createElement('div');
            div.className = 'idcard-person-card';
            div.id = `id-person-row-${idx}`;
            div.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <b style="font-size:12px; color:#1e293b;">人员 ${idx + 1} 身份证照片:</b>
                    <div>
                        <button type="button" class="btn-del" style="font-size:11px; margin-right:6px;" onclick="IdCardManager.clearPerson(${idx})">清空</button>
                        ${idx > 0 ? `<button type="button" class="btn-del" style="font-size:11px; font-weight:700;" onclick="IdCardManager.removePerson(${idx})">🗑️ 删除该人员</button>` : ''}
                    </div>
                </div>
                <div class="idcard-grid">
                    <div class="idcard-box-wrapper">
                        <div class="idcard-box" onclick="IdCardManager.pick(${idx}, 'f')">
                            <span class="text-tip" id="lbl-f-${idx}" style="${p.front ? 'display:none;' : ''}">点击上传：正面 (人像)</span>
                            <img id="img-f-${idx}" src="${p.url_f || ''}" style="${p.front ? 'display:block;' : 'display:none;'}; transform:scale(${p.zoom_f});" alt="正面">
                        </div>
                        <div class="idcard-actions">
                            <button type="button" class="idcard-mini-btn" onclick="IdCardManager.rotate(${idx}, 'f')">↺ 旋转</button>
                            <div class="zoom-slider-wrap">
                                <span>裁切</span>
                                <input type="range" min="1.0" max="2.2" step="0.05" value="${p.zoom_f}" oninput="IdCardManager.onZoomChange(${idx}, 'f', this.value)">
                            </div>
                            <button type="button" class="idcard-mini-btn del" onclick="IdCardManager.deletePhoto(${idx}, 'f')">❌</button>
                        </div>
                    </div>
                    <div class="idcard-box-wrapper">
                        <div class="idcard-box" onclick="IdCardManager.pick(${idx}, 'b')">
                            <span class="text-tip" id="lbl-b-${idx}" style="${p.back ? 'display:none;' : ''}">点击上传：反面 (国徽)</span>
                            <img id="img-b-${idx}" src="${p.url_b || ''}" style="${p.back ? 'display:block;' : 'display:none;'}; transform:scale(${p.zoom_b});" alt="反面">
                        </div>
                        <div class="idcard-actions">
                            <button type="button" class="idcard-mini-btn" onclick="IdCardManager.rotate(${idx}, 'b')">↺ 旋转</button>
                            <div class="zoom-slider-wrap">
                                <span>裁切</span>
                                <input type="range" min="1.0" max="2.2" step="0.05" value="${p.zoom_b}" oninput="IdCardManager.onZoomChange(${idx}, 'b', this.value)">
                            </div>
                            <button type="button" class="idcard-mini-btn del" onclick="IdCardManager.deletePhoto(${idx}, 'b')">❌</button>
                        </div>
                    </div>
                </div>
                <input type="file" id="id-f-${idx}" accept="image/*" style="display:none;" onchange="IdCardManager.onFile(${idx}, 'f', this)">
                <input type="file" id="id-b-${idx}" accept="image/*" style="display:none;" onchange="IdCardManager.onFile(${idx}, 'b', this)">
            `;
            box.appendChild(div);
        });
    },

    renderPreviewGrid() {
        const holder = document.getElementById('viewport-idcard-holder');
        if (!holder) return;
        holder.innerHTML = '';
        holder.style.display = 'block';

        const count = this.persons.length;
        if (count === 1) {
            const p = this.persons[0];
            const div = document.createElement('div');
            div.className = 'idcard-layout-single';
            div.innerHTML = `
                <div class="idcard-single-item"><img src="${p.url_f || ''}" style="${p.front ? 'display:block;' : 'display:none;'}; transform:scale(${p.zoom_f});"></div>
                <div class="idcard-single-item"><img src="${p.url_b || ''}" style="${p.back ? 'display:block;' : 'display:none;'}; transform:scale(${p.zoom_b});"></div>
            `;
            holder.appendChild(div);
        } else {
            const div = document.createElement('div');
            div.className = 'idcard-layout-multi';
            this.persons.forEach(p => {
                const row = document.createElement('div');
                row.className = 'idcard-multi-row';
                row.innerHTML = `
                    <div class="idcard-multi-item"><img src="${p.url_f || ''}" style="${p.front ? 'display:block;' : 'display:none;'}; transform:scale(${p.zoom_f});"></div>
                    <div class="idcard-multi-item"><img src="${p.url_b || ''}" style="${p.back ? 'display:block;' : 'display:none;'}; transform:scale(${p.zoom_b});"></div>
                `;
                div.appendChild(row);
            });
            holder.appendChild(div);
        }
    }
};

const PrintManager = {
    normalFiles: [],
    invoiceFiles: [],

    onFilesSelected(type, files) {
        if (!files) return;
        const target = (type === 'normal') ? this.normalFiles : this.invoiceFiles;
        for (let i = 0; i < files.length; i++) target.push(files[i]);
        AppConfig.currentPreviewIndex = target.length - files.length;
        this.renderFileList(type);
    },

    onMergeChange() {
        EnhanceEngine.resetZoom();
        EnhanceEngine.renderPreview();
    },

    renderFileList(type) {
        const isNormal = (type === 'normal');
        const list = isNormal ? this.normalFiles : this.invoiceFiles;
        const wrap = document.getElementById(isNormal ? 'file-list-normal' : 'file-list-invoice');
        if (!wrap) return;
        wrap.innerHTML = '';

        if (list.length === 0) {
            wrap.style.display = 'none';
            AppConfig.currentPreviewIndex = 0;
            EnhanceEngine.invalidateCache();
            EnhanceEngine.renderPreview();
            return;
        }

        wrap.style.display = 'block';
        list.forEach((f, idx) => {
            const div = document.createElement('div');
            div.className = 'file-row ' + (idx === AppConfig.currentPreviewIndex ? 'active' : '');
            div.onclick = (e) => {
                if (e.target.tagName.toLowerCase() === 'button') return;
                AppConfig.currentPreviewIndex = idx;
                this.renderFileList(type);
            };
            div.innerHTML = `
                <span><b>[${idx + 1}]</b> 📄 ${f.name}</span>
                <button type="button" class="btn-del" onclick="PrintManager.removeFile('${type}', ${idx})">❌</button>
            `;
            wrap.appendChild(div);
        });

        EnhanceEngine.renderPreview();
    },

    removeFile(type, idx) {
        const target = (type === 'normal') ? this.normalFiles : this.invoiceFiles;
        target.splice(idx, 1);
        if (AppConfig.currentPreviewIndex >= target.length) {
            AppConfig.currentPreviewIndex = Math.max(0, target.length - 1);
        }
        this.renderFileList(type);
    },

    clearFiles(type) {
        if (type === 'normal') this.normalFiles = [];
        else this.invoiceFiles = [];
        AppConfig.currentPreviewIndex = 0;
        this.renderFileList(type);
    },

    onPaperSizeChange() {
        const media = document.getElementById('p-media').value;
        const vp = document.getElementById('a4-viewport');
        const isLand = (AppConfig.currentOrientation === 'landscape');

        if (media === 'B5') {
            vp.style.width = isLand ? '412px' : '290px';
            vp.style.height = isLand ? '290px' : '412px';
        } else if (media === 'A5') {
            vp.style.width = isLand ? '345px' : '245px';
            vp.style.height = isLand ? '245px' : '345px';
        } else {
            vp.style.width = isLand ? '424px' : '300px';
            vp.style.height = isLand ? '300px' : '424px';
        }
        EnhanceEngine.resetZoom();
        EnhanceEngine.renderPreview();
    },

    setColorMode(mode) {
        AppConfig.currentColorMode = mode;
        document.getElementById('btn-color-mono').className = 'flat-radio-btn ' + (mode === 'monochrome' ? 'active' : '');
        document.getElementById('btn-color-rgb').className = 'flat-radio-btn ' + (mode === 'color' ? 'active' : '');
        EnhanceEngine.invalidateCache();
        EnhanceEngine.renderPreview();
    },

    setOrientation(orient) {
        AppConfig.currentOrientation = orient;
        const vp = document.getElementById('a4-viewport');
        document.getElementById('btn-orient-port').className = 'flat-radio-btn ' + (orient === 'portrait' ? 'active' : '');
        document.getElementById('btn-orient-land').className = 'flat-radio-btn ' + (orient === 'landscape' ? 'active' : '');
        if (orient === 'landscape') vp.classList.add('landscape');
        else vp.classList.remove('landscape');
        this.onPaperSizeChange();
    },

    switchSubTab(t) {
        AppConfig.currentPrintType = t;
        AppConfig.currentPreviewIndex = 0;
        document.querySelectorAll('.type-item').forEach(i => i.classList.remove('active'));
        document.getElementById('tab-sub-' + t).classList.add('active');

        const secNormal = document.getElementById('sec-upload-normal');
        const secInvoice = document.getElementById('sec-upload-invoice');
        const secID = document.getElementById('sec-upload-idcard');
        const itemEnhance = document.getElementById('item-enhance-wrap');
        const itemPageRanges = document.getElementById('item-pageranges-wrap');
        const vpHolder = document.getElementById('viewport-idcard-holder');
        const canvas = document.getElementById('canvas-preview');

        EnhanceEngine.resetZoom();

        if (t === 'normal') {
            itemEnhance.style.display = 'block';
            itemPageRanges.style.display = 'block';
            secNormal.style.display = 'block';
            secInvoice.style.display = 'none';
            secID.style.display = 'none';
            vpHolder.style.display = 'none';
            EnhanceEngine.renderPreview();
        } else if (t === 'invoice') {
            itemEnhance.style.display = 'none';
            itemPageRanges.style.display = 'none';
            secNormal.style.display = 'none';
            secInvoice.style.display = 'block';
            secID.style.display = 'none';
            vpHolder.style.display = 'none';
            EnhanceEngine.renderPreview();
        } else {
            itemEnhance.style.display = 'none';
            itemPageRanges.style.display = 'none';
            secNormal.style.display = 'none';
            secInvoice.style.display = 'none';
            secID.style.display = 'block';
            canvas.style.display = 'none';
            IdCardManager.renderPreviewGrid();
        }
    },

    submitJob() {
        const btn = document.getElementById('btn-submit');
        const alertBox = document.getElementById('print-alert');
        const printer = document.getElementById('printer-select').value;
        const copies = document.getElementById('p-copies').value;
        const duplex = document.getElementById('p-duplex').value;
        const media = document.getElementById('p-media').value;
        const enhance = (AppConfig.currentPrintType === 'normal') ? document.getElementById('p-enhance').value : 'false';
        const pageRanges = (AppConfig.currentPrintType === 'normal') ? document.getElementById('p-page-ranges').value.trim() : '';

        if (!printer) {
            alert('请选择打印机！');
            return;
        }

        if (AppConfig.currentPrintType === 'idcard') {
            let validPairs = 0;
            const fd = new FormData();
            fd.append('printer', printer);
            fd.append('copies', copies);

            for (let i = 0; i < IdCardManager.persons.length; i++) {
                if (IdCardManager.persons[i].front && IdCardManager.persons[i].back) {
                    fd.append('front', IdCardManager.persons[i].front);
                    fd.append('back', IdCardManager.persons[i].back);
                    validPairs++;
                }
            }

            if (validPairs === 0) {
                alert('请至少上传一位人员的完整正面与反面照片！');
                return;
            }

            btn.disabled = true;
            btn.innerText = `正在拼版 ${validPairs} 人的身份证...`;

            fetch('/api/idcard', { method: 'POST', body: fd })
                .then(r => r.json())
                .then(res => {
                    btn.disabled = false;
                    btn.innerText = '🖨️ 提交打印';
                    alertBox.style.display = 'block';
                    alertBox.className = 'status-alert ' + (res.success ? 'status-success' : 'status-error');
                    alertBox.innerText = AppConfig.getMsg(res, '身份证拼版已成功送达打印机！');
                })
                .catch(err => {
                    btn.disabled = false;
                    btn.innerText = '🖨️ 提交打印';
                    alertBox.style.display = 'block';
                    alertBox.className = 'status-alert status-error';
                    alertBox.innerText = '拼版异常: ' + err;
                });
            return;
        }

        const targetList = (AppConfig.currentPrintType === 'invoice') ? this.invoiceFiles : this.normalFiles;
        const isMerge = (AppConfig.currentPrintType === 'invoice') ? document.getElementById('chk-invoice-merge').checked : document.getElementById('chk-normal-merge').checked;

        if (targetList.length === 0) {
            alert('请先选取文件！');
            return;
        }

        btn.disabled = true;
        btn.innerText = '正在送入打印队列...';

        const fd = new FormData();
        fd.append('printer', printer);
        fd.append('copies', copies);
        fd.append('duplex', duplex);
        fd.append('color_mode', AppConfig.currentColorMode);
        fd.append('media', media);
        fd.append('enhance', enhance);
        if (pageRanges) fd.append('page_ranges', pageRanges);
        if (isMerge && targetList.length > 1) fd.append('multi_merge', 'true');

        targetList.forEach(f => fd.append('file', f));

        fetch('/api/print', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(res => {
                btn.disabled = false;
                btn.innerText = '🖨️ 提交打印';
                alertBox.style.display = 'block';
                alertBox.className = 'status-alert ' + (res.success ? 'status-success' : 'status-error');
                alertBox.innerText = AppConfig.getMsg(res, '文件已送达打印队列！');
                if (res.success) {
                    if (AppConfig.currentPrintType === 'invoice') PrintManager.clearFiles('invoice');
                    else PrintManager.clearFiles('normal');
                }
            })
            .catch(err => {
                btn.disabled = false;
                btn.innerText = '🖨️ 提交打印';
                alertBox.style.display = 'block';
                alertBox.className = 'status-alert status-error';
                alertBox.innerText = '打印服务异常: ' + err;
            });
    }
};

const ScanService = {
    loadDevices() {
        fetch('/api/scan/devices')
            .then(r => r.json())
            .then(d => {
                const sel = document.getElementById('scan-dev-select');
                if (!sel) return;
                sel.innerHTML = '';
                if (d.success && d.data && d.data.devices && d.data.devices.length > 0) {
                    d.data.devices.forEach(dev => {
                        const opt = document.createElement('option');
                        opt.value = dev.id;
                        opt.innerText = dev.name;
                        sel.appendChild(opt);
                    });
                } else {
                    const opt = document.createElement('option');
                    opt.value = '';
                    opt.innerText = '未检测到可用扫描仪';
                    sel.appendChild(opt);
                }
            });
    },

    startScan() {
        const dev = document.getElementById('scan-dev-select').value;
        const res = document.getElementById('scan-res').value;
        const mode = document.getElementById('scan-mode').value;
        const alertBox = document.getElementById('scan-alert');
        const btn = document.getElementById('btn-start-scan');

        if (!dev) {
            alert('未检测到可用扫描仪！');
            return;
        }

        btn.disabled = true;
        btn.innerText = '⏳ 正在硬件扫描中...';
        alertBox.style.display = 'block';
        alertBox.className = 'status-alert status-success';
        alertBox.innerText = '正在调动硬件扫描头...';

        const fd = new FormData();
        fd.append('device', dev);
        fd.append('resolution', res);
        fd.append('mode', mode);

        fetch('/api/scan', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(d => {
                btn.disabled = false;
                btn.innerText = '📷 启动硬件扫描并呈现原图';
                if (d.success) {
                    alertBox.innerText = AppConfig.getMsg(d, '扫描成功！原画无损呈现');
                    const liveImg = document.getElementById('scan-live-img');
                    const emptyTip = document.getElementById('scan-empty-tip');
                    const dlBtn = document.getElementById('scan-download-anchor');
                    const directBtn = document.getElementById('btn-scan-direct-print');

                    emptyTip.style.display = 'none';
                    AppConfig.currentScannedFilename = d.data.filename;

                    const fetchUrl = d.data.download_url || ('/api/scan/download?file=' + d.data.filename);
                    fetch(fetchUrl)
                        .then(res => res.blob())
                        .then(blob => {
                            AppConfig.safeRevoke(AppConfig.currentLiveScanUrl);
                            AppConfig.currentLiveScanUrl = URL.createObjectURL(blob);
                            liveImg.src = AppConfig.currentLiveScanUrl;
                            liveImg.style.display = 'block';
                        });

                    dlBtn.href = fetchUrl;
                    dlBtn.style.display = 'inline-block';
                    if (directBtn) directBtn.style.display = 'inline-block';
                } else {
                    alertBox.className = 'status-alert status-error';
                    alertBox.innerText = '扫描中断: ' + AppConfig.getMsg(d, '', '硬件未就绪');
                }
            })
            .catch(err => {
                btn.disabled = false;
                btn.innerText = '📷 启动硬件扫描并呈现原图';
                alertBox.className = 'status-alert status-error';
                alertBox.innerText = '扫描请求异常: ' + err;
            });
    },

    printCurrentFile() {
        if (!AppConfig.currentScannedFilename) {
            alert('请先完成一次扫描操作！');
            return;
        }

        const sel = document.getElementById('printer-select');
        const targetPrinter = sel ? sel.value : '';
        const btn = document.getElementById('btn-scan-direct-print');
        const alertBox = document.getElementById('scan-alert');

        if (btn) { btn.disabled = true; btn.innerText = '⏳ 正在派发打印...'; }

        const fd = new FormData();
        fd.append('filename', AppConfig.currentScannedFilename);
        if (targetPrinter) fd.append('printer', targetPrinter);
        fd.append('copies', '1');

        fetch('/api/scan/print', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(d => {
                if (btn) { btn.disabled = false; btn.innerText = '🖨️ 打印该件'; }
                alertBox.style.display = 'block';
                alertBox.className = 'status-alert ' + (d.success ? 'status-success' : 'status-error');
                alertBox.innerText = AppConfig.getMsg(d, '扫描件已直接下发至打印机出纸！');
            })
            .catch(err => {
                if (btn) { btn.disabled = false; btn.innerText = '🖨️ 打印该件'; }
                alertBox.style.display = 'block';
                alertBox.className = 'status-alert status-error';
                alertBox.innerText = '直接打印异常: ' + err;
            });
    }
};

const DashboardService = {
    loadPrinters(renderTable, callback) {
        if (AppConfig.isPollingActive) { if (callback) callback(); return; }
        AppConfig.isPollingActive = true;

        fetch('/api/devices')
            .then(r => r.json())
            .then(d => {
                AppConfig.isPollingActive = false;
                if (!d.success || !d.data) { if (callback) callback(); return; }
                const data = d.data;
                AppConfig.globalPrintersData = data.printers || [];

                const sel = document.getElementById('printer-select');
                let currentSelected = sel ? sel.value : '';

                if (sel) {
                    sel.innerHTML = '';
                    data.printers.forEach(p => {
                        const opt = document.createElement('option');
                        opt.value = p.name;
                        opt.innerText = p.name + (p.is_default ? ' (默认)' : '');
                        if (currentSelected ? (p.name === currentSelected) : p.is_default) {
                            opt.selected = true;
                        }
                        sel.appendChild(opt);
                    });
                }

                this.updateMetrics(data);

                if (!renderTable) { if (callback) callback(); return; }

                const cardList = document.getElementById('device-card-list');
                if (!cardList) return;
                cardList.innerHTML = '';
                if (data.printers.length === 0) {
                    cardList.innerHTML = '<div style="text-align:center; padding:20px; color:#94a3b8;">暂未检测到打印机，请在下方搜索驱动添加</div>';
                    if (callback) callback();
                    return;
                }

                data.printers.forEach(p => {
                    const card = document.createElement('div');
                    card.style.border = '1px solid var(--border-color)';
                    card.style.borderRadius = '8px';
                    card.style.padding = '12px';
                    card.style.background = '#fff';

                    let warnTags = '';
                    let resumeBtn = '';
                    if (p.has_error || p.error_code) {
                        warnTags += `<span style="background:#fee2e2; color:#b91c1c; padding:2px 6px; border-radius:4px; font-size:11px; font-weight:800; border:1px solid #fca5a5;">🚨 硬件故障: ${p.error_code || '异常'}</span> `;
                        resumeBtn = `<button type="button" class="nav-btn" style="background:#b91c1c; color:#fff; border:none; font-weight:700;" onclick="DashboardService.resumePrinter('${p.name}')">▶️ 远程恢复</button>`;
                    }

                    card.innerHTML = `
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <div>
                                <span style="font-size:14px; font-weight:700; color:#1e293b;">${p.name}</span>
                                ${p.is_default ? '<span style="background:#dbeafe; color:#1d4ed8; padding:2px 6px; border-radius:4px; font-size:11px; margin-left:6px;">默认</span>' : ''}
                            </div>
                            <div>${warnTags}</div>
                        </div>
                        <div style="font-size:11px; color:var(--text-sub); margin-bottom:8px;">状态: ${p.status_msg || p.status || '就绪'}</div>
                        <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px dashed #f1f5f9; padding-top:8px;">
                            <label style="font-size:12px; display:flex; align-items:center; gap:6px; cursor:pointer;">
                                <input type="checkbox" ${p.is_shared ? 'checked' : ''} onchange="DashboardService.toggleShare('${p.name}', this.checked)">
                                <b>共享此打印机 (AirPrint)</b>
                            </label>
                            <div style="display:flex; gap:6px;">
                                ${resumeBtn}
                                ${!p.is_default ? `<button type="button" class="nav-btn" onclick="DashboardService.setDefault('${p.name}')">设为默认</button>` : ''}
                                <button type="button" class="nav-btn" onclick="DashboardService.testPage('${p.name}')">测试页</button>
                            </div>
                        </div>
                    `;
                    cardList.appendChild(card);
                });

                if (callback) callback();
            })
            .catch(err => {
                AppConfig.isPollingActive = false;
                if (callback) callback();
            });
    },

    updateMetrics(data) {
        if (!data) return;

        const pill = document.getElementById('dash-status-pill');
        let isBusy = (data.queue_count && data.queue_count > 0);
        let pObj = (data.printers && data.printers.length > 0) ? data.printers[0] : null;

        if (pill) {
            if (pObj && pObj.has_error) {
                pill.className = 'status-badge-pill stopped';
                pill.innerText = `异常 (${pObj.error_code || '故障'})`;
            } else if (isBusy) {
                pill.className = 'status-badge-pill busy';
                pill.innerText = '处理中';
            } else {
                pill.className = 'status-badge-pill';
                pill.innerText = '空闲';
            }
        }

        const countEl = document.getElementById('dash-queue-count');
        if (countEl && data.queue_count !== undefined) countEl.innerText = data.queue_count;

        const hist = document.getElementById('dash-completed-history');
        if (hist && data.completed_jobs && data.completed_jobs.length > 0) {
            hist.innerHTML = '';
            data.completed_jobs.forEach(j => {
                const div = document.createElement('div');
                div.style.padding = '3px 0';
                div.style.borderBottom = '1px solid #f1f5f9';
                div.style.display = 'flex';
                div.style.justifyContent = 'space-between';
                div.innerHTML = `<span>✔ ${j.job_id}</span><span style="color:#94a3b8;">${j.time}</span>`;
                hist.appendChild(div);
            });
        }
    },

    onPrinterChange() {
        const sel = document.getElementById('printer-select');
        if (!sel || !sel.value) return;
        const p = AppConfig.globalPrintersData.find(item => item.name === sel.value);
        const pill = document.getElementById('dash-status-pill');
        if (!pill) return;

        if (p && (p.has_error || p.error_code)) {
            pill.className = 'status-badge-pill stopped';
            pill.innerText = `异常 (${p.error_code || '故障'})`;
        } else {
            pill.className = 'status-badge-pill';
            pill.innerText = '空闲';
        }
    },

    resumePrinter(name) {
        const fd = new FormData();
        fd.append('action', 'resume_printer');
        fd.append('printer', name);
        fetch('/api/printer_admin', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(d => {
                alert(AppConfig.getMsg(d, '已向打印机发送远程恢复指令！'));
                this.loadPrinters(true);
            });
    },

    loadDiscoveredDevices() {
        const sel = document.getElementById('discovered-device-select');
        if (!sel) return;
        sel.innerHTML = '<option value="">正在检测物理硬件端口...</option>';

        fetch('/api/printer_admin?action=discovered_devices')
            .then(r => r.json())
            .then(d => {
                sel.innerHTML = '';
                if (d.success && d.data && d.data.length > 0) {
                    d.data.forEach(item => {
                        const opt = document.createElement('option');
                        opt.value = item.uri;
                        opt.innerText = `${item.name} (${item.uri})`;
                        sel.appendChild(opt);
                    });
                } else {
                    const opt = document.createElement('option');
                    opt.value = '';
                    opt.innerText = '未检测到可用物理打印机 (请检查 USB 数据线与电源)';
                    sel.appendChild(opt);
                }
            });
    },

    searchDrivers() {
        const kw = document.getElementById('driver-search-kw').value.trim();
        const resBox = document.getElementById('driver-search-results');
        resBox.style.display = 'block';
        resBox.innerHTML = '<div style="padding:10px; color:#64748b;">正在检索 CUPS 631 系统驱动池...</div>';

        fetch('/api/printer_admin?action=drivers&q=' + encodeURIComponent(kw))
            .then(r => r.json())
            .then(d => {
                if (d.success && d.data && d.data.length > 0) {
                    resBox.innerHTML = '';
                    d.data.forEach(item => {
                        const row = document.createElement('div');
                        row.style.padding = '6px 10px';
                        row.style.borderBottom = '1px solid #f1f5f9';
                        row.style.display = 'flex';
                        row.style.justifyContent = 'space-between';
                        row.style.alignItems = 'center';
                        row.innerHTML = `
                            <div>
                                <b style="color:#1e293b;">${item.name}</b><br>
                                <span style="color:#94a3b8; font-size:11px;">${item.id}</span>
                            </div>
                            <button type="button" class="nav-btn" style="padding:3px 8px; font-size:11px;" onclick="DashboardService.bindDriver('${item.id}', '${item.name}')">选取并绑定</button>
                        `;
                        resBox.appendChild(row);
                    });
                } else {
                    resBox.innerHTML = '<div style="padding:10px; color:#ef4444;">未检索到匹配驱动。</div>';
                }
            });
    },

    bindDriver(drvId, drvName) {
        const selDev = document.getElementById('discovered-device-select');
        const selectedUri = selDev ? selDev.value.trim() : '';

        if (!selectedUri) {
            alert('未检测到可用的物理打印机端口！请先确认打印机已插紧 USB 数据线。');
            return;
        }

        let defaultName = drvName.replace(/[^a-zA-Z0-9]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
        if (defaultName.length > 25) defaultName = defaultName.substring(0, 25);

        const pName = prompt(`确定使用驱动【${drvName}】安装打印机吗？\n请输入打印机名称:`, defaultName || "HP_Printer");
        if (!pName) return;

        const fd = new FormData();
        fd.append('name', pName);
        fd.append('uri', selectedUri);
        fd.append('driver', drvId);

        fetch('/api/printer_admin', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(res => {
                alert(AppConfig.getMsg(res, '打印机已成功安装并同步至 631！'));
                this.loadPrinters(true);
            });
    },

    resetUsb() {
        const fd = new FormData();
        fd.append('action', 'reset_usb');
        fetch('/api/printer_admin', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(d => {
                alert(AppConfig.getMsg(d, 'USB 通信与设备锁已成功复位！'));
                this.loadPrinters(true);
            });
    },

    toggleShare(printer, isShared) {
        const fd = new FormData();
        fd.append('action', 'toggle_share');
        fd.append('printer', printer);
        fd.append('shared', isShared ? 'true' : 'false');
        fetch('/api/printer_admin', { method: 'POST', body: fd });
    },

    setDefault(printer) {
        const fd = new FormData();
        fd.append('action', 'set_default');
        fd.append('printer', printer);
        fetch('/api/printer_admin', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(d => {
                alert(AppConfig.getMsg(d, '默认打印机设置成功！'));
                this.loadPrinters(true);
            });
    },

    uploadPpd() {
        const fileInput = document.getElementById('ppd-file-input');
        const alertBox = document.getElementById('ppd-alert');
        if (!fileInput.files || !fileInput.files[0]) {
            alert('请先选取 .ppd 格式驱动文件！');
            return;
        }
        const fd = new FormData();
        fd.append('action', 'upload_ppd');
        fd.append('ppd_file', fileInput.files[0]);

        fetch('/api/printer_admin', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(d => {
                alertBox.style.display = 'block';
                const isSuccess = d.success === true;
                alertBox.className = 'status-alert ' + (isSuccess ? 'status-success' : 'status-error');
                alertBox.innerText = AppConfig.getMsg(d, '驱动安装成功！');
                if (isSuccess) fileInput.value = '';
            });
    },

    uploadPlugin() {
        const fileInput = document.getElementById('plugin-file-input');
        const alertBox = document.getElementById('plugin-alert');
        if (!fileInput.files || !fileInput.files[0]) {
            alert('请先选取官方插件包文件！');
            return;
        }
        const fd = new FormData();
        fd.append('action', 'upload_scanner_plugin');
        fd.append('plugin_file', fileInput.files[0]);

        alertBox.style.display = 'block';
        alertBox.className = 'status-alert status-success';
        alertBox.innerText = '⏳ 正在上传并解压注册 arm 专有动态库...';

        fetch('/api/printer_admin', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(d => {
                const isSuccess = d.success === true;
                alertBox.className = 'status-alert ' + (isSuccess ? 'status-success' : 'status-error');
                alertBox.innerText = AppConfig.getMsg(d, '扫描插件安装成功！');
                if (isSuccess) {
                    fileInput.value = '';
                    ScanService.loadDevices();
                }
            });
    },

    cleanDisk() {
        const fd = new FormData();
        fd.append('action', 'clean_disk');
        fetch('/api/printer_admin', { method: 'POST', body: fd })
            .then(r => r.json())
            .then(d => {
                alert(AppConfig.getMsg(d, '临时打印缓存已成功清理！'));
                this.loadPrinters(true);
            });
    },

    testPage(p) {
        fetch('/api/printer_admin?action=test_page&printer=' + encodeURIComponent(p), { method: 'POST' })
            .then(r => r.json())
            .then(d => alert(AppConfig.getMsg(d, '测试页已发送至打印机！')));
    },

    cleanJobs() {
        fetch('/api/printer_admin?action=cancel_all', { method: 'POST' })
            .then(r => r.json())
            .then(d => {
                alert(AppConfig.getMsg(d, '已清空打印队列！'));
                this.loadPrinters(true);
            });
    }
};

function switchNavTab(tab) {
    AppConfig.currentNav = tab;
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel-container').forEach(p => p.classList.remove('active'));
    event.currentTarget.classList.add('active');
    document.getElementById('panel-' + tab).classList.add('active');

    if (tab === 'scan') ScanService.loadDevices();
    if (tab === 'devices') {
        DashboardService.loadPrinters(true);
        DashboardService.loadDiscoveredDevices();
        if (window.DocAI) {
            DocAI.updateDashboardMetrics();
            DocAI.renderLogs();
        }
    }
}

// 拖拽监听
['drag-drop-zone-normal', 'drag-drop-zone-invoice'].forEach(id => {
    const zone = document.getElementById(id);
    if (!zone) return;
    const isNormal = id.includes('normal');

    ['dragenter', 'dragover'].forEach(name => {
        zone.addEventListener(name, (e) => { e.preventDefault(); zone.classList.add('drag-over'); }, false);
    });

    ['dragleave', 'drop'].forEach(name => {
        zone.addEventListener(name, (e) => { e.preventDefault(); zone.classList.remove('drag-over'); }, false);
    });

    zone.addEventListener('drop', (e) => {
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            PrintManager.onFilesSelected(isNormal ? 'normal' : 'invoice', e.dataTransfer.files);
        }
    }, false);
});

// 云邮箱配置监听
const mailForm = document.getElementById('mail-cfg-form');
if (mailForm) {
    mailForm.onsubmit = function(e) {
        e.preventDefault();
        const btn = this.querySelector('button[type="submit"]');
        const alertBox = document.getElementById('mail-alert');
        btn.disabled = true;
        btn.innerText = '正在保存配置...';

        const payload = {
            enable: document.getElementById('mail_enable').value === 'true',
            server: document.getElementById('mail_server').value.trim(),
            port: parseInt(document.getElementById('mail_port').value) || 993,
            user: document.getElementById('mail_user').value.trim(),
            password: document.getElementById('mail_password').value.trim(),
            pushplus_token: document.getElementById('mail_pushplus_token').value.trim(),
            keyword: document.getElementById('mail_keyword').value.trim(),
            whitelist: document.getElementById('mail_whitelist').value.trim()
        };

        const printerSelect = document.getElementById('printer-select');
        if (printerSelect && printerSelect.value) payload.default_printer = printerSelect.value;

        fetch('/api/mail_config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(r => r.json())
        .then(d => {
            btn.disabled = false;
            btn.innerText = '💾 保存云邮件配置并立即生效';
            alertBox.style.display = 'block';
            alertBox.className = 'status-alert ' + (d.success ? 'status-success' : 'status-error');
            alertBox.innerText = AppConfig.getMsg(d, '云邮箱及微信通知设置已成功保存！');
        })
        .catch(err => {
            btn.disabled = false;
            btn.innerText = '💾 保存云邮件配置并立即生效';
            alertBox.style.display = 'block';
            alertBox.className = 'status-alert status-error';
            alertBox.innerText = '请求失败: ' + err;
        });
    };
}

// 智能可见性轮询管理器
let pollTimer = null;
function startSmartPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => {
        if (!document.hidden && (AppConfig.currentNav === 'print' || AppConfig.currentNav === 'devices')) {
            DashboardService.loadPrinters(false);
        }
    }, 4000);
}

document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    } else {
        startSmartPolling();
    }
});

// 页面加载启动
document.addEventListener("DOMContentLoaded", () => {
    IdCardManager.init();
    EnhanceEngine.initViewerInteraction();
    if (window.DocAI) DocAI.init();
    DashboardService.loadPrinters(false);
    startSmartPolling();
});
