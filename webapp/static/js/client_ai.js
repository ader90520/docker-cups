/**
 * CUPS 智能工作台 - 端侧多模型调度引擎与在线参数适配器 (client_ai.js)
 */

const DocAI = {
    cornerSession: null,
    unwarpSession: null,
    tempLoadedSession: null,
    readyStatus: {
        corner: false,
        unwarp: false
    },
    currentBackend: 'none',

    customConfig: {
        inputName: 'input',
        inputW: 496,
        inputH: 720,
        outputName: 'grid',
        algoMode: 'uvdoc_grid'
    },

    log: function(msg) {
        const consoleEl = document.getElementById('ai-diag-console');
        if (consoleEl) {
            const time = new Date().toTimeString().split(' ')[0];
            consoleEl.innerHTML = `<div>[${time}] ${msg}</div>` + consoleEl.innerHTML;
        }
        console.log(`[DocAI] ${msg}`);
    },

    loadPersistedConfig: function() {
        try {
            const stored = localStorage.getItem('cups_ai_model_config');
            if (stored) {
                this.customConfig = Object.assign(this.customConfig, JSON.parse(stored));
            }
        } catch (e) {}
    },

    init: async function() {
        this.loadPersistedConfig();
        this.log('端侧 AI 诊断守护启动...');
        const pill = document.getElementById('ai-engine-pill');
        const metricBackend = document.getElementById('diag-metric-backend');
        const metricCache = document.getElementById('diag-metric-cache');
        const metricModel = document.getElementById('diag-metric-model');
        const metricSpeed = document.getElementById('diag-metric-speed');

        if (pill) {
            pill.style.background = '#e2e8f0';
            pill.style.color = '#475569';
            pill.innerText = 'AI 引擎初始化中...';
        }

        // 规避 iOS Safari SIMD 报错，强制单线程标准 WASM
        if (typeof ort !== 'undefined' && ort.env) {
            if (ort.env.wasm) {
                ort.env.wasm.simd = false;
                ort.env.wasm.numThreads = 1;
            }
        } else {
            this.log('❌ 未检测到 onnxruntime-web 运行库');
            this.updateUiFallback(pill, metricBackend);
            return;
        }

        const sessionOptions = {
            executionProviders: ['webgl', 'wasm'],
            graphOptimizationLevel: 'all'
        };

        let t0 = performance.now();

        // 1. 加载透视切边模型
        try {
            this.cornerSession = await ort.InferenceSession.create('/models/doc_corner.onnx?v=' + Date.now(), sessionOptions);
            this.readyStatus.corner = true;
            this.log('✔ [成功] doc_corner.onnx 加载就绪');
        } catch (e) {
            this.log(`ℹ 透视切边模型未加载: ${e.message}`);
        }

        // 2. 加载弯曲展平模型
        try {
            this.unwarpSession = await ort.InferenceSession.create('/models/uvdoc_grid.onnx?v=' + Date.now(), sessionOptions);
            this.readyStatus.unwarp = true;
            this.log('✔ [成功] uvdoc_grid.onnx 加载就绪');
        } catch (e) {
            this.log(`ℹ 弯曲拉平模型未加载: ${e.message}`);
        }

        const totalCost = (performance.now() - t0).toFixed(0);

        if (this.readyStatus.corner || this.readyStatus.unwarp) {
            const count = (this.readyStatus.corner ? 1 : 0) + (this.readyStatus.unwarp ? 1 : 0);
            if (pill) {
                pill.style.background = '#dcfce7';
                pill.style.color = '#166534';
                pill.innerText = `端侧 AI 就绪 (${count} 个模型)`;
            }
            if (metricBackend) metricBackend.innerText = 'WebGL/WASM 双通道';
            if (metricCache) metricCache.innerText = '浏览器内存';
            if (metricModel) metricModel.innerText = this.readyStatus.unwarp ? 'UVDoc 网格展平' : 'DocCorner 透视';
            if (metricSpeed) metricSpeed.innerText = `< ${totalCost}ms`;
        } else {
            this.updateUiFallback(pill, metricBackend);
        }

        this.fetchDeployedModels();
    },

    updateUiFallback: function(pill, metricBackend) {
        this.readyStatus.corner = false;
        this.readyStatus.unwarp = false;
        if (pill) {
            pill.style.background = '#f1f5f9';
            pill.style.color = '#475569';
            pill.innerText = '云端轻量加速';
        }
        if (metricBackend) metricBackend.innerText = '主机端代理';
    },

    // 模型上传、嗅探与在线参数适配核心
    inspectAndUploadModel: async function(fileInputId) {
        const fileInput = document.getElementById(fileInputId);
        if (!fileInput || fileInput.files.length === 0) {
            alert('请先选择 .onnx 模型文件！');
            return;
        }
        const file = fileInput.files[0];
        const modelUrl = URL.createObjectURL(file);

        this.log(`🔍 正在深度解析 ${file.name} 的模型拓扑结构...`);

        try {
            const tempSession = await ort.InferenceSession.create(modelUrl, {
                executionProviders: ['wasm'],
                graphOptimizationLevel: 'basic'
            });

            const inputNames = tempSession.inputNames || ['input'];
            const outputNames = tempSession.outputNames || ['output'];
            const detectedInputName = inputNames[0] || 'input';
            const detectedOutputName = outputNames[0] || 'output';

            const panel = document.getElementById('model-config-panel');
            const statusLbl = document.getElementById('model-match-status');
            if (panel) panel.style.display = 'block';

            document.getElementById('cfg-input-name').value = detectedInputName;
            document.getElementById('cfg-output-name').value = detectedOutputName;

            const lowerName = file.name.toLowerCase();
            const lowerOut = detectedOutputName.toLowerCase();

            if (lowerName.includes('uvdoc') || lowerOut.includes('grid')) {
                document.getElementById('cfg-input-w').value = 496;
                document.getElementById('cfg-input-h').value = 720;
                document.getElementById('cfg-algo-mode').value = 'uvdoc_grid';
                statusLbl.innerHTML = '<span style="color:#16a34a;">✔ 匹配 UVDoc 控制网格拉平架构</span>';
            } else if (lowerName.includes('corner') || lowerOut.includes('corner')) {
                document.getElementById('cfg-input-w').value = 256;
                document.getElementById('cfg-input-h').value = 256;
                document.getElementById('cfg-algo-mode').value = 'corner_homography';
                statusLbl.innerHTML = '<span style="color:#16a34a;">✔ 匹配 DocCorner 四角透视架构</span>';
            } else {
                document.getElementById('cfg-input-w').value = 512;
                document.getElementById('cfg-input-h').value = 512;
                document.getElementById('cfg-algo-mode').value = 'uvdoc_grid';
                statusLbl.innerHTML = '<span style="color:#d97706;">⚠ 未知拓扑架构，请手动核对尺寸</span>';
            }

            this.tempLoadedSession = tempSession;
            this.log(`元数据提取成功：输入节点 [${detectedInputName}]，输出节点 [${detectedOutputName}]`);

            // 同时将该模型同步上传至机顶盒持久化保存
            const fd = new FormData();
            fd.append('model', file);
            const alertBox = document.getElementById('model-alert');
            if (alertBox) alertBox.innerHTML = '<span style="color:#0284c7;">正在持久化保存模型至机顶盒...</span>';
            
            const upResp = await fetch('/api/models', { method: 'POST', body: fd });
            const upRes = await upResp.json();
            if (upRes.success) {
                if (alertBox) alertBox.innerHTML = `<span style="color:#10b981;">✔ ${upRes.msg}</span>`;
                this.fetchDeployedModels();
            } else {
                if (alertBox) alertBox.innerHTML = `<span style="color:#ef4444;">上传错误: ${upRes.msg}</span>`;
            }

        } catch (err) {
            this.log(`❌ 模型解析阻断: ${err.message}`);
            alert('模型解析失败！可能是 opset 版本不兼容或文件已损坏。');
        }
    },

    saveModelConfig: function() {
        this.customConfig = {
            inputName: document.getElementById('cfg-input-name').value.trim() || 'input',
            inputW: parseInt(document.getElementById('cfg-input-w').value) || 496,
            inputH: parseInt(document.getElementById('cfg-input-h').value) || 720,
            outputName: document.getElementById('cfg-output-name').value.trim() || 'output',
            algoMode: document.getElementById('cfg-algo-mode').value
        };

        localStorage.setItem('cups_ai_model_config', JSON.stringify(this.customConfig));
        this.log(`✔ 参数已热更新：${this.customConfig.inputW}x${this.customConfig.inputH}，算法模式：${this.customConfig.algoMode}`);

        if (this.tempLoadedSession) {
            if (this.customConfig.algoMode === 'uvdoc_grid') {
                this.unwarpSession = this.tempLoadedSession;
                this.readyStatus.unwarp = true;
            } else {
                this.cornerSession = this.tempLoadedSession;
                this.readyStatus.corner = true;
            }
        }

        const pill = document.getElementById('ai-engine-pill');
        if (pill) {
            pill.style.background = '#dcfce7';
            pill.style.color = '#166534';
            pill.innerText = '端侧新模型已激活';
        }
        alert('模型参数配置成功并立即生效！');
        this.fetchDeployedModels();
    },

    resetModelConfig: function() {
        localStorage.removeItem('cups_ai_model_config');
        this.customConfig = {
            inputName: 'input',
            inputW: 496,
            inputH: 720,
            outputName: 'grid',
            algoMode: 'uvdoc_grid'
        };
        document.getElementById('cfg-input-name').value = this.customConfig.inputName;
        document.getElementById('cfg-input-w').value = this.customConfig.inputW;
        document.getElementById('cfg-input-h').value = this.customConfig.inputH;
        document.getElementById('cfg-output-name').value = this.customConfig.outputName;
        document.getElementById('cfg-algo-mode').value = this.customConfig.algoMode;
        this.log('参数已重置为默认规格');
    },

    fetchDeployedModels: async function() {
        const container = document.getElementById('deployed-models-container');
        if (!container) return;

        try {
            const resp = await fetch('/api/models');
            const res = await resp.json();
            if (res.success && res.models && res.models.length > 0) {
                container.innerHTML = res.models.map(m => {
                    let badge = '<span style="background:#fee2e2; color:#991b1b; padding:1px 6px; border-radius:4px; font-size:10px;">🔴 未挂载</span>';
                    if ((m.name.includes('corner') && this.readyStatus.corner) || 
                        (m.name.includes('uvdoc') && this.readyStatus.unwarp)) {
                        badge = '<span style="background:#dcfce7; color:#166534; padding:1px 6px; border-radius:4px; font-size:10px; font-weight:700;">🟢 已加载就绪</span>';
                    }

                    return `
                        <div style="display:flex; justify-content:space-between; align-items:center; background:#fff; border:1px solid #e0e7ff; padding:6px 10px; border-radius:6px; font-size:11px;">
                            <div style="display:flex; align-items:center; gap:8px;">
                                <span style="font-weight:700; color:#1e293b;">🧠 ${m.name}</span>
                                <span style="color:#64748b;">(${m.size})</span>
                                ${badge}
                            </div>
                            <button type="button" class="btn-del" style="background:#fee2e2; color:#ef4444; padding:2px 8px; border-radius:4px;" onclick="DocAI.deleteModel('${m.name}')">🗑️ 删除模型</button>
                        </div>
                    `;
                }).join('');
            } else {
                container.innerHTML = '<div style="font-size:11px; color:#94a3b8;">暂未安装任何 .onnx 模型</div>';
            }
        } catch (e) {
            container.innerHTML = '<div style="font-size:11px; color:#ef4444;">获取模型列表失败</div>';
        }
    },

    deleteModel: async function(modelName) {
        if (!confirm(`确认删除模型文件 [${modelName}] 吗？删除后将无法恢复。`)) return;

        try {
            const resp = await fetch(`/api/models?name=${encodeURIComponent(modelName)}`, {
                method: 'DELETE'
            });
            const res = await resp.json();
            if (res.success) {
                this.log(`✔ [成功] ${modelName} 已删除`);
                alert(res.msg);
                this.clearCacheAndReload();
                this.fetchDeployedModels();
            } else {
                alert(`删除失败: ${res.msg}`);
            }
        } catch (e) {
            alert(`网络异常: ${e.message}`);
        }
    },

    preprocessDynamic: function(imgElement, targetW, targetH) {
        const canvas = document.createElement('canvas');
        canvas.width = targetW;
        canvas.height = targetH;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(imgElement, 0, 0, targetW, targetH);

        const imgData = ctx.getImageData(0, 0, targetW, targetH).data;
        const channelSize = targetW * targetH;
        const floatData = new Float32Array(3 * channelSize);

        for (let i = 0; i < channelSize; i++) {
            floatData[i] = imgData[i * 4] / 255.0;
            floatData[channelSize + i] = imgData[i * 4 + 1] / 255.0;
            floatData[2 * channelSize + i] = imgData[i * 4 + 2] / 255.0;
        }

        return new ort.Tensor('float32', floatData, [1, 3, targetH, targetW]);
    },

    unwarpDocument: async function(imageInput) {
        if (!this.readyStatus.unwarp || !this.unwarpSession) {
            return (imageInput instanceof Blob) ? imageInput : null;
        }

        return new Promise((resolve) => {
            const img = new Image();
            img.onload = async () => {
                try {
                    const tensor = this.preprocessDynamic(img, this.customConfig.inputW, this.customConfig.inputH);
                    const feeds = {};
                    feeds[this.customConfig.inputName] = tensor;

                    const results = await this.unwarpSession.run(feeds);
                    const outputGrid = results[this.customConfig.outputName];

                    if (!outputGrid || !outputGrid.data) {
                        resolve(null);
                        return;
                    }

                    const unwarpedBlob = await this.remapGridToImage(img, outputGrid.data, 45, 31);
                    this.log('✔ [成功] 文档弯曲拉直处理完毕');
                    resolve(unwarpedBlob);
                } catch (err) {
                    this.log(`弯曲拉直推理受阻: ${err.message}，回退使用原图`);
                    resolve(null);
                }
            };
            img.onerror = () => resolve(null);

            if (imageInput instanceof Blob || imageInput instanceof File) {
                img.src = URL.createObjectURL(imageInput);
            } else if (typeof imageInput === 'string') {
                img.src = imageInput;
            } else if (imageInput instanceof HTMLImageElement) {
                img.src = imageInput.src;
            }
        });
    },

    remapGridToImage: function(sourceImg, gridData, gridH, gridW) {
        return new Promise((resolve) => {
            const origW = sourceImg.naturalWidth || sourceImg.width;
            const origH = sourceImg.naturalHeight || sourceImg.height;

            const canvas = document.createElement('canvas');
            canvas.width = origW;
            canvas.height = origH;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, origW, origH);

            const cellW = origW / (gridW - 1);
            const cellH = origH / (gridH - 1);

            for (let r = 0; r < gridH - 1; r++) {
                for (let c = 0; c < gridW - 1; c++) {
                    const idxTL = (0 * gridH + r) * gridW + c;
                    const idxTR = (0 * gridH + r) * gridW + (c + 1);
                    const idxBL = (0 * gridH + (r + 1)) * gridW + c;

                    const uTL = ((gridData[idxTL] + 1.0) / 2.0) * origW;
                    const vTL = ((gridData[gridH * gridW + idxTL] + 1.0) / 2.0) * origH;
                    const uTR = ((gridData[idxTR] + 1.0) / 2.0) * origW;
                    const vBL = ((gridData[gridH * gridW + idxBL] + 1.0) / 2.0) * origH;

                    const srcW = Math.max(1, uTR - uTL);
                    const srcH = Math.max(1, vBL - vTL);
                    const dstX = c * cellW;
                    const dstY = r * cellH;

                    ctx.drawImage(sourceImg, uTL, vTL, srcW, srcH, dstX, dstY, cellW + 0.5, cellH + 0.5);
                }
            }

            canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.95);
        });
    },

    processDocument: async function(file) {
        let currentPayload = file;
        if (this.readyStatus.unwarp && this.customConfig.algoMode === 'uvdoc_grid') {
            const unwarped = await this.unwarpDocument(currentPayload);
            if (unwarped) currentPayload = unwarped;
        }
        return currentPayload;
    },

    clearCacheAndReload: function() {
        if (window.indexedDB) {
            try { indexedDB.deleteDatabase('onnxruntime-web'); } catch (e) {}
        }
        if (window.localStorage) {
            localStorage.removeItem('cups_ai_model_config');
        }
        this.log('🧹 浏览器端模型缓存与自定义参数已清空');
        this.init();
    }
};
