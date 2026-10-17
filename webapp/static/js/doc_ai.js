/**
 * 端侧 ONNX 运算中枢 (完整闭环与自动降级版 - 深度优化)
 * 1. 自动前处理 (256x256 RGB Tensor，纯净兼容模式)
 * 2. 推理角点热力图/坐标并提取 4 个顶点
 * 3. 前端 Canvas 双三角形映射透视拉平
 * 4. 模型缺失、SIMD受限或损坏时，零报错自动透明降级到服务端 OpenCV 处理
 */

// 彻底屏蔽 SIMD 与 Worker 代理，确保移动端 Safari 与低配客户端环境零崩溃
if (typeof ort !== 'undefined') {
    ort.env.wasm = ort.env.wasm || {};
    ort.env.wasm.simd = false;
    ort.env.wasm.proxy = false;
    ort.env.wasm.numThreads = 1;
}

window.DocAI = {
    session: null,
    isReady: false,

    init: async function() {
        logToConsole("端侧 AI 诊断守护启动...");
        try {
            if (typeof ort === 'undefined') {
                logToConsole("未检测到 onnxruntime-web 运行时，转用服务端轻量 OpenCV 通道", "warn");
                this.fallbackToHost("未挂载运行时");
                return;
            }

            // 禁用内存碎片整理与图算子优化，绕过 WASM 算子检查
            const sessionOptions = {
                executionProviders: ['wasm'],
                graphOptimizationLevel: 'disabled',
                enableCpuMemArena: false,
                enableMemPattern: false
            };

            const t0 = performance.now();
            logToConsole("正在装载本地轻量角点模型 /models/doc_corner.onnx...");

            try {
                const modelUrl = "/models/doc_corner.onnx?t=" + new Date().getTime();
                this.session = await ort.InferenceSession.create(modelUrl, sessionOptions);
                const cost = (performance.now() - t0).toFixed(1);
                this.isReady = true;
                this.updateUIStatus("WASM (纯净兼容)", "持久化挂载", "doc_corner.onnx", `${cost}ms`);
                logToConsole(`✔ 端侧角点定位引擎装载完成 (耗时: ${cost}ms)`, "success");

                const pill = document.getElementById("ai-engine-pill");
                if (pill) {
                    pill.innerText = "⚡ 端侧 AI 就绪 (自动切边拉平)";
                    pill.style.background = "#dcfce7";
                    pill.style.color = "#166534";
                }
            } catch (loadErr) {
                let reason = loadErr.message || "文件未就绪";
                logToConsole(`ℹ 本地模型未就绪: ${reason}，系统已无缝切换至 OpenCV 轻量图像管线`, "warn");
                this.fallbackToHost("OpenCV 几何滤镜");
            }
        } catch (e) {
            logToConsole(`端侧环境初始化跳过: ${e.message}`, "warn");
            this.fallbackToHost("系统级自动增强");
        }
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

    // 图像前处理：归一化为 1x3x256x256 Float32Array RGB Tensor
    preprocess: function(img) {
        const targetW = 256, targetH = 256;
        const canvas = document.createElement("canvas");
        canvas.width = targetW;
        canvas.height = targetH;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, targetW, targetH);
        const imgData = ctx.getImageData(0, 0, targetW, targetH).data;

        const floatData = new Float32Array(3 * targetW * targetH);
        const channelSize = targetW * targetH;

        for (let i = 0; i < channelSize; i++) {
            floatData[i] = imgData[i * 4] / 255.0;
            floatData[channelSize + i] = imgData[i * 4 + 1] / 255.0;
            floatData[2 * channelSize + i] = imgData[i * 4 + 2] / 255.0;
        }

        return new ort.Tensor("float32", floatData, [1, 3, targetH, targetW]);
    },

    // 从模型输出解析 4 个角点坐标 (适配 Heatmap 峰值及归一化关键点)
    extractCorners: function(outputTensor, origW, origH) {
        if (!outputTensor || !outputTensor.data) return null;
        const data = outputTensor.data;
        const dims = outputTensor.dims;
        let corners = [];

        // 格式 1: 直接输出 8 个坐标点 [1, 8]
        if (dims.length === 2 && dims[1] === 8) {
            for (let i = 0; i < 4; i++) {
                corners.push({
                    x: data[i * 2] * origW,
                    y: data[i * 2 + 1] * origH
                });
            }
        } 
        // 格式 2: 4 通道热力图 [1, 4, H, W]
        else if (dims.length === 4 && dims[1] === 4) {
            const h = dims[2], w = dims[3];
            const planeSize = h * w;
            for (let c = 0; c < 4; c++) {
                let maxVal = -Infinity, maxIdx = 0;
                const offset = c * planeSize;
                for (let i = 0; i < planeSize; i++) {
                    if (data[offset + i] > maxVal) {
                        maxVal = data[offset + i];
                        maxIdx = i;
                    }
                }
                const py = Math.floor(maxIdx / w);
                const px = maxIdx % w;
                corners.push({
                    x: (px / w) * origW,
                    y: (py / h) * origH
                });
            }
        }

        // 顶点几何排序：左上(TL)、右上(TR)、右下(BR)、左下(BL)
        if (corners.length === 4) {
            corners.sort((a, b) => a.y - b.y);
            const top = [corners[0], corners[1]].sort((a, b) => a.x - b.x);
            const bottom = [corners[2], corners[3]].sort((a, b) => a.x - b.x);
            return [top[0], top[1], bottom[1], bottom[0]];
        }
        return null;
    },

    // 前端 Canvas 透视变换拉直切边
    warpPerspective: function(img, pts) {
        const [tl, tr, br, bl] = pts;
        const widthTop = Math.hypot(tr.x - tl.x, tr.y - tl.y);
        const widthBottom = Math.hypot(br.x - bl.x, br.y - bl.y);
        const heightLeft = Math.hypot(bl.x - tl.x, bl.y - tl.y);
        const heightRight = Math.hypot(br.x - tr.x, br.y - tr.y);

        const outW = Math.max(widthTop, widthBottom);
        const outH = Math.max(heightLeft, heightRight);

        // 若角点框选区域过小，视为无效识别，安全退出
        if (outW < img.width * 0.35 || outH < img.height * 0.35) {
            return Promise.resolve(img);
        }

        const canvas = document.createElement("canvas");
        canvas.width = Math.round(outW);
        canvas.height = Math.round(outH);
        const ctx = canvas.getContext("2d");

        this.drawTriangleWarp(ctx, img, [tl, tr, br], [{x:0, y:0}, {x:outW, y:0}, {x:outW, y:outH}]);
        this.drawTriangleWarp(ctx, img, [tl, br, bl], [{x:0, y:0}, {x:outW, y:outH}, {x:0, y:outH}]);

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

    // 闭环总调度入口：端侧 AI 优先切边拉平，异常或未就绪时自动透明降级
    processImageAuto: async function(rawImg) {
        if (!this.isReady || !this.session) {
            return rawImg;
        }
        try {
            const t0 = performance.now();
            const inputTensor = this.preprocess(rawImg);
            
            // 动态匹配模型实际输入节点名称 (input / images / data 等)
            const inputName = this.session.inputNames && this.session.inputNames.length > 0 
                ? this.session.inputNames[0] 
                : "input";
            const feeds = {};
            feeds[inputName] = inputTensor;

            const results = await this.session.run(feeds);
            const outputTensor = results[Object.keys(results)[0]];
            const corners = this.extractCorners(outputTensor, rawImg.width, rawImg.height);
            const cost = (performance.now() - t0).toFixed(1);
            document.getElementById("diag-metric-speed")?.innerText = `${cost}ms`;

            if (corners) {
                logToConsole(`✔ 端侧 AI 锁定文档 4 个顶点，执行智能平整 (${cost}ms)`, "success");
                return await this.warpPerspective(rawImg, corners);
            }
        } catch (e) {
            logToConsole(`端侧推理跳过，转由后端 OpenCV 兜底: ${e.message}`, "warn");
        }
        return rawImg;
    },

    // 模型校验与自动正名上传
    inspectAndUploadModel: async function(inputId) {
        const fileInput = document.getElementById(inputId);
        const alertBox = document.getElementById("model-alert");
        if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
            if (alertBox) alertBox.innerHTML = '<span style="color:#ef4444;">请选取 .onnx 文件</span>';
            return;
        }

        const file = fileInput.files[0];
        const rawFileName = file.name || "";
        if (!rawFileName.toLowerCase().endsWith(".onnx")) {
            if (alertBox) alertBox.innerHTML = '<span style="color:#ef4444;">仅允许上传 .onnx 格式二进制模型</span>';
            return;
        }

        const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
        logToConsole(`正在校验并上传模型: ${rawFileName} (${sizeMb} MB)...`);

        const fd = new FormData();
        // 关键增强：自动将如 doc_corner 2.onnx 纠偏固化为标准名 doc_corner.onnx
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
                logToConsole(`✔ ${res.msg}`, "success");
                if (alertBox) alertBox.innerHTML = `<span style="color:#10b981;">✔ ${res.msg}</span>`;
                this.fetchDeployedModels();
                this.init();
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
        } catch (e) {}
        logToConsole("✔ 缓存已全部抹除，正在重新载入工作台...", "success");
        setTimeout(() => window.location.reload(true), 300);
    }
};

document.addEventListener("DOMContentLoaded", () => {
    window.DocAI.init();
});
