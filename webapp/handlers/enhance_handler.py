#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
import math
import numpy as np
import cv2
from handlers.base_handler import BaseHandler

MODEL_PATH = "/opt/webapp/models/doc_corner.onnx"
net = None
if os.path.exists(MODEL_PATH):
    try:
        net = cv2.dnn.readNetFromONNX(MODEL_PATH)
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    except Exception:
        pass

def crop_scanner_black_borders(img):
    """几何自适应边界收割：探测并裁除扫描仪物理黑边与装订黑缝"""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    top, bottom, left, right = 0, h, 0, w

    # 扫描上边缘（向下探测最多 12% 高度）
    for y in range(int(h * 0.12)):
        line = gray[y, int(w * 0.15):int(w * 0.85)]
        if np.mean(line < 100) > 0.35 or np.mean(line) < 140:
            top = y + 1
        else:
            if y > top + 5:
                break

    # 扫描下边缘（向上探测最多 8% 高度）
    for y in range(h - 1, int(h * 0.92), -1):
        line = gray[y, int(w * 0.15):int(w * 0.85)]
        if np.mean(line < 100) > 0.35 or np.mean(line) < 140:
            bottom = y
        else:
            if (h - 1 - y) > (h - bottom + 5):
                break

    # 扫描左边缘（向右探测最多 8% 宽度）
    for x in range(int(w * 0.08)):
        col = gray[int(h * 0.15):int(h * 0.85), x]
        if np.mean(col < 100) > 0.35 or np.mean(col) < 140:
            left = x + 1
        else:
            if x > left + 5:
                break

    # 扫描右边缘（向左探测最多 8% 宽度）
    for x in range(w - 1, int(w * 0.92), -1):
        col = gray[int(h * 0.15):int(h * 0.85), x]
        if np.mean(col < 100) > 0.35 or np.mean(col) < 140:
            right = x
        else:
            if (w - 1 - x) > (w - right + 5):
                break

    if (bottom - top > h * 0.70) and (right - left > w * 0.70):
        img = img[top:bottom, left:right]

    return img

def auto_perspective_crop(cv_img):
    """结合深度模型 Mask 与外接矩形的文档智能切边"""
    h, w = cv_img.shape[:2]
    
    # 物理黑边先行切除
    cv_img = crop_scanner_black_borders(cv_img)
    h, w = cv_img.shape[:2]

    if net is not None:
        try:
            blob = cv2.dnn.blobFromImage(cv_img, 1.0 / 255.0, (256, 256), swapRB=True, crop=False)
            net.setInput(blob)
            out = net.forward()

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
                        cv_img = cv_img[y0:y1, x0:x1]
        except Exception:
            pass

    return cv_img

def sanitize_clean_borders(img):
    """极细安全边缘置白：消除传感器最外圈残留噪点"""
    h, w = img.shape[:2]
    m = 6
    img[:m, :] = (255, 255, 255)
    img[h - m:, :] = (255, 255, 255)
    img[:, :m] = (255, 255, 255)
    img[:, w - m:] = (255, 255, 255)
    return img

def camscanner_core_render(img, is_color=True):
    h, w = img.shape[:2]

    # 背景大核除法光照归一化
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (45, 45))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
    bg = cv2.GaussianBlur(bg, (39, 39), 0).astype(np.float32) + 1.0

    bg_3d = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    norm = (img.astype(np.float32) / bg_3d) * 255.0
    norm = np.clip(norm, 0, 255).astype(np.uint8)

    # 1. 黑白模式：实心笔画 LUT 映射，消灭空心镂空字
    if not is_color:
        norm_gray = cv2.cvtColor(norm, cv2.COLOR_BGR2GRAY)
        lut = np.zeros(256, dtype=np.uint8)
        for i in range(256):
            if i >= 195:
                lut[i] = 255
            elif i <= 140:
                lut[i] = int((i / 140.0) ** 1.3 * 40.0)
            else:
                lut[i] = int(40.0 + ((i - 140.0) / 55.0) * (255.0 - 40.0))
        
        solid_gray = cv2.LUT(norm_gray, lut)
        out = cv2.cvtColor(solid_gray, cv2.COLOR_GRAY2BGR)
        return sanitize_clean_borders(out)

    # 2. 彩色模式：保留彩色插图与字迹饱和度
    hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV)
    h_c, s_c, v_c = cv2.split(hsv)

    lut_v = np.zeros(256, dtype=np.uint8)
    for i in range(256):
        if i >= 210:
            lut_v[i] = 255
        elif i <= 130:
            lut_v[i] = int((i / 130.0) ** 1.2 * 50.0)
        else:
            lut_v[i] = int(50.0 + ((i - 130.0) / 80.0) * (255.0 - 50.0))

    v_final = cv2.LUT(v_c, lut_v)
    s_f = s_c.astype(np.float32)
    s_boost = np.clip(s_f * 1.4, 0, 255).astype(np.uint8)
    s_final = np.where((v_final > 250) & (s_f < 15), 0, s_boost)

    merged = cv2.merge([h_c, s_final.astype(np.uint8), v_final])
    out = cv2.cvtColor(merged, cv2.COLOR_HSV2BGR)
    return sanitize_clean_borders(out)

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes
        
        img = auto_perspective_crop(img)
        is_color = (str(color_mode).strip().lower() == "color")
        result = camscanner_core_render(img, is_color=is_color)
        _, enc = cv2.imencode(".jpg", result, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return enc.tobytes()
    except Exception:
        return raw_bytes

class EnhancePreviewHandler(BaseHandler):
    def post(self):
        try:
            files = self.request.files.get("file", [])
            color_mode = self.get_argument("color_mode", "monochrome").strip()
            if not files:
                self.set_status(400)
                self.write("未收到预览文件")
                return
            raw_bytes = files[0]["body"]
            processed_bytes = enhance_camscanner_precise(raw_bytes, color_mode=color_mode)
            self.set_header("Content-Type", "image/jpeg")
            self.set_header("Cache-Control", "no-cache")
            self.write(processed_bytes)
            self.finish()
        except Exception as e:
            self.set_status(500)
            self.write(str(e))
