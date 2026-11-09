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

def safe_clean_page_borders(img):
    """
    轻量安全边缘清理：
    严禁暴力内切！只裁切 0.5% 边缘毛刺，确保题号与顶部大标题绝对完整
    """
    if img is None:
        return img
    h, w = img.shape[:2]
    # 仅安全微切 0.5%，绝不误伤题号和标题
    px = max(1, int(w * 0.005))
    py = max(1, int(h * 0.005))
    cropped = img[py:h-py, px:w-px]
    return cv2.copyMakeBorder(cropped, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[255, 255, 255])

def render_natural_pure_white(norm_img, is_color=False, density="dark"):
    """
    自然保真去底算法：
    只推白背景，不削弱字骨，完整保留原图的圆润墨迹与灰阶抗锯齿
    """
    if not is_color:
        gray = np.min(norm_img, axis=2).astype(np.float32)
        
        # 档位微调：只调整白底推平阈值与微弱对比度，绝不使用硬二值化
        if density == "ultra":
            white_thresh = 195.0
            gamma = 0.80
        elif density == "normal":
            white_thresh = 215.0
            gamma = 0.95
        else: # dark (推荐)
            white_thresh = 205.0
            gamma = 0.88

        # 1. 伽马平缓加深字迹，保持笔画圆润
        gray = np.clip(gray, 0, 255)
        darkened = np.power(gray / 255.0, 1.0 / gamma) * 255.0

        # 2. 软白场截断：背景推向纯白 255，深色字骨保留平滑灰阶梯度（不发虚断笔）
        v_out = np.where(darkened >= white_thresh, 255.0, darkened * (255.0 / white_thresh))
        result = np.clip(v_out, 0, 255).astype(np.uint8)

        # 3. 极浅底噪彻底推白
        result[result >= 238] = 255
        return cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    else:
        # 彩色模式：只漂白底色，完整保留红蓝印章与彩色字
        hsv = cv2.cvtColor(norm_img, cv2.COLOR_BGR2HSV).astype(np.float32)
        s = hsv[:, :, 1]
        v = hsv[:, :, 2]

        color_mask = (s > 15) & (v > 35)
        # 彩色轻度增艳 20%
        hsv[:, :, 1] = np.where(color_mask, np.clip(s * 1.20, 0, 255), s)
        # 背景推白
        hsv[:, :, 2] = np.where(color_mask, v, np.where(v >= 205.0, 255.0, v * (255.0 / 205.0)))
        
        result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        gray_chk = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
        result[gray_chk >= 238] = [255, 255, 255]
        return result

def camscanner_core_render(img, is_color=False, density="dark"):
    if img is None:
        return None

    # 1. 安全边框处理（不切文字）
    img = safe_clean_page_borders(img)
    h, w = img.shape[:2]

    # 2. 320px 大范围背景漫反射照度估计（平滑去除阴影泛黄）
    tiny_scale = 320.0 / max(h, w)
    tiny_img = cv2.resize(img, (int(w * tiny_scale), int(h * tiny_scale)), interpolation=cv2.INTER_NEAREST)
    bg_tiny = cv2.GaussianBlur(tiny_img, (0, 0), sigmaX=8, sigmaY=8)
    bg = cv2.resize(bg_tiny, (w, h), interpolation=cv2.INTER_LINEAR)

    # 3. 照度除法，消除暗角泛黄
    norm = cv2.divide(img, bg, scale=255)

    # 4. 执行自然保真去黑底
    return render_natural_pure_white(norm, is_color=is_color, density=density)

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome", density="dark"):
    try:
        if not HAVE_OPENCV:
            return raw_bytes
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        h, w = img.shape[:2]
        if max(h, w) > 1600:
            scale = 1600.0 / max(h, w)
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
