#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
import json
import math
import shutil
import numpy as np
import cv2
import tornado.web
from handlers.base_handler import BaseHandler

MODELS_PATH = "/opt/webapp/models"
MODEL_PATH = os.path.join(MODELS_PATH, "doc_corner.onnx")

net = None

def init_onnx_model():
    """安全初始化 ONNX 模型，失败时不影响全局运行"""
    global net
    if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 1024 * 1024:
        try:
            loaded_net = cv2.dnn.readNetFromONNX(MODEL_PATH)
            loaded_net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            loaded_net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            net = loaded_net
        except Exception:
            net = None
    else:
        net = None

init_onnx_model()

def limit_max_resolution(img, max_dim=2560):
    """内存安全上限：防止超大像素图片导致 1GB 内存盒子 OOM 崩溃"""
    if img is None:
        return None
    h, w = img.shape[:2]
    if max(h, w) > max_dim:
        scale = max_dim / float(max(h, w))
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return img

def crop_scanner_black_borders(img):
    """纯几何自适应边缘收割：探测并裁除外围大面积黑边和暗色木纹"""
    if img is None:
        return None
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    top, bottom, left, right = 0, h, 0, w

    for y in range(int(h * 0.12)):
        line = gray[y, int(w * 0.15):int(w * 0.85)]
        if np.mean(line < 110) > 0.30 or np.mean(line) < 140:
            top = y + 1
        else:
            if y > top + 5: break

    for y in range(h - 1, int(h * 0.88), -1):
        line = gray[y, int(w * 0.15):int(w * 0.85)]
        if np.mean(line < 110) > 0.30 or np.mean(line) < 140:
            bottom = y
        else:
            if (h - 1 - y) > (h - bottom + 5): break

    for x in range(int(w * 0.08)):
        col = gray[int(h * 0.15):int(h * 0.85), x]
        if np.mean(col < 110) > 0.30 or np.mean(col) < 140:
            left = x + 1
        else:
            if x > left + 5: break

    for x in range(w - 1, int(w * 0.92), -1):
        col = gray[int(h * 0.15):int(h * 0.85), x]
        if np.mean(col < 110) > 0.30 or np.mean(col) < 140:
            right = x
        else:
            if (w - 1 - x) > (w - right + 5): break

    if (bottom - top > h * 0.65) and (right - left > w * 0.65):
        img = img[top:bottom, left:right].copy()

    return img

def auto_perspective_crop(cv_img):
    """
    模型优先的边缘切边：
    若 ONNX 模型可用则走模型推理切边；
    若模型不可用或报错，平滑回退到几何边缘收割，绝不阻断后续流程。
    """
    if cv_img is None:
        return cv_img

    # 1. 尝试使用 ONNX 模型切边
    current_net = net
    if current_net is not None:
        try:
            h, w = cv_img.shape[:2]
            blob = cv2.dnn.blobFromImage(cv_img, 1.0 / 255.0, (256, 256), swapRB=True, crop=False)
            current_net.setInput(blob)
            out = current_net.forward()

            if len(out.shape) == 4:
                feat = out[0, 1] if out.shape[1] > 1 else out[0, 0]
                prob = 1.0 / (1.0 + np.exp(-np.clip(feat, -12.0, 12.0)))
                mask = (prob > 0.30).astype(np.uint8) * 255
                mask_full = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

                cnts, _ = cv2.findContours(mask_full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if cnts:
                    c = max(cnts, key=cv2.contourArea)
                    x, y, bw, bh = cv2.boundingRect(c)
                    if (bw * bh) > (w * h * 0.40) and (bw < w * 0.98 or bh < h * 0.98):
                        pad_x = int(bw * 0.01)
                        pad_y = int(bh * 0.01)
                        x0 = max(0, x - pad_x)
                        y0 = max(0, y - pad_y)
                        x1 = min(w, x + bw + pad_x)
                        y1 = min(h, y + bh + pad_y)
                        return cv_img[y0:y1, x0:x1].copy()
        except Exception:
            pass  # 模型异常直接进入下方降级

    # 2. 降级模式：纯几何算法收割四周边界
    try:
        cv_img = crop_scanner_black_borders(cv_img)
    except Exception:
        pass

    return cv_img

def sanitize_clean_borders(img):
    """边缘细微置白，防止切边边缘出现微小毛刺"""
    if img is None:
        return None
    h, w = img.shape[:2]
    m = 4
    img[:m, :] = (255, 255, 255)
    img[h - m:, :] = (255, 255, 255)
    img[:, :m] = (255, 255, 255)
    img[:, w - m:] = (255, 255, 255)
    return img

def camscanner_core_render(img, is_color=True):
    """
    全能王经典核心渲染算法（纯 OpenCV 图像处理，无外部依赖）：
    - 大核形态学闭运算估计光照背景，除法归一化彻底去除纸张阴影、褶皱、泛黄与桌面杂色
    - 黑色字迹压黑加深实心化
    - 彩色模式下智能保留红色标题、印章与彩色插图
    """
    if img is None:
        return None
    h, w = img.shape[:2]

    # 1. 解耦色彩空间：将亮度 (L) 与色度 (A/B) 分开
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    # 2. 局部大尺度闭运算估计背景光照
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (51, 51))
    bg_l = cv2.morphologyEx(l_channel, cv2.MORPH_CLOSE, kernel)
    bg_l = cv2.GaussianBlur(bg_l, (45, 45), 0).astype(np.float32) + 1.0

    # 3. 亮度除法归一化：将暗角、阴影、泛黄底色全量提亮置白
    norm_l = np.clip((l_channel.astype(np.float32) / bg_l) * 255.0, 0, 255).astype(np.uint8)

    # 4. 文字对比度强化映射曲线 (S 型非线性增强)
    lut_text = np.zeros(256, dtype=np.uint8)
    for i in range(256):
        if i >= 185:
            lut_text[i] = 255  # 纸张底色与浅灰直接变为纯白
        elif i <= 130:
            lut_text[i] = int((i / 130.0) ** 1.35 * 35.0)  # 文字笔迹压黑实心化
        else:
            lut_text[i] = int(35.0 + ((i - 130.0) / 55.0) * (255.0 - 35.0))
    enhanced_l = cv2.LUT(norm_l, lut_text)

    # 纯黑白模式直接输出
    if not is_color:
        out = cv2.cvtColor(enhanced_l, cv2.COLOR_GRAY2BGR)
        return sanitize_clean_borders(out)

    # 5. 彩色保留模式：利用 HSV 识别真实有效色彩区域
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h_c, s_c, v_c = cv2.split(hsv)

    # 判定彩色区域（排除白纸、浅灰和极暗杂斑）
    color_mask = (s_c > 38) & (v_c > 45)
    color_mask = cv2.GaussianBlur(color_mask.astype(np.uint8) * 255, (5, 5), 0) > 100

    # 彩色区域保留原色，非彩色区域（泛黄桌面/纸面残余）a/b 归中性灰 (128) 消除杂色
    a_new = np.where(color_mask, a_channel, 128)
    b_new = np.where(color_mask, b_channel, 128)

    # 融合亮度和色彩
    l_final = np.where(color_mask & (enhanced_l < 240), np.clip(l_channel * 1.1, 0, 245).astype(np.uint8), enhanced_l)

    lab_merged = cv2.merge([l_final, a_new.astype(np.uint8), b_new.astype(np.uint8)])
    out_bgr = cv2.cvtColor(lab_merged, cv2.COLOR_LAB2BGR)

    # 背景纯白区（L >= 252）强制写入纯白，消灭残余木纹
    out_bgr[enhanced_l >= 252] = [255, 255, 255]

    return sanitize_clean_borders(out_bgr)

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome", is_already_cropped=False):
    """
    通用图像增强主入口（Web 预览与云邮箱打印共享此函数）：
    - 支持直接传入二进制图片 bytes
    - 自动切边（模型优先，失败则几何兜底）
    - 无条件执行全能王白底除法归一化与彩色保留
    """
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        # 内存保护缩放
        img = limit_max_resolution(img, max_dim=2560)

        # 边缘切边（如果前端或调用方尚未裁切）
        if not is_already_cropped:
            try:
                img = auto_perspective_crop(img)
            except Exception:
                pass

        # 色彩模式判断
        is_color = (str(color_mode).strip().lower() == "color")

        # 核心去底白化与色彩增强
        result = camscanner_core_render(img, is_color=is_color)
        if result is None:
            result = img

        _, enc = cv2.imencode(".jpg", result, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return enc.tobytes()
    except Exception:
        return raw_bytes

# ================= 路由与接口处理器 =================

class EnhancePreviewHandler(BaseHandler):
    """Web 8088 页面实时预览接口"""
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def options(self):
        self.set_status(204)
        self.finish()

    def post(self):
        try:
            files = self.request.files.get("file", [])
            color_mode = self.get_argument("color_mode", "monochrome").strip()
            client_cropped = self.get_argument("client_cropped", "false").strip().lower() == "true"
            if not files:
                self.set_status(400)
                self.write("未收到预览文件")
                return

            raw_bytes = files[0]["body"]
            processed_bytes = enhance_camscanner_precise(
                raw_bytes, 
                color_mode=color_mode, 
                is_already_cropped=client_cropped
            )

            self.set_header("Content-Type", "image/jpeg")
            self.set_header("Content-Length", str(len(processed_bytes)))
            self.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.write(processed_bytes)
            self.finish()
        except Exception as e:
            self.set_status(500)
            self.write(str(e))

class ModelUploadHandler(tornado.web.RequestHandler):
    """模型动态热更新与自动热加载"""
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            files = self.request.files.get("model_file", [])
            if not files:
                self.set_status(400)
                self.write(json.dumps({"success": False, "msg": "未接收到模型文件"}))
                return

            raw_filename = files[0]["filename"]
            safe_filename = os.path.basename(raw_filename).replace(" ", "_")
            if not safe_filename or not safe_filename.lower().endswith(".onnx"):
                self.set_status(400)
                self.write(json.dumps({"success": False, "msg": "仅允许上传 .onnx 格式权重文件"}))
                return

            os.makedirs(MODELS_PATH, exist_ok=True)
            target_path = os.path.join(MODELS_PATH, safe_filename)
            with open(target_path, "wb") as f:
                f.write(files[0]["body"])

            default_path = os.path.join(MODELS_PATH, "doc_corner.onnx")
            shutil.copyfile(target_path, default_path)

            # 重新加载模型
            init_onnx_model()

            msg_status = "且模型加载成功" if net is not None else "但模型初始化受阻，已启用纯算法平滑兜底"
            self.write(json.dumps({"success": True, "msg": f"模型 {safe_filename} 部署完成，{msg_status}"}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))

class ModelManageHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        os.makedirs(MODELS_PATH, exist_ok=True)
        models = [f for f in os.listdir(MODELS_PATH) if f.endswith(".onnx")]
        self.write(json.dumps({"success": True, "models": models}))

class AdvancedEnhanceHandler(BaseHandler):
    """进阶批处理/自动化任务接口"""
    def post(self):
        files = self.request.files.get("file", [])
        color_mode = self.get_argument("color_mode", "monochrome").strip()
        if not files:
            self.set_status(400)
            self.write("未收到图像")
            return
        raw_bytes = files[0]["body"]
        out_bytes = enhance_camscanner_precise(raw_bytes, color_mode=color_mode)
        self.set_header("Content-Type", "image/jpeg")
        self.set_header("Content-Length", str(len(out_bytes)))
        self.write(out_bytes)

class OcrHandler(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.write(json.dumps({"success": True, "text": "", "codes": []}))

class WebhookPrintHandler(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.write(json.dumps({"success": True, "msg": "Webhook 任务已接收"}))
