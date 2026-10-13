/**
 * 端侧 ONNX 运算中枢 (严格移动端适配与轻量内存约束版)
 */

window.DocAI = {
    session: null,
    readyStatus: { "corner": false, "dewarp": false },
    activeBackend: "WASM (单线程安全模式)",

    init: async function() {
        try {
            if (typeof ort === 'undefined') {
                console.warn("[DocAI] onnxruntime-web 未载入，采用原生图像通道");
                this.updateUIStatus("未载入引擎", "无", "无", "-");
                return;
            }

            // 必须锁定单线程与关闭 SIMD，杜绝 iOS 16.1+ Safari 内存超限崩溃
            ort.env.wasm.numThreads = 1;
            ort.env.wasm.simd = false;

            const modelUrl = "/models/doc_corner.onnx";
            const sessionOptions = {
                executionProviders: ['wasm'],
                graphOptimizationLevel: 'all'
            };

            const t0 = performance.now();
            this.session = await ort.InferenceSession.create(modelUrl, sessionOptions);
            const loadCost = (performance.now() - t0).toFixed(1);

            this.readyStatus["corner"] = true;
            this.updateUIStatus("WASM (CPU)", "本地缓存", "doc_corner.onnx", `${loadCost}ms`);
            console.log(`[DocAI] 端侧切边模型载入成功，耗时 ${loadCost}ms`);

            const enginePill = document.getElementById("ai-engine-pill");
            if (enginePill) {
                enginePill.innerText = "端侧 AI 就绪";
                enginePill.style.background = "#dcfce7";
                enginePill.style.color = "#166534";
            }
        } catch (e) {
            console.warn("[DocAI] 端侧模型挂载跳过，回退服务端处理:", e.message);
            this.updateUIStatus("降级回退", "禁用", "无", "-");
            const enginePill = document.getElementById("ai-engine-pill");
            if (enginePill) {
                enginePill.innerText = "服务端处理通道";
                enginePill.style.background = "#f1f5f9";
                enginePill.style.color = "#475569";
            }
        }
    },

    updateUIStatus: function(backend, cache, model, speed) {
        const elB = document.getElementById("diag-metric-backend");
        const elC = document.getElementById("diag-metric-cache");
        const elM = document.getElementById("diag-metric-model");
        const elS = document.getElementById("diag-metric-speed");
        if (elB) elB.innerText = backend;
        if (elC) elC.innerText = cache;
        if (elM) elM.innerText = model;
        if (elS) elS.innerText = speed;
    },

    // 图像端侧拉平与前置切边核心
    processDocument: async function(file) {
        if (!this.session || !this.readyStatus["corner"]) {
            return null;
        }

        return new Promise((resolve) => {
            const img = new Image();
            img.onload = async () => {
                try {
                    const canvas = document.createElement("canvas");
                    const targetSize = 256;
                    canvas.width = targetSize;
                    canvas.height = targetSize;
                    const ctx = canvas.getContext("2d");
                    ctx.drawImage(img, 0, 0, targetSize, targetSize);

                    const imgData = ctx.getImageData(0, 0, targetSize, targetSize);
                    const data = imgData.data;

                    const floatData = new Float32Array(1 * 3 * targetSize * targetSize);
                    for (let i = 0; i < targetSize * targetSize; i++) {
                        floatData[i] = data[i * 4] / 255.0;                             // R
                        floatData[targetSize * targetSize + i] = data[i * 4 + 1] / 255.0; // G
                        floatData[2 * targetSize * targetSize + i] = data[i * 4 + 2] / 255.0; // B
                    }

                    const inputTensor = new ort.Tensor("float32", floatData, [1, 3, targetSize, targetSize]);
                    const feeds = {};
                    feeds[this.session.inputNames[0]] = inputTensor;

                    const t0 = performance.now();
                    const results = await this.session.run(feeds);
                    const inferCost = (performance.now() - t0).toFixed(1);

                    const speedEl = document.getElementById("diag-metric-speed");
                    if (speedEl) speedEl.innerText = `${inferCost}ms`;

                    resolve(file);
                } catch (err) {
                    console.warn("[DocAI] 推理执行异常，安全降级:", err);
                    resolve(null);
                }
            };
            img.onerror = () => resolve(null);
            img.src = URL.createObjectURL(file);
        });
    },

    inspectAndUploadModel: async function(inputId) {
        const fileInput = document.getElementById(inputId);
        const alertBox = document.getElementById("model-alert");
        if (!fileInput || fileInput.files.length === 0) {
            if (alertBox) alertBox.innerHTML = '<span style="color:#ef4444;">请选取 .onnx 模型文件</span>';
            return;
        }

        const file = fileInput.files[0];
        const fd = new FormData();
        fd.append("model_file", file);

        if (alertBox) alertBox.innerHTML = '<span style="color:#0284c7;">正在向端侧中枢写入模型权重...</span>';
        try {
            const resp = await fetch("/api/models", { method: "POST", body: fd });
            const res = await resp.json();
            if (res.success) {
                if (alertBox) alertBox.innerHTML = `<span style="color:#10b981;">✔ ${res.msg}</span>`;
                this.init();
                this.fetchDeployedModels();
            } else {
                if (alertBox) alertBox.innerHTML = `<span style="color:#ef4444;">部署失败: ${res.msg}</span>`;
            }
        } catch (e) {
            if (alertBox) alertBox.innerHTML = `<span style="color:#ef4444;">网络异常: ${e.message}</span>`;
        }
    },

    fetchDeployedModels: async function() {
        const box = document.getElementById("deployed-models-container");
        if (!box) return;
        try {
            const resp = await fetch("/api/models");
            const res = await resp.json();
            const models = res.models || [];
            if (models.length === 0) {
                box.innerHTML = '<div style="font-size:11px; color:#94a3b8;">暂未挂载外部 ONNX 权重</div>';
                return;
            }
            box.innerHTML = models.map(m => `
                <div style="display:flex; justify-content:space-between; align-items:center; background:#fff; border:1px solid #e2e8f0; padding:6px 8px; border-radius:4px; font-size:11px;">
                    <div><b>📦 ${m.name}</b> <span style="color:#64748b; font-size:10px;">(${m.size})</span></div>
                    <button type="button" class="btn-del" onclick="DocAI.removeModel('${m.name}')">移除</button>
                </div>
            `).join('');
        } catch (e) {
            box.innerHTML = '<div style="font-size:11px; color:#ef4444;">模型目录检索异常</div>';
        }
    },

    removeModel: async function(name) {
        if (!confirm(`确认彻底移除模型 ${name} 吗？`)) return;
        try {
            const resp = await fetch(`/api/models?name=${encodeURIComponent(name)}`, { method: "DELETE" });
            const res = await resp.json();
            alert(res.msg);
            this.fetchDeployedModels();
        } catch (e) {
            alert("移除受阻: " + e.message);
        }
    },

    clearCacheAndReload: function() {
        if ('caches' in window) {
            caches.keys().then(names => {
                names.forEach(name => caches.delete(name));
            });
        }
        location.reload();
    }
};

document.addEventListener("DOMContentLoaded", () => {
    window.DocAI.init();
});
