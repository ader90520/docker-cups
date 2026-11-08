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

def smart_clean_page_borders_fast(img):
    """
    对标全能王的自适应文档切边：
    消除顶部黑缝、外露木桌面与翻页弯曲阴影
    """
    if img is None:
        return img
    h, w = img.shape[:2]
    scale = 360.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    
    # 采用 Canny 边缘结合形态学膨胀锁定真正纸张边缘
    edged = cv2.Canny(blurred, 40, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    dilated = cv2.dilate(edged, kernel, iterations=2)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    crop_x1, crop_y1, crop_x2, crop_y2 = 0, 0, w, h
    found = False

    if contours:
        # 取面积前三的大轮廓
        cnts = sorted(contours, key=cv2.contourArea, reverse=True)[:3]
        for c in cnts:
            x, y, cw, ch = cv2.boundingRect(c)
            # 纸张主体覆盖超过 40%
            if cw > small.shape[1] * 0.40 and ch > small.shape[0] * 0.40:
                rx, ry, rcw, rch = int(x / scale), int(y / scale), int(cw / scale), int(ch / scale)
                # 安全内收 3.5%：彻底斩断顶部黑折痕、边缘装订暗影
                pad_x = int(rcw * 0.035)
                pad_y = int(rch * 0.035)
                crop_x1 = max(0, rx + pad_x)
                crop_y1 = max(0, ry + pad_y)
                crop_x2 = min(w, rx + rcw - pad_x)
                crop_y2 = min(h, ry + rch - pad_y)
                found = True
                break

    if not found:
        # 兜底裁切 3%
        pad_x, pad_y = int(w * 0.03), int(h * 0.03)
        crop_x1, crop_y1 = pad_x, pad_y
        crop_x2, crop_y2 = w - pad_x, h - pad_y

    cropped = img[crop_y1:crop_y2, crop_x1:crop_x2]
    # 外围补白边防贴边
    return cv2.copyMakeBorder(cropped, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=[255, 255, 255])

def render_camscanner_black_and_white(norm_img, density="dark"):
    """
    全能王级黑白滤镜：CLAHE 局部对比度增强 + 伽马非线性压黑 + 抗透印
    """
    min_gray = np.min(norm_img, axis=2)

    # 1. 引入 CLAHE 限制对比度自适应直方图均衡，让每一行文字无论处于阴影还是强光区都浓黑一致
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced_gray = clahe.apply(min_gray)

    # 2. 档位动态曲线
    if density == "ultra":
        gamma = 0.55
        black_cut = 10.0
        white_cut = 215.0
    elif density == "normal":
        gamma = 0.80
        black_cut = 25.0
        white_cut = 205.0
    else:  # dark (推荐)
        gamma = 0.65
        black_cut = 15.0
        white_cut = 210.0

    # 3. 查表非线性压黑
    table = np.array([((i / 255.0) ** (1.0 / gamma)) * 255 for i in np.arange(0, 256)]).astype("uint8")
    darkened = cv2.LUT(enhanced_gray, table).astype(np.float32)

    # 4. 白场推平（透背印痕推到纯白 255，字骨压到 0）
    v_out = np.clip((darkened - black_cut) / (white_cut - black_cut) * 255.0, 0, 255).astype(np.uint8)
    v_out[v_out >= 225] = 255

    # 5. 微闭运算修复发虚笔画，消除断笔
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    closed = cv2.morphologyEx(v_out, cv2.MORPH_CLOSE, kernel)

    # 6. 微膨胀（白底腐蚀）0.4 个像素，文字饱满圆润、不发虚
    kernel_thicken = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8)
    eroded = cv2.erode(closed, kernel_thicken, iterations=1)
    result = cv2.addWeighted(closed, 0.5, eroded, 0.5, 0)

    return cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)

def camscanner_core_render(img, is_color=False, density="dark"):
    if img is None:
        return None

    # 1. 切边与清除四周黑块
    img = smart_clean_page_borders_fast(img)
    h, w = img.shape[:2]

    # 2. 320px 超轻量尺度计算背景照度图
    tiny_scale = 320.0 / max(h, w)
    tiny_img = cv2.resize(img, (int(w * tiny_scale), int(h * tiny_scale)), interpolation=cv2.INTER_NEAREST)
    bg_tiny = cv2.GaussianBlur(tiny_img, (0, 0), sigmaX=8, sigmaY=8)
    bg = cv2.resize(bg_tiny, (w, h), interpolation=cv2.INTER_LINEAR)

    # 3. 照度除法归一，抹平黄色纸底与背光
    norm = cv2.divide(img, bg, scale=255)

    if not is_color:
        return render_camscanner_black_and_white(norm, density=density)

    # 彩色模式：保护色彩饱和度
    hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV).astype(np.float32)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    color_mask = (s > 18) & (v > 38)
    s_boost = np.clip(s * 1.30, 0, 255)
    hsv[:, :, 1] = np.where(color_mask, s_boost, s)
    v_stretched = np.clip((v - 25.0) / (215.0 - 25.0) * 255.0, 0, 255)
    hsv[:, :, 2] = np.where(color_mask, v, v_stretched)

    result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    gray_check = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    result[gray_check >= 230] = [255, 255, 255]
    return result

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome", density="dark"):
    try:
        if not HAVE_OPENCV:
            return raw_bytes
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        h, w = img.shape[:2]
        # 提高预览分辨率上限至 1440px，确保 PC 端视窗缩放时字迹锐利
        if max(h, w) > 1440:
            scale = 1440.0 / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        is_color = (str(color_mode).strip().lower() == "color")
        result = camscanner_core_render(img, is_color=is_color, density=density)
        if result is None:
            result = img

        _, enc = cv2.imencode(".jpg", result, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return enc.tobytes()
    except Exception:
        return raw_bytes

class EnhancePreviewHandler(BaseHandler):
    def post(self):
        try:
            files = self.request.files.get("file", [])
            color_mode = self.get_argument("color_mode", "monochrome").strip()
            density = self.get_argument("density", "dark").strip().lower()

            if not files:
                self.set_status(400)
                self.write("未收到预览数据")
                return

            raw_bytes = files[0]["body"]
            processed_bytes = enhance_camscanner_precise(raw_bytes, color_mode=color_mode, density=density)
            self.set_header("Content-Type", "image/jpeg")
            self.set_header("Content-Length", str(len(processed_bytes)))
            self.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.write(processed_bytes)
            self.finish()
        except Exception as e:
            self.set_status(500)
            self.write(str(e))
