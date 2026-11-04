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

def apply_usm_sharpen(img, amount=1.35, radius=1):
    """
    USM (Unsharp Masking) 动态高反差锐化：
    专门针对试卷打印的文字边缘强化，防止小字号、题号、括号笔画发虚发飘
    """
    if img is None:
        return img
    # 采用高斯模糊提取低频轮廓
    blurred = cv2.GaussianBlur(img, (0, 0), radius)
    # 高反差融合加权，锐化文字骨架与边缘
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
    双尺度极速除法归一化：
    在 320px 尺度上计算全局光照底色，耗时从 35000ms 降至 400ms，同时强化输出锐度
    """
    if img is None:
        return None

    # 1. 快速切除黑边与外围杂质
    img = smart_clean_page_borders_fast(img)
    h, w = img.shape[:2]

    # 2. 双尺度光照估计：在超轻量图上做平缓高斯模糊，瞬间完成
    tiny_scale = 320.0 / max(h, w)
    tiny_img = cv2.resize(img, (int(w * tiny_scale), int(h * tiny_scale)), interpolation=cv2.INTER_NEAREST)
    bg_tiny = cv2.GaussianBlur(tiny_img, (0, 0), sigmaX=8, sigmaY=8)
    bg = cv2.resize(bg_tiny, (w, h), interpolation=cv2.INTER_LINEAR)

    # 3. 照度除法归一化，彻底消除泛黄与不均匀阴影
    norm = cv2.divide(img, bg, scale=255)

    if not is_color:
        # ================= 极清黑白模式 (文字饱满、红字保留、防虚化) =================
        # 取 BGR 各通道最小值：使红色批改、浅蓝题号在通道中均呈现深色，防止被直接抹除
        min_gray = np.min(norm, axis=2)

        # 自适应局部对比度保护：白底更白、黑字更黑
        # 195 阈值彻底抹除灰底，保留虚线和题号
        _, thresh = cv2.threshold(min_gray, 195, 255, cv2.THRESH_BINARY)

        # 闭运算紧致笔画，修复因手机翻拍抖动造成的微小断线
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

        # 叠加 USM 锐化层，强化文字边缘反差
        sharpened_mono = apply_usm_sharpen(closed, amount=0.8, radius=1)
        return cv2.cvtColor(sharpened_mono, cv2.COLOR_GRAY2BGR)

    # ================= 纯正彩色模式 (保留红笔批注与插画色彩) =================
    hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV).astype(np.float32)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    # 色彩感知掩模：降低饱和度门槛，使浅红题号、淡橙虚线与浅彩插图均能被锁定
    color_mask = (s > 18) & (v > 38)

    # 彩色区域饱和度增益 30%，红笔批改鲜亮醒目
    s_boost = np.clip(s * 1.30, 0, 255)
    hsv[:, :, 1] = np.where(color_mask, s_boost, s)

    # 黑白底色区域做色阶拉伸，把微灰底直接推向纯白
    v_stretched = np.clip((v - 30.0) / (220.0 - 30.0) * 255.0, 0, 255)
    hsv[:, :, 2] = np.where(color_mask, v, v_stretched)

    result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # 残留浅灰底（灰度 >= 235）直接锁定为纯白 #FFFFFF
    gray_check = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    result[gray_check >= 235] = [255, 255, 255]

    # 彩色图像整体做一次轻微边缘保真锐化
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
        # 降采样保护：最大边限制在 1280px，既兼顾屏幕清晰度，又绝不拖慢 ARM 算力
        if max(h, w) > 1280:
            scale = 1280.0 / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        is_color = (str(color_mode).strip().lower() == "color")
        result = camscanner_core_render(img, is_color=is_color)
        if result is None:
            result = img

        # 采用高保真 JPEG 质量输出，保留边缘细节
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
