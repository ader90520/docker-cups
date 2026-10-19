/**
 * 端侧 ONNX 运算中枢 (精准适配 DocCornerNet 官方标准版)
 * 1. 严格遵循官方 Corner Order: 0:TL -> 1:TR -> 2:BR -> 3:BL
 * 2. 彻底剔除二次重排，根治对角三角凹坑与撕裂折叠
 * 3. 仿射透视自适应校正，输出完整平整试卷
 */

if (typeof ort !== 'undefined' && ort && ort.env) {
    ort.env.wasm = ort.env.wasm || {};
    ort.env.wasm.simd = false;
    ort.env.wasm.proxy = false;
    ort.env.wasm.numThreads = 1;
    ort.env.wasm.wasmPaths = "https://cdnjs.cloudflare.com/ajax/libs/onnxruntime-web/1.17.3/";
}

window.DocAI = {
    session: null,
    isReady: false,

    cfg: {
        layout: localStorage.getItem("docai_layout") || "auto",
        inputSize: parseInt(localStorage.getItem("docai_size") || "256", 10),
        norm: localStorage.getItem("docai_norm") || "div255"
    },

    actualLayout: "NHWC",

    init: async function() {
        if (typeof logToConsole === 'function') {
            logToConsole("端侧 AI 诊断守护启动...");
        }

        try {
            if (typeof ort === 'undefined' || !ort) {
                if (typeof logToConsole === 'function') {
                    logToConsole("未检测到 onnxruntime-web 运行时，转用服务端轻量 OpenCV 通道", "warn");
                }
                this.fallbackToHost("未挂载运行时");
                return;
            }

            const sessionOptions = {
                executionProviders: ['wasm'],
                graphOptimizationLevel: 'disabled',
                enableCpuMemArena: false,
                enableMemPattern: false
            };

            const t0 = performance.now();
            if (typeof logToConsole === 'function') {
                logToConsole("正在装载本地轻量角点模型 /models/doc_corner.onnx...");
            }

            try {
                const modelUrl = "/models/doc_corner.onnx?t=" + new Date().getTime();
                this.session = await ort.InferenceSession.create(modelUrl, sessionOptions);
                const cost = (performance.now() - t0).toFixed(1);
                this.isReady = true;

                this.inspectModelSpecs();

                this.updateUIStatus("WASM (纯净兼容)", "持久化挂载", "doc_corner.onnx", `${cost}ms`);
                if (typeof logToConsole === 'function') {
                    logToConsole(`✔ 端侧角点定位引擎装载完成 (耗时: ${cost}ms, 当前排布: ${this.actualLayout})`, "success");
                }

                const pill = document.getElementById("ai-engine-pill");
                if (pill) {
                    pill.innerText = "⚡ 端侧 AI 就绪 (自动切边拉平)";
                    pill.style.background = "#dcfce7";
                    pill.style.color = "#166534";
                }
            } catch (loadErr) {
                const reason = loadErr.message || "文件未就绪";
                if (typeof logToConsole === 'function') {
                    logToConsole(`ℹ 本地模型未就绪: ${reason}，系统已无缝切换至 OpenCV 轻量图像管线`, "warn");
                }
                this.fallbackToHost("OpenCV 几何滤镜");
            }
        } catch (e) {
            if (typeof logToConsole === 'function') {
                logToConsole(`端侧环境初始化跳过: ${e.message}`, "warn");
            }
            this.fallbackToHost("系统级自动增强");
        }
    },

    inspectModelSpecs: function() {
        try {
            if (!this.session) return;
            const inNames = this.session.inputNames || [];
            const outNames = this.session.outputNames || [];
            let inDims = "未知";
            let detected = "NHWC";

            if (inNames.length > 0 && this.session.inputs && this.session.inputs.length > 0) {
                const dims = this.session.inputs[0].dims;
                if (dims && dims.length === 4) {
                    inDims = `[${dims.join(', ')}]`;
                    if (dims[1] === 3) {
                        detected = "NCHW";
                    } else if (dims[3] === 3) {
                        detected = "NHWC";
                    }
                }
            }

            if (this.cfg.layout === "auto") {
                this.actualLayout = detected;
            } else {
                this.actualLayout = this.cfg.layout;
            }

            this.syncConfigUI();

            if (typeof logToConsole === 'function') {
                logToConsole(`💡 [模型规格解析] 输入节点: [${inNames.join(', ')}], 输出节点: [${outNames.join(', ')}]`);
                logToConsole(`💡 [模型维度探测] 输入形状: ${inDims} | 采用排布: [${this.actualLayout}]`, "success");
            }
        } catch (specErr) {
            this.actualLayout = (this.cfg.layout === "auto") ? "NHWC" : this.cfg.layout;
        }
    },

    syncConfigUI: function() {
        const selLayout = document.getElementById("ai-cfg-layout");
        const selSize = document.getElementById("ai-cfg-size");
        const selNorm = document.getElementById("ai-cfg-norm");
        if (selLayout) selLayout.value = this.cfg.layout;
        if (selSize) selSize.value = String(this.cfg.inputSize);
        if (selNorm) selNorm.value = this.cfg.norm;
    },

    applyCustomConfig: function() {
        const selLayout = document.getElementById("ai-cfg-layout");
        const selSize = document.getElementById("ai-cfg-size");
        const selNorm = document.getElementById("ai-cfg-norm");

        if (selLayout) this.cfg.layout = selLayout.value;
        if (selSize) this.cfg.inputSize = parseInt(selSize.value, 10);
        if (selNorm) this.cfg.norm = selNorm.value;

        localStorage.setItem("docai_layout", this.cfg.layout);
        localStorage.setItem("docai_size", String(this.cfg.inputSize));
        localStorage.setItem("docai_norm", this.cfg.norm);

        this.inspectModelSpecs();
        if (typeof logToConsole === 'function') {
            logToConsole(`✔ 模型参数已更新: 排布=${this.cfg.layout}(实际:${this.actualLayout}), 尺寸=${this.cfg.inputSize}`, "success");
        }
        alert("参数已更新生效！");
    },

    fallbackToHost: function(modelDesc) {
        this.isReady = false;
        this.session = null;
        this.updateUIStatus("OpenCV 极速引擎", "宿主机处理", modelDesc, "0ms");
        const pill = document.getElementById("ai-engine-pill");
        if (pill) {
            pill.innerText = "☁️ 轻量模式 (OpenCV 智能拉平)";
            pill.style.background = "#f1f5f9";
            pill.style.color = "#475569";
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

    preprocess: function(img, forceLayout) {
        const targetW = this.cfg.inputSize || 256;
        const targetH = this.cfg.inputSize || 256;
        const canvas = document.createElement("canvas");
        canvas.width = targetW;
        canvas.height = targetH;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, targetW, targetH);
        const imgData = ctx.getImageData(0, 0, targetW, targetH).data;

        const layout = forceLayout || this.actualLayout || "NHWC";
        const floatData = new Float32Array(3 * targetW * targetH);
        const totalPixels = targetW * targetH;
        const normMode = this.cfg.norm || "div255";

        const transformPixel = (v) => {
            if (normMode === "tanh") {
                return (v / 127.5) - 1.0;
            }
            return v / 255.0;
        };

        if (layout === "NHWC") {
            for (let i = 0; i < totalPixels; i++) {
                floatData[i * 3 + 0] = transformPixel(imgData[i * 4 + 0]);
                floatData[i * 3 + 1] = transformPixel(imgData[i * 4 + 1]);
                floatData[i * 3 + 2] = transformPixel(imgData[i * 4 + 2]);
            }
            return new ort.Tensor("float32", floatData, [1, targetH, targetW, 3]);
        } else {
            for (let i = 0; i < totalPixels; i++) {
                floatData[i] = transformPixel(imgData[i * 4 + 0]);
                floatData[totalPixels + i] = transformPixel(imgData[i * 4 + 1]);
                floatData[2 * totalPixels + i] = transformPixel(imgData[i * 4 + 2]);
            }
            return new ort.Tensor("float32", floatData, [1, 3, targetH, targetW]);
        }
    },

    // 严格遵循 DocCornerNet 官方顺序提取四角：[TL, TR, BR, BL]
    extractCorners: function(outputTensor, origW, origH) {
        if (!outputTensor || !outputTensor.data) return null;
        const data = outputTensor.data;
        const len = data.length;
        if (len < 8) return null;

        let points = [];
        for (let i = 0; i < 4; i++) {
            let x = data[i * 2 + 0];
            let y = data[i * 2 + 1];

            // 比例归一化判定 (若是 0~1 的浮点比例则转换为真实像素)
            if (x <= 1.5 && y <= 1.5) {
                x *= origW;
                y *= origH;
            } else if (x <= 260 && y <= 260 && (origW > 300 || origH > 300)) {
                // 如果是基于 256 尺度的绝对像素坐标
                x = (x / 256.0) * origW;
                y = (y / 256.0) * origH;
            }

            // 边界保护
            x = Math.max(0, Math.min(origW, x));
            y = Math.max(0, Math.min(origH, y));

            points.push({ x: x, y: y });
        }

        // 直接遵循官方原生定义的顺序 [TL, TR, BR, BL]，不执行任何可能引发对角错乱的重排
        return points;
    },

    // 透视拉直变换
    warpPerspective: function(img, pts) {
        const [tl, tr, br, bl] = pts;

        const widthTop = Math.hypot(tr.x - tl.x, tr.y - tl.y);
        const widthBottom = Math.hypot(br.x - bl.x, br.y - bl.y);
        const heightLeft = Math.hypot(bl.x - tl.x, bl.y - tl.y);
        const heightRight = Math.hypot(br.x - tr.x, br.y - tr.y);

        const outW = Math.max(widthTop, widthBottom);
        const outH = Math.max(heightLeft, heightRight);

        if (outW < img.width * 0.25 || outH < img.height * 0.25) {
            return Promise.resolve(img);
        }

        const canvas = document.createElement("canvas");
        canvas.width = Math.round(outW);
        canvas.height = Math.round(outH);
        const ctx = canvas.getContext("2d");

        // 三角形 1: 左上(TL) -> 右上(TR) -> 右下(BR)
        this.drawTriangleWarp(ctx, img, [tl, tr, br], [
            { x: 0, y: 0 },
            { x: outW, y: 0 },
            { x: outW, y: outH }
        ]);

        // 三角形 2: 左上(TL) -> 右下(BR) -> 左下(BL)
        this.drawTriangleWarp(ctx, img, [tl, br, bl], [
            { x: 0, y: 0 },
            { x: outW, y: outH },
            { x: 0, y: outH }
        ]);

        return new Promise(resolve => {
            const warpedImg = new Image();
            warpedImg.onload = () => resolve(warpedImg);
            warpedImg.onerror = () => resolve(img);
            warpedImg.src = canvas.toDataURL("image/jpeg", 0.95);
        });
    },

    drawTriangleWarp: function(ctx, im, srcPts, dstPts) {
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(dstPts[0].x, dstPts[0].y);
        ctx.lineTo(dstPts[1].x, dstPts[1].y);
        ctx.lineTo(dstPts[2].x, dstPts[2].y);
        ctx.closePath();
        ctx.clip();

        const x0 = srcPts[0].x, y0 = srcPts[0].y;
        const x1 = srcPts[1].x, y1 = srcPts[1].y;
        const x2 = srcPts[2].x, y2 = srcPts[2].y;
        const u0 = dstPts[0].x, v0 = dstPts[0].y;
        const u1 = dstPts[1].x, v1 = dstPts[1].y;
        const u2 = dstPts[2].x, v2 = dstPts[2].y;

        const delta = u0*(v1 - v2) - u1*(v0 - v2) + u2*(v0 - v1);
        if (Math.abs(delta) > 0.0001) {
            const a = (x0*(v1 - v2) - x1*(v0 - v2) + x2*(v0 - v1)) / delta;
            const b = (u0*(x1 - x2) - u1*(x0 - x2) + u2*(x0 - x1)) / delta;
            const c = (u0*(v1*x2 - v2*x1) - u1*(v0*x2 - v2*x0) + u2*(v0*x1 - v1*x0)) / delta;
            const d = (y0*(v1 - v2) - y1*(v0 - v2) + y2*(v0 - v1)) / delta;
            const e = (u0*(y1 - y2) - u1*(y0 - y2) + u2*(y0 - y1)) / delta;
            const f = (u0*(v1*y2 - v2*y1) - u1*(v0*y2 - v2*y0) + u2*(v0*x1 - v1*x0)) / delta;

            ctx.transform(a, d, b, e, c, f);
            ctx.drawImage(im, 0, 0);
        }
        ctx.restore();
    },

    processImageAuto: async function(rawImg) {
        if (!this.isReady || !this.session) {
            return rawImg;
        }
        try {
            const t0 = performance.now();
            const inputName = this.session.inputNames && this.session.inputNames.length > 0 
                ? this.session.inputNames[0] 
                : "input";

            let results = null;

            try {
                const inputTensor = this.preprocess(rawImg, this.actualLayout);
                const feeds = {};
                feeds[inputName] = inputTensor;
                results = await this.session.run(feeds);
            } catch (runErr) {
                const errMsg = runErr.message || "";
                if (errMsg.includes("Expected: 3") || errMsg.includes("Expected: 256") || errMsg.includes("invalid dimensions")) {
                    const altLayout = (this.actualLayout === "NCHW") ? "NHWC" : "NCHW";
                    if (typeof logToConsole === 'function') {
                        logToConsole(`⚠️ 自动切换排布至 [${altLayout}]...`, "warn");
                    }
                    const retryTensor = this.preprocess(rawImg, altLayout);
                    const retryFeeds = {};
                    retryFeeds[inputName] = retryTensor;
                    results = await this.session.run(retryFeeds);
                    this.actualLayout = altLayout;
                } else {
                    throw runErr;
                }
            }

            // 精准匹配 coords 节点输出
            const outputKey = this.session.outputNames.find(n => n.includes("coord")) || Object.keys(results)[0];
            const outputTensor = results[outputKey];
            const corners = this.extractCorners(outputTensor, rawImg.width, rawImg.height);
            const cost = (performance.now() - t0).toFixed(1);
            
            const elSpeed = document.getElementById("diag-metric-speed");
            if (elSpeed) elSpeed.innerText = `${cost}ms`;

            if (corners) {
                if (typeof logToConsole === 'function') {
                    logToConsole(`✔ 端侧 AI 锁定文档 4 个顶点，按官方标准几何平直校正 (${cost}ms)`, "success");
                }
                return await this.warpPerspective(rawImg, corners);
            }
        } catch (e) {
            if (typeof logToConsole === 'function') {
                logToConsole(`端侧推理跳过，转由后端 OpenCV 兜底: ${e.message}`, "warn");
            }
        }
        return rawImg;
    },

    inspectAndUploadModel: async function(inputId) {
        let fileInput = null;
        if (inputId) {
            fileInput = document.getElementById(inputId);
        }
        if (!fileInput) {
            fileInput = document.getElementById("onnx-model-file-input") || 
                        document.querySelector("input[type='file'][accept*='.onnx']") ||
                        document.querySelector("#panel-devices input[type='file']");
        }

        const alertBox = document.getElementById("model-alert");

        if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
            alert("请先点击【选取文件】选择一个 .onnx 模型！");
            if (alertBox) alertBox.innerHTML = '<span style="color:#ef4444;">请选取 .onnx 文件</span>';
            return;
        }

        const file = fileInput.files[0];
        const rawFileName = file.name || "";
        if (!rawFileName.toLowerCase().endsWith(".onnx")) {
            alert("文件格式不正确，仅支持 .onnx 模型！");
            if (alertBox) alertBox.innerHTML = '<span style="color:#ef4444;">仅允许上传 .onnx 格式二进制模型</span>';
            return;
        }

        const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
        if (typeof logToConsole === 'function') {
            logToConsole(`正在校验并上传模型: ${rawFileName} (${sizeMb} MB)...`);
        }

        const fd = new FormData();
        fd.append("model_file", file, "doc_corner.onnx");

        try {
            const resp = await fetch("/api/model/upload", { method: "POST", body: fd });
            const text = await resp.text();
            let res;
            try {
                res = JSON.parse(text);
            } catch (jsonErr) {
                throw new Error(`服务端异常返回: ${text.substring(0, 60)}...`);
            }

            if (res.success) {
                if (typeof logToConsole === 'function') {
                    logToConsole(`✔ ${res.msg}`, "success");
                }
                if (alertBox) alertBox.innerHTML = `<span style="color:#10b981;">✔ ${res.msg}</span>`;
                this.fetchDeployedModels();
                this.init();
            } else {
                if (typeof logToConsole === 'function') {
                    logToConsole(`模型部署拒绝: ${res.msg}`, "error");
                }
                if (alertBox) alertBox.innerHTML = `<span style="color:#ef4444;">${res.msg}</span>`;
            }
        } catch (e) {
            if (typeof logToConsole === 'function') {
                logToConsole(`模型写入异常: ${e.message}`, "error");
            }
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
            if (typeof logToConsole === 'function') {
                logToConsole(res.msg, res.success ? "success" : "error");
            }
            this.fetchDeployedModels();
            this.init();
        } catch (e) {
            if (typeof logToConsole === 'function') {
                logToConsole(`移除异常: ${e.message}`, "error");
            }
        }
    },

    clearCacheAndReload: async function() {
        if (typeof logToConsole === 'function') {
            logToConsole("正在清理端侧运行时缓存与离线存储...");
        }
        try {
            if (window.isSecureContext && window.caches && typeof caches.keys === 'function') {
                const keys = await caches.keys();
                await Promise.all(keys.map(k => caches.delete(k)));
            }
        } catch (e) {
            console.warn("跳过 CacheStorage 抹除:", e);
        }

        try {
            localStorage.clear();
            sessionStorage.clear();
        } catch (e) {}

        if (typeof logToConsole === 'function') {
            logToConsole("✔ 缓存已全部抹除，正在重新载入工作台...", "success");
        }
        setTimeout(() => {
            window.location.reload(true);
        }, 300);
    }
};

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
        window.DocAI.init();
    });
} else {
    window.DocAI.init();
}
