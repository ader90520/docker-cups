/**
 * 修复 IdCardManager 中的预览排版逻辑
 */
const IdCardManager = {
    persons: [
        { front: null, back: null, rot_f: 0, rot_b: 0, zoom_f: 1.0, zoom_b: 1.0 }
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
        if (file) {
            upImg.src = URL.createObjectURL(file);
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
        img.onload = () => {
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
        img.src = URL.createObjectURL(file);
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
        this.persons.push({ front: null, back: null, rot_f: 0, rot_b: 0, zoom_f: 1.0, zoom_b: 1.0 });
        this.rebuildDOM();
        this.renderPreviewGrid();
    },

    removePerson(idx) {
        if (this.persons.length <= 1) {
            this.clearPerson(0);
            return;
        }
        this.persons.splice(idx, 1);
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
                    <b style="font-size:12px; color:#1e293b;">人员 ${idx + 1} 身份证:</b>
                    <div>
                        <button type="button" class="btn-del" style="font-size:11px; margin-right:6px;" onclick="IdCardManager.clearPerson(${idx})">清空</button>
                        ${idx > 0 ? `<button type="button" class="btn-del" style="font-size:11px; font-weight:700;" onclick="IdCardManager.removePerson(${idx})">🗑️ 删除</button>` : ''}
                    </div>
                </div>
                <div class="idcard-grid">
                    <div class="idcard-box-wrapper">
                        <div class="idcard-box" onclick="IdCardManager.pick(${idx}, 'f')">
                            <span class="text-tip" id="lbl-f-${idx}" style="${p.front ? 'display:none;' : ''}">点击上传：正面 (人像)</span>
                            <img id="img-f-${idx}" src="${p.front ? URL.createObjectURL(p.front) : ''}" style="${p.front ? 'display:block;' : 'display:none;'}; transform:scale(${p.zoom_f});" alt="正面">
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
                            <img id="img-b-${idx}" src="${p.back ? URL.createObjectURL(p.back) : ''}" style="${p.back ? 'display:block;' : 'display:none;'}; transform:scale(${p.zoom_b});" alt="反面">
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

    // 核心渲染：保证无论是否选图，始终呈现清晰标准槽位与纸张
    renderPreviewGrid() {
        const holder = document.getElementById('viewport-idcard-holder');
        const tip = document.getElementById('viewport-empty-tip');
        const canvas = document.getElementById('canvas-preview');
        const vp = document.getElementById('a4-viewport');
        if (!holder || !vp) return;

        if (canvas) canvas.style.display = 'none';
        if (tip) tip.style.display = 'none';
        holder.innerHTML = '';
        holder.style.display = 'block';

        // 锁定 A4 纯白比例
        vp.style.width = '320px';
        vp.style.height = '452px';

        const count = this.persons.length;

        if (count === 1) {
            const p = this.persons[0];
            const div = document.createElement('div');
            div.className = 'idcard-layout-single';
            div.innerHTML = `
                <div class="idcard-slot-box ${p.front ? 'has-img' : ''}">
                    ${p.front ? `<img src="${URL.createObjectURL(p.front)}" style="display:block; transform:scale(${p.zoom_f});">` : `<div class="idcard-slot-label">🪪 正面 (人像面)<br><span style="font-size:9px; color:#94a3b8;">标准摆放槽位</span></div>`}
                </div>
                <div class="idcard-slot-box ${p.back ? 'has-img' : ''}">
                    ${p.back ? `<img src="${URL.createObjectURL(p.back)}" style="display:block; transform:scale(${p.zoom_b});">` : `<div class="idcard-slot-label">🪪 反面 (国徽面)<br><span style="font-size:9px; color:#94a3b8;">标准摆放槽位</span></div>`}
                </div>
            `;
            holder.appendChild(div);
        } else {
            const div = document.createElement('div');
            div.className = 'idcard-layout-multi';
            this.persons.forEach((p, i) => {
                const row = document.createElement('div');
                row.className = 'idcard-multi-row';
                row.innerHTML = `
                    <div class="idcard-slot-box mini ${p.front ? 'has-img' : ''}">
                        ${p.front ? `<img src="${URL.createObjectURL(p.front)}" style="display:block; transform:scale(${p.zoom_f});">` : `<div class="idcard-slot-label">人${i+1} 正面</div>`}
                    </div>
                    <div class="idcard-slot-box mini ${p.back ? 'has-img' : ''}">
                        ${p.back ? `<img src="${URL.createObjectURL(p.back)}" style="display:block; transform:scale(${p.zoom_b});">` : `<div class="idcard-slot-label">人${i+1} 反面</div>`}
                    </div>
                `;
                div.appendChild(row);
            });
            holder.appendChild(div);
        }
    }
};
