/**
 * client_ai.js - 端侧通用 AI 多模型推理管线 + 状态诊断与 WebGL2 硬件加速引擎
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
    },

    async clearAll() {
        const db = await this.openDB();
        if (!db) return;
        const tx = db.transaction(this.storeName, "readwrite");
        tx.objectStore(this.storeName).clear();
    }
};

class DocumentAIEngine {
    constructor() {
        this.sessions = {};
        this.readyStatus = {};
        this.currentBackend = "未初始化";
        this.cacheSource = "无";
        this.lastInferTime = 0;
        this.activeModelName = "无";
        this.logs = [];
    }

    log(msg, type = "info") {
        const now = new Date();
        const timeStr = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
        const item = { time: timeStr, msg, type };
        this.logs.unshift(item);
        if (this.logs.length > 50) this.logs.pop();
        this.renderLogs();
    }

    renderLogs() {
        const logBox = document.getElementById("ai-diag-console");
        if (!logBox) return;
        logBox.innerHTML = this.logs.map(l => {
            let color = "#64748b";
            if (l.type === "success") color = "#16a34a";
            if (l.type === "warn") color = "#d97706";
            if (l.type === "error") color = "#dc2626";
            return `<div style="margin-bottom:3px; line-height:1.4;"><span style="color:#94a3b8;">[${l.time}]</span> <span style="color:${color}; font-weight:600;">${l.msg}</span></div>`;
        }).join("");
    }

    updateDashboardMetrics() {
        const elBackend = document.getElementById("diag-metric-backend");
        const elCache = document.getElementById("diag-metric-cache");
        const elModel = document.getElementById("diag-metric-model");
        const elSpeed = document.getElementById("diag-metric-speed");
        const elPill = document.getElementById("ai-engine-pill");

        if (elBackend) elBackend.innerText = this.currentBackend;
        if (elCache) elCache.innerText = this.cacheSource;
        if (elModel) elModel.innerText = this.activeModelName;
        if (elSpeed) elSpeed.innerText = this.lastInferTime > 0 ? `${this.lastInferTime} ms` : "-";

        if (elPill) {
            const isOk = !!this.readyStatus["corner"];
            elPill.style.background = isOk ? "#dcfce7" : "#e0f2fe";
            elPill.style.color = isOk ? "#15803d" : "#0369a1";
            elPill.innerText = isOk ? `⚡ 端侧 AI 就绪 (${this.currentBackend})` : "☁ 云端轻量加速";
        }
    }

    async loadModel(modelKey, filename) {
        if (!window.ort) {
            this.log("未检测到 onnxruntime-web 引擎库，降级至盒子端处理", "warn");
            return null;
        }

        this.log(`正在检查模型: ${filename}...`, "info");
        try {
            let buf = await ClientModelCache.get(modelKey);
            if (buf) {
                this.cacheSource = "IndexedDB (秒开)";
                this.log(`[成功] 命中浏览器本地 IndexedDB 缓存 (${(buf.byteLength / 1024 / 1024).toFixed(2)} MB)`, "success");
            } else {
                this.cacheSource = "网络下载";
                this.log(`[下载] 本地无缓存，正在请求 /models/${filename}...`, "info");
                const resp = await fetch(`/models/${filename}`);
                if (!resp.ok) {
                    throw new Error(`HTTP ${resp.status} - 服务端 /models/ 目录未找到该模型`);
                }
                buf = await resp.arrayBuffer();
                await ClientModelCache.set(modelKey, buf);
                this.log(`[成功] 模型已下载并永久存入 IndexedDB 数据库`, "success");
            }

            // WebGL2 硬件配置：启用浮点纹理打包
            const sessionOptions = {
                executionProviders: [
                    {
                        name: 'webgl',
                        contextId: 'webgl2',
                        pack: true
                    },
                    'wasm'
                ],
                graphOptimizationLevel: 'all'
            };

            this.log("正在初始化 WebGL2 硬件执行通道...", "info");
            let session = null;
            try {
                session = await ort.InferenceSession.create(buf, sessionOptions);
                this.currentBackend = "WebGL (GPU 加速)";
                this.log("[就绪] WebGL 硬件加速激活成功，当前在设备显卡上全速运算", "success");
            } catch (gpuErr) {
                this.log(`[降级] WebGL 初始化受限 (${gpuErr.message})，转为 WASM (CPU 多线程)`, "warn");
                session = await ort.InferenceSession.create(buf, { executionProviders: ["wasm"] });
                this.currentBackend = "WASM (CPU 多线程)";
            }

            this.sessions[modelKey] = session;
            this.readyStatus[modelKey] = true;
            this.activeModelName = filename;
            this.updateDashboardMetrics();
            return session;
        } catch (e) {
            this.readyStatus[modelKey] = false;
            this.currentBackend = "降级至服务端";
            this.log(`[故障] 模型载入失败: ${e.message}`, "error");
            this.log("排查建议: 1. 检查 /opt/webapp/models/ 文件状态; 2. 上传剔除动态量化的原生模型覆盖", "warn");
            this.updateDashboardMetrics();
            return null;
        }
    }

    async init() {
        if (!window.ort) return false;
        ort.env.wasm.numThreads = 2;
        this.log("端侧 AI 诊断守护启动...", "info");
        const ok = await this.loadModel("corner", "doc_corner.onnx");
        return !!ok;
    }

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

    async processDocument(file, options = { pipeline: ["corner"] }) {
        if (!this.readyStatus["corner"]) return null;
        const t0 = performance.now();
        let objectUrl = null;
        let inputTensor = null;

        try {
            const img = await new Promise((res, rej) => {
                const i = new Image();
                objectUrl = URL.createObjectURL(file);
                i.onload = () => {
                    // 图片加载完成后立即解绑 URL，防止 iOS Safari 显存常驻泄露
                    if (objectUrl) {
                        URL.revokeObjectURL(objectUrl);
                        objectUrl = null;
                    }
                    res(i);
                };
                i.onerror = (e) => {
                    if (objectUrl) {
                        URL.revokeObjectURL(objectUrl);
                        objectUrl = null;
                    }
                    rej(e);
                };
                i.src = objectUrl;
            });

            let curCanvas = this.safeResize(img, 2048);

            if (options.pipeline.includes("corner") && this.sessions["corner"]) {
                const w = curCanvas.width, h = curCanvas.height;
                const netCanvas = document.createElement("canvas");
                netCanvas.width = 256; netCanvas.height = 256;
                const nCtx = netCanvas.getContext("2d");
                nCtx.drawImage(curCanvas, 0, 0, 256, 256);
                const data = nCtx.getImageData(0, 0, 256, 256).data;

                const floatArr = new Float32Array(3 * 256 * 256);
                for (let i = 0; i < 256 * 256; i++) {
                    floatArr[i] = data[i * 4] / 255.0;
                    floatArr[256 * 256 + i] = data[i * 4 + 1] / 255.0;
                    floatArr[2 * 256 * 256 + i] = data[i * 4 + 2] / 255.0;
                }

                inputTensor = new ort.Tensor("float32", floatArr, [1, 3, 256, 256]);
                const results = await this.sessions["corner"].run({ input: inputTensor });
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

                // 释放网络输入画布
                netCanvas.width = 1; netCanvas.height = 1;

                if (cnt > (256 * 256 * 0.20)) {
                    const sx = w / 256.0, sy = h / 256.0;
                    const cropX = Math.max(0, Math.floor(minX * sx));
                    const cropY = Math.max(0, Math.floor(minY * sy));
                    const cropW = Math.min(w - cropX, Math.ceil((maxX - minX) * sx));
                    const cropH = Math.min(h - cropY, Math.ceil((maxY - minY) * sy));

                    const outCanvas = document.createElement("canvas");
                    outCanvas.width = cropW; outCanvas.height = cropH;
                    const outCtx = outCanvas.getContext("2d");
                    outCtx.drawImage(curCanvas, cropX, cropY, cropW, cropH, 0, 0, cropW, cropH);
                    
                    curCanvas.width = 1; curCanvas.height = 1;
                    curCanvas = outCanvas;
                }
            }

            this.lastInferTime = Math.round(performance.now() - t0);
            this.log(`图像推理与切边完成，耗时: ${this.lastInferTime} ms`, "success");
            this.updateDashboardMetrics();

            const blobResult = await new Promise((res) => curCanvas.toBlob(res, "image/jpeg", 0.94));
            curCanvas.width = 1; curCanvas.height = 1; // 彻底释放离屏像素内存
            return blobResult;
        } catch (err) {
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            this.log(`推理执行异常: ${err.message}，自动回退原图`, "warn");
            return null;
        }
    }

    async clearCacheAndReload() {
        this.log("正在清空浏览器 IndexedDB 数据库中的模型缓存...", "info");
        await ClientModelCache.clearAll();
        this.readyStatus = {};
        this.sessions = {};
        this.log("缓存已清空，重新拉取最新模型...", "info");
        await this.init();
    }

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
        alertBox.innerText = `⏳ 正在上传模型 ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)...`;
        this.log(`正在上传新模型文件: ${file.name}`, "info");

        try {
            const resp = await fetch("/api/model/upload", { method: "POST", body: fd });
            const d = await resp.json();
            if (d.success) {
                alertBox.innerText = "✅ " + d.msg;
                this.log(`服务端保存成功: ${d.msg}`, "success");
                fileInput.value = "";
                await this.clearCacheAndReload();
            } else {
                alertBox.className = "status-alert status-error";
                alertBox.innerText = "❌ 上传失败: " + d.msg;
                this.log(`上传被拦截: ${d.msg}`, "error");
            }
        } catch (e) {
            alertBox.className = "status-alert status-error";
            alertBox.innerText = "❌ 请求异常: " + e;
            this.log(`网络上传错误: ${e}`, "error");
        }
    }
}

window.DocAI = new DocumentAIEngine();
