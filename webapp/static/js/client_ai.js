/**
 * 客户端端侧大模型文档切边引擎 (IndexedDB 缓存 + WebGL 硬件加速)
 */
const ModelCacheManager = {
    dbName: "cups_ai_cache_v1",
    storeName: "models",

    getDB() {
        return new Promise((resolve) => {
            const req = indexedDB.open(this.dbName, 1);
            req.onupgradeneeded = (e) => e.target.result.createObjectStore(this.storeName);
            req.onsuccess = (e) => resolve(e.target.result);
            req.onerror = () => resolve(null);
        });
    },

    async getModel(key) {
        const db = await this.getDB();
        if (!db) return null;
        return new Promise((resolve) => {
            const tx = db.transaction(this.storeName, "readonly");
            const req = tx.objectStore(this.storeName).get(key);
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => resolve(null);
        });
    },

    async saveModel(key, buffer) {
        const db = await this.getDB();
        if (!db) return;
        const tx = db.transaction(this.storeName, "readwrite");
        tx.objectStore(this.storeName).put(buffer, key);
    }
};

class ClientDocAI {
    constructor() {
        this.session = null;
        this.isReady = false;
    }

    async init() {
        if (!window.ort) return false;
        try {
            ort.env.wasm.numThreads = 2;
            let buffer = await ModelCacheManager.getModel("doc_corner");
            if (!buffer) {
                console.log("[ClientDocAI] 本地无缓存，正在下载 doc_corner.onnx...");
                const resp = await fetch("/models/doc_corner.onnx");
                if (!resp.ok) throw new Error("Fetch failed");
                buffer = await resp.arrayBuffer();
                await ModelCacheManager.saveModel("doc_corner", buffer);
            }

            this.session = await ort.InferenceSession.create(buffer, {
                executionProviders: ["webgl", "wasm"]
            });
            this.isReady = true;
            console.log("[ClientDocAI] 客户端大模型就绪 (WebGL 加速已激活)");
            return true;
        } catch (e) {
            console.warn("[ClientDocAI] 初始化失败，自动降级至服务端通道:", e);
            this.isReady = false;
            return false;
        }
    }

    // 内存安全缩放：避免 iOS Safari 因图片像素过大内存溢出闪退
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

    async processImage(imgElement) {
        if (!this.isReady || !this.session) return null;
        try {
            const baseCanvas = this.safeResize(imgElement, 2048);
            const w = baseCanvas.width, h = baseCanvas.height;

            // 归一化输入至 256x256
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
            const results = await this.session.run({ input: inputTensor });
            const mask = results.output.data;

            // 定位版芯边界
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

                return await new Promise((res) => outCanvas.toBlob(res, "image/jpeg", 0.92));
            }
        } catch (err) {
            console.warn("[ClientDocAI] 端侧推理异常，降级使用原图:", err);
        }
        return null;
    }
}

window.clientDocAI = new ClientDocAI();
