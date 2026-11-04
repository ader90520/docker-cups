#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
import json
import numpy as np
import tornado.web
from handlers.base_handler import BaseHandler

HAVE_OPENCV = False
try:
    import cv2
    HAVE_OPENCV = True
except Exception:
    HAVE_OPENCV = False

def apply_usm_sharpen(img, amount=1.2, radius=1):
    """USM 动态高反差锐化：强化文字笔画边缘反差"""
    if img is None:
        return img
    blurred = cv2.GaussianBlur(img, (0, 0), radius)
    sharpened = cv2.addWeighted(img, 1.0 + amount, blurred, -amount, 0)
    return sharpened

def smart_clean_page_borders_fast(img):
    """自适应快速边框清理 (280px 超轻量图快速定位文档，消除黑边)"""
    h, w = img.shape[:2]
    scale = 280.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_NEAREST)

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        x, y, cw, ch = cv2.boundingRect(c)
        if cw > small.shape[1] * 0.45 and ch > small.shape[0] * 0.45:
            rx, ry, rcw, rch = int(x / scale), int(y / scale), int(cw / scale), int(ch / scale)
            pad_x = int(rcw * 0.02)
            pad_y = int(rch * 0.02)
            x1 = max(0, rx + pad_x)
            y1 = max(0, ry + pad_y)
            x2 = min(w, rx + rcw - pad_x)
            y2 = min(h, ry + rch - pad_y)
            cropped = img[y1:y2, x1:x2]
            return cv2.copyMakeBorder(cropped, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[255, 255, 255])

    # 兜底裁切 1.5%
    px, py = int(w * 0.015), int(h * 0.015)
    cropped = img[py:h-py, px:w-px]
    return cv2.copyMakeBorder(cropped, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[255, 255, 255])

def camscanner_core_render(img, is_color=False):
    """
    极清饱满除法归一化：
    彻底摒弃丢边缘的粗暴硬二值化，采用与彩色一致的非线性色阶拉伸与抗锯齿保护
    """
    if img is None:
        return None

    # 1. 快速切除黑边与外围杂质
    img = smart_clean_page_borders_fast(img)
    h, w = img.shape[:2]

    # 2. 双尺度全局光照底色分离 (微缩图 320px 算底，速度 < 400ms)
    tiny_scale = 320.0 / max(h, w)
    tiny_img = cv2.resize(img, (int(w * tiny_scale), int(h * tiny_scale)), interpolation=cv2.INTER_NEAREST)
    bg_tiny = cv2.GaussianBlur(tiny_img, (0, 0), sigmaX=8, sigmaY=8)
    bg = cv2.resize(bg_tiny, (w, h), interpolation=cv2.INTER_LINEAR)

    # 3. 照度除法归一化，彻底消除泛黄与光照阴影
    norm = cv2.divide(img, bg, scale=255)

    if not is_color:
        # ================= 极清饱满黑白模式 (保留抗锯齿灰阶，字骨黑亮粗壮) =================
        # 取三通道最小值：确保红笔批改、淡蓝题号深色沉淀，绝不被抹白
        min_gray = np.min(norm, axis=2).astype(np.float32)

        # 非线性色阶拉伸：黑场下压至 40，白场截断在 200
        # 既将底色杂色全部推向纯白 255，又将浅灰文字深沉压黑，且完整保留边缘半透明抗锯齿像素
        v_stretched = np.clip((min_gray - 40.0) / (200.0 - 40.0) * 255.0, 0, 255).astype(np.uint8)

        # 残留浅底噪点直接锁定为纯白
        v_stretched[v_stretched >= 235] = 255

        # 叠加 USM 动态锐化层，强化文字微边缘反差，达到与彩色模式相同的饱满清晰度
        sharpened_mono = apply_usm_sharpen(v_stretched, amount=1.2, radius=1)
        return cv2.cvtColor(sharpened_mono, cv2.COLOR_GRAY2BGR)

    # ================= 纯正彩色模式 =================
    hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV).astype(np.float32)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    color_mask = (s > 18) & (v > 38)
    s_boost = np.clip(s * 1.30, 0, 255)
    hsv[:, :, 1] = np.where(color_mask, s_boost, s)
    v_stretched = np.clip((v - 30.0) / (220.0 - 30.0) * 255.0, 0, 255)
    hsv[:, :, 2] = np.where(color_mask, v, v_stretched)

    result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    gray_check = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    result[gray_check >= 235] = [255, 255, 255]

    return apply_usm_sharpen(result, amount=1.1, radius=1)

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    try:
        if not HAVE_OPENCV:
            return raw_bytes
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        h, w = img.shape[:2]
        if max(h, w) > 1280:
            scale = 1280.0 / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        is_color = (str(color_mode).strip().lower() == "color")
        result = camscanner_core_render(img, is_color=is_color)
        if result is None:
            result = img

        _, enc = cv2.imencode(".jpg", result, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
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
                self.write("未收到预览数据")
                return

            raw_bytes = files[0]["body"]
            processed_bytes = enhance_camscanner_precise(raw_bytes, color_mode=color_mode)
            self.set_header("Content-Type", "image/jpeg")
            self.set_header("Content-Length", str(len(processed_bytes)))
            self.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.write(processed_bytes)
            self.finish()
        except Exception as e:
            self.set_status(500)
            self.write(str(e))
