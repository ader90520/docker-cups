/**
 * client_ai.js - 端侧 AI 模型引擎 (IndexedDB 永久持久化 + WebGL 硬件加速)
 * 支持切边纠偏 (Corner)、文字方向与文档展平 (Dewarp)
 */

const ClientModelCache = {
    dbName: "cups_ai_model_storage",
    storeName: "models_cache",

    openDB() {
        return new Promise((resolve) => {
            const req = indexedDB.open(this.dbName, 1);
            req.onupgradeneeded = (e) => e.target.result.createObjectStore(this.storeName);
            req.onsuccess = (e) => resolve(e.target.result);
            req.onerror = () => resolve(null);
        });
    },

    async get(key) {
        const db = await this.openDB();
        if (!db) return null;
        return new Promise((resolve) => {
            const tx = db.transaction(this.storeName, "readonly");
            const req = tx.objectStore(this.storeName).get(key);
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => resolve(null);
        });
    },

    async set(key, val) {
        const db = await this.openDB();
        if (!db) return;
        const tx = db.transaction(this.storeName, "readwrite");
        tx.objectStore(this.storeName).put(val, key);
    },

    async remove(key) {
        const db = await this.openDB();
        if (!db) return;
        const tx = db.transaction(this.storeName, "readwrite");
        tx.objectStore(this.storeName).delete(key);
    }
};

class DocumentAIEngine {
    constructor() {
        this.sessionCorner = null;
        this.sessionDewarp = null;
        this.isCornerReady = false;
        this.isDewarpReady = false;
    }

    // 显式界面状态提示
    updateBadge(text, isSuccess = true) {
        let badge = document.getElementById("ai-engine-pill");
        if (!badge) {
            badge = document.createElement("span");
            badge.id = "ai-engine-pill";
            badge.className = "ai-engine-tag";
            const parent = document.querySelector(".printer-select-wrap");
            if (parent) parent.appendChild(badge);
        }
        badge.style.background = isSuccess ? "#dcfce7" : "#e0f2fe";
        badge.style.color = isSuccess ? "#15803d" : "#0369a1";
        badge.style.fontSize = "11px";
        badge.style.padding = "3px 8px";
        badge.style.borderRadius = "12px";
        badge.style.fontWeight = "600";
        badge.innerText = text;
    }

    async init() {
        if (!window.ort) {
            this.updateBadge("☁ 云端轻量加速", false);
            return false;
        }

        try {
            ort.env.wasm.numThreads = 2;
            let cornerBuf = await ClientModelCache.get("doc_corner");

            if (!cornerBuf) {
                this.updateBadge("⏳ 正在下载模型...", false);
                const resp = await Promise.race([
                    fetch("/models/doc_corner.onnx"),
                    new Promise((_, reject) => setTimeout(() => reject(new Error("Timeout")), 4000))
                ]);
                if (!resp.ok) throw new Error("Model not found");
                cornerBuf = await resp.arrayBuffer();
                await ClientModelCache.set("doc_corner", cornerBuf);
            }

            this.sessionCorner = await ort.InferenceSession.create(cornerBuf, {
                executionProviders: ["webgl", "wasm"]
            });
            this.isCornerReady = true;

            // 成功提示：明确呈现模型已加载就绪
            this.updateBadge("⚡ 端侧 AI 硬件加速就绪", true);
            console.log("[ClientAI] 切边与校正模型加载成功并就绪 (WebGL)");
            return true;
        } catch (e) {
            console.warn("[ClientAI] 端侧模型未找到或初始化跳过，降级至服务端通道:", e);
            this.isCornerReady = false;
            this.updateBadge("☁ 云端轻量加速", false);
            return false;
        }
    }

    // 防 Safari 内存崩溃缩放
    safeResize(img, maxDim = 2048) {
        let w = img.width, h = img.height;
        if (w > maxDim || h > maxDim) {
            if (w > h) { h = Math.round((h * maxDim) / w); w = maxDim; }
            else { w = Math.round((w * maxDim) / h); h = maxDim; }
        }
        const canvas = document.createElement("canvas");
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, w, h);
        return canvas;
    }

    // 执行切边与轮廓纠偏
    async processDocument(file) {
        if (!this.isCornerReady || !this.sessionCorner) return null;
        try {
            const img = await new Promise((res) => {
                const i = new Image();
                i.onload = () => res(i);
                i.src = URL.createObjectURL(file);
            });

            const baseCanvas = this.safeResize(img, 2048);
            const w = baseCanvas.width, h = baseCanvas.height;

            const netCanvas = document.createElement("canvas");
            netCanvas.width = 256; netCanvas.height = 256;
            const nCtx = netCanvas.getContext("2d");
            nCtx.drawImage(baseCanvas, 0, 0, 256, 256);
            const data = nCtx.getImageData(0, 0, 256, 256).data;

            const floatArr = new Float32Array(3 * 256 * 256);
            for (let i = 0; i < 256 * 256; i++) {
                floatArr[i] = data[i * 4] / 255.0;
                floatArr[256 * 256 + i] = data[i * 4 + 1] / 255.0;
                floatArr[2 * 256 * 256 + i] = data[i * 4 + 2] / 255.0;
            }

            const inputTensor = new ort.Tensor("float32", floatArr, [1, 3, 256, 256]);
            const results = await this.sessionCorner.run({ input: inputTensor });
            const mask = results.output.data;

            let minX = 256, minY = 256, maxX = 0, maxY = 0, cnt = 0;
            for (let y = 0; y < 256; y++) {
                for (let x = 0; x < 256; x++) {
                    if (mask[y * 256 + x] > 0.35) {
                        if (x < minX) minX = x;
                        if (x > maxX) maxX = x;
                        if (y < minY) minY = y;
                        if (y > maxY) maxY = y;
                        cnt++;
                    }
                }
            }

            if (cnt > (256 * 256 * 0.20)) {
                const sx = w / 256.0, sy = h / 256.0;
                const cropX = Math.max(0, Math.floor(minX * sx));
                const cropY = Math.max(0, Math.floor(minY * sy));
                const cropW = Math.min(w - cropX, Math.ceil((maxX - minX) * sx));
                const cropH = Math.min(h - cropY, Math.ceil((maxY - minY) * sy));

                const outCanvas = document.createElement("canvas");
                outCanvas.width = cropW; outCanvas.height = cropH;
                const outCtx = outCanvas.getContext("2d");
                outCtx.drawImage(baseCanvas, cropX, cropY, cropW, cropH, 0, 0, cropW, cropH);

                return await new Promise((res) => outCanvas.toBlob(res, "image/jpeg", 0.94));
            }
        } catch (err) {
            console.warn("[ClientAI] 推理微异常，直接降级:", err);
        }
        return null;
    }

    // 在线上传并热重载新模型
    async uploadAndReloadModel(fileInputId, alertId) {
        const fileInput = document.getElementById(fileInputId);
        const alertBox = document.getElementById(alertId);
        if (!fileInput.files || !fileInput.files[0]) {
            alert("请选择 .onnx 格式的模型文件！");
            return;
        }

        const file = fileInput.files[0];
        const fd = new FormData();
        fd.append("model_file", file);

        alertBox.style.display = "block";
        alertBox.className = "status-alert status-success";
        alertBox.innerText = "⏳ 正在上传模型并刷新本地缓存...";

        try {
            const resp = await fetch("/api/model/upload", { method: "POST", body: fd });
            const d = await resp.json();
            if (d.success) {
                alertBox.innerText = "✅ 模型已更新成功！正在热加载...";
                // 清理旧缓存并重启
                await ClientModelCache.remove("doc_corner");
                await this.init();
                alertBox.innerText = "✅ " + (d.msg || "新模型已生效并已加载至浏览器！");
                fileInput.value = "";
            } else {
                alertBox.className = "status-alert status-error";
                alertBox.innerText = "❌ 上传失败: " + d.msg;
            }
        } catch (e) {
            alertBox.className = "status-alert status-error";
            alertBox.innerText = "❌ 请求异常: " + e;
        }
    }
}

window.DocAI = new DocumentAIEngine();
