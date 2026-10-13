/**
 * 端侧 ONNX 运算中枢 (移动端单线程与无 SIMD 兼容版)
 */

window.DocAI = {
    session: null,
    readyStatus: { "corner": false, "dewarp": false },

    init: async function() {
        logToConsole("端侧 AI 诊断守护启动...");
        try {
            if (typeof ort === 'undefined') {
                logToConsole("未检测到 onnxruntime-web 运行时，转用服务端通道", "warn");
                this.updateUIStatus("未挂载", "无", "无", "-");
                return;
            }

            ort.env.wasm.numThreads = 1;
            ort.env.wasm.simd = false;

            const sessionOptions = {
                executionProviders: ['wasm'],
                graphOptimizationLevel: 'all'
            };

            const t0 = performance.now();
            logToConsole("正在挂载本地默认模型 /models/doc_corner.onnx...");

            try {
                this.session = await ort.InferenceSession.create("/models/doc_corner.onnx", sessionOptions);
                const cost = (performance.now() - t0).toFixed(1);
                this.readyStatus["corner"] = true;
                this.updateUIStatus("WASM (兼容模式)", "持久化挂载", "doc_corner.onnx", `${cost}ms`);
                logToConsole(`✔ 端侧角点定位引擎装载完成 (耗时: ${cost}ms)`, "success");

                const pill = document.getElementById("ai-engine-pill");
                if (pill) {
                    pill.innerText = "⚡ 端侧 AI 就绪 (WASM 兼容模式)";
                    pill.style.background = "#dcfce7";
                    pill.style.color = "#166534";
                }
            } catch (loadErr) {
                logToConsole(`本地模型未挂载或算子不兼容: ${loadErr.message}`, "warn");
                this.updateUIStatus("主机端代理", "宿主机计算", "服务端滤镜", "0ms");

                const pill = document.getElementById("ai-engine-pill");
                if (pill) {
                    pill.innerText = "☁️ 主机端代理 (轻量模式)";
                    pill.style.background = "#f1f5f9";
                    pill.style.color = "#475569";
                }
            }

        } catch (e) {
            logToConsole(`端侧环境初始化跳过: ${e.message}`, "warn");
            this.updateUIStatus("主机端代理", "自动降级", "系统级增强", "0ms");
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

    inspectAndUploadModel: async function(inputId) {
        const fileInput = document.getElementById(inputId);
        const alertBox = document.getElementById("model-alert");
        if (!fileInput || fileInput.files.length === 0) {
            if (alertBox) alertBox.innerHTML = '<span style="color:#ef4444;">请选取 .onnx 文件</span>';
            return;
        }

        const file = fileInput.files[0];
        logToConsole(`正在深度解析 ${file.name} 的模型拓扑结构...`);
        const fd = new FormData();
        fd.append("model_file", file);

        try {
            const resp = await fetch("/api/models", { method: "POST", body: fd });
            const res = await resp.json();
            if (res.success) {
                logToConsole(`✔ ${res.msg}`, "success");
                if (alertBox) alertBox.innerHTML = `<span style="color:#10b981;">✔ ${res.msg}</span>`;
                this.init();
                this.fetchDeployedModels();
            } else {
                logToConsole(`模型部署拒绝: ${res.msg}`, "error");
                if (alertBox) alertBox.innerHTML = `<span style="color:#ef4444;">${res.msg}</span>`;
            }
        } catch (e) {
            logToConsole(`模型写入异常: ${e.message}`, "error");
            if (alertBox) alertBox.innerHTML = `<span style="color:#ef4444;">传输异常: ${e.message}</span>`;
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
                    <button type="button" class="btn-text-danger" onclick="DocAI.removeModel('${m.name}')">移除</button>
                </div>
            `).join('');
        } catch (e) {
            box.innerHTML = '<div style="font-size:11px; color:#ef4444;">获取已部署模型失败</div>';
        }
    },

    removeModel: async function(name) {
        if (!confirm(`确认彻底移除模型 ${name} 吗？`)) return;
        try {
            const resp = await fetch(`/api/models?name=${encodeURIComponent(name)}`, { method: "DELETE" });
            const res = await resp.json();
            logToConsole(res.msg, res.success ? "success" : "error");
            this.fetchDeployedModels();
        } catch (e) {
            logToConsole(`移除异常: ${e.message}`, "error");
        }
    },

    clearCacheAndReload: function() {
        logToConsole("浏览器端模型缓存与自定义参数已清空...");
        if ('caches' in window) {
            caches.keys().then(names => names.forEach(name => caches.delete(name)));
        }
        location.reload();
    }
};

document.addEventListener("DOMContentLoaded", () => {
    window.DocAI.init();
});
