/**
 * 端侧 ONNX 运算中枢 (移动端 Safari 与低内存硬件完全兼容版)
 */

if (typeof ort !== 'undefined' && ort.env) {
    ort.env.wasm = ort.env.wasm || {};
    ort.env.wasm.simd = false;
    ort.env.wasm.numThreads = 1;
    ort.env.wasm.proxy = false;
}

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
                graphOptimizationLevel: 'disabled'
            };

            const t0 = performance.now();
            logToConsole("正在挂载本地默认模型 /models/doc_corner.onnx...");

            try {
                const modelUrl = "/models/doc_corner.onnx?t=" + new Date().getTime();
                this.session = await ort.InferenceSession.create(modelUrl, sessionOptions);
                const cost = (performance.now() - t0).toFixed(1);
                this.readyStatus["corner"] = true;
                this.updateUIStatus("WASM (标准兼容)", "持久化挂载", "doc_corner.onnx", `${cost}ms`);
                logToConsole(`✔ 端侧角点定位引擎装载完成 (耗时: ${cost}ms)`, "success");

                const pill = document.getElementById("ai-engine-pill");
                if (pill) {
                    pill.innerText = "⚡ 端侧 AI 就绪 (WASM 兼容模式)";
                    pill.style.background = "#dcfce7";
                    pill.style.color = "#166534";
                }
            } catch (loadErr) {
                let reason = loadErr.message || "未知原因";
                if (reason.includes("invalid wire type") || reason.includes("offset")) {
                    reason = "模型文件损坏 (可能下载到了 HTML 网页或空文件)";
                } else if (reason.includes("SIMD")) {
                    reason = "设备不支持 WASM SIMD 指令集";
                }
                logToConsole(`ℹ 本地模型未就绪: ${reason}，系统已无缝回退到主机计算`, "warn");
                this.updateUIStatus("主机端代理", "宿主机计算", "服务端几何滤镜", "0ms");

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
        if (!file.name.toLowerCase().endsWith(".onnx")) {
            if (alertBox) alertBox.innerHTML = '<span style="color:#ef4444;">仅允许上传 .onnx 格式二进制模型</span>';
            return;
        }

        logToConsole(`正在校验并上传模型: ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)...`);
        const fd = new FormData();
        fd.append("model_file", file);

        try {
            const resp = await fetch("/api/model/upload", { method: "POST", body: fd });
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
                box.innerHTML = '<div style="font-size:11px; color:#94a3b8; padding:6px;">暂未挂载外部 ONNX 权重</div>';
                return;
            }
            box.innerHTML = models.map(m => `
                <div style="display:flex; justify-content:space-between; align-items:center; background:#fff; border:1px solid #e2e8f0; padding:6px 8px; border-radius:4px; font-size:11px; margin-bottom:4px;">
                    <div><b>📦 ${m.name}</b> <span style="color:#64748b; font-size:10px;">(${m.size})</span></div>
                    <button type="button" class="btn-text-danger" style="color:#ef4444; border:none; background:none; cursor:pointer;" onclick="DocAI.removeModel('${m.name}')">移除</button>
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
            this.init();
        } catch (e) {
            logToConsole(`移除异常: ${e.message}`, "error");
        }
    },

    clearCacheAndReload: async function() {
        logToConsole("正在清理端侧运行时缓存与离线存储...");
        try {
            if (window.caches && caches.keys) {
                const keys = await caches.keys();
                await Promise.all(keys.map(k => caches.delete(k)));
            }
            localStorage.clear();
            sessionStorage.clear();
        } catch (e) {
            console.warn("清理存储异常:", e);
        }
        logToConsole("✔ 缓存已全部抹除，正在重新载入工作台...", "success");
        setTimeout(() => {
            window.location.reload(true);
        }, 300);
    }
};

document.addEventListener("DOMContentLoaded", () => {
    window.DocAI.init();
});
