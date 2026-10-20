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

def smart_clean_page_borders(img):
    """自适应切除外部桌面木纹、折页暗角与装订中缝黑边"""
    h, w = img.shape[:2]
    scale = 450.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)))

    # 转为灰度并提取纸面主体
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        x, y, cw, ch = cv2.boundingRect(c)
        # 满足纸张主体覆盖面积时，实施安全内切
        if cw > small.shape[1] * 0.4 and ch > small.shape[0] * 0.4:
            rx, ry, rcw, rch = int(x / scale), int(y / scale), int(cw / scale), int(ch / scale)
            # 安全内切 2.5%，剔除书角黑斑与外露杂色
            pad_x = int(rcw * 0.025)
            pad_y = int(rch * 0.025)
            x1 = max(0, rx + pad_x)
            y1 = max(0, ry + pad_y)
            x2 = min(w, rx + rcw - pad_x)
            y2 = min(h, ry + rch - pad_y)
            cropped = img[y1:y2, x1:x2]
            return cv2.copyMakeBorder(cropped, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[255, 255, 255])

    # 兜底裁切 2%
    px, py = int(w * 0.02), int(h * 0.02)
    cropped = img[py:h-py, px:w-px]
    return cv2.copyMakeBorder(cropped, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[255, 255, 255])

def camscanner_core_render(img, is_color=False):
    """自适应光照除法归一化：兼顾全能王纯白底与浅色彩保护"""
    if img is None:
        return None

    # 1. 优先切除黑边与外围木桌杂边
    img = smart_clean_page_borders(img)

    # 2. 大核高斯滤波估计全局平缓光照底色
    bg = cv2.GaussianBlur(img, (0, 0), sigmaX=35, sigmaY=35)
    norm = cv2.divide(img, bg, scale=255)

    if not is_color:
        # ================= 极清黑白模式 (保护红字与细虚线，防止发虚) =================
        # 取 BGR 各通道最小值，使红色题号在绿色/蓝色通道呈现深色，避免直接被抹白
        min_gray = np.min(norm, axis=2)
        _, thresh = cv2.threshold(min_gray, 185, 255, cv2.THRESH_BINARY)
        # 形态学微闭运算紧致笔画，消除断线发虚
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        return cv2.cvtColor(closed, cv2.COLOR_GRAY2BGR)

    # ================= 扫描全能王级纯正彩色保真 =================
    # 转换至 HSV 色彩空间进行独立的色彩与照度分离增强
    hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV).astype(np.float32)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # 色彩感知掩模：降低饱和度门槛（s > 20），使浅红题号、淡橙虚线与浅彩插图均能被锁定
    color_mask = (s > 20) & (v > 40)

    # 对有色彩区域适度增益饱和度 30%，使红笔批改与图画更鲜明
    s_boost = np.clip(s * 1.30, 0, 255)
    hsv[:, :, 1] = np.where(color_mask, s_boost, s)

    # 对黑白文字与灰白纸面区域做白场色阶拉伸，将泛黄杂底直接推平至纯白
    v_stretched = np.clip((v - 35.0) / (225.0 - 35.0) * 255.0, 0, 255)
    hsv[:, :, 2] = np.where(color_mask, v, v_stretched)

    result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # 将极浅的微弱泛黄杂点（灰度 >= 238）直接锁定为纯白 #FFFFFF
    gray_check = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    result[gray_check >= 238] = [255, 255, 255]
    return result

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    try:
        if not HAVE_OPENCV:
            return raw_bytes
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        h, w = img.shape[:2]
        if max(h, w) > 2560:
            scale = 2560.0 / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        is_color = (str(color_mode).strip().lower() == "color")
        result = camscanner_core_render(img, is_color=is_color)
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
            # 默认接收并解析请求中的 color_mode，防止前端未指定时误回退至黑白
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
