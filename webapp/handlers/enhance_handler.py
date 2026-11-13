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

def detect_and_crop_paper_content(img):
    """基于亮度投影精准切除多余桌面与边缘杂质"""
    if img is None:
        return img

    h, w = img.shape[:2]
    scale = 300.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    row_means = np.mean(gray, axis=1)
    mid_brightness = np.median(row_means)
    thresh_val = max(55.0, mid_brightness * 0.65)

    top = 0
    for r in range(len(row_means)):
        if row_means[r] >= thresh_val:
            top = r
            break

    bottom = len(row_means) - 1
    for r in range(len(row_means) - 1, -1, -1):
        if row_means[r] >= thresh_val:
            bottom = r
            break

    col_means = np.mean(gray, axis=0)
    left = 0
    for c in range(len(col_means)):
        if col_means[c] >= thresh_val:
            left = c
            break

    right = len(col_means) - 1
    for c in range(len(col_means) - 1, -1, -1):
        if col_means[c] >= thresh_val:
            right = c
            break

    y1 = int(top / scale)
    y2 = int(bottom / scale)
    x1 = int(left / scale)
    x2 = int(right / scale)

    if (y2 - y1) < h * 0.5 or (x2 - x1) < w * 0.5:
        pad_x, pad_y = int(w * 0.015), int(h * 0.015)
        y1, y2, x1, x2 = pad_y, h - pad_y, pad_x, w - pad_x

    y1 = min(h - 10, y1 + 4)
    y2 = max(y1 + 10, y2 - 4)
    x1 = min(w - 10, x1 + 4)
    x2 = max(x1 + 10, x2 - 4)

    cropped = img[y1:y2, x1:x2]
    return cv2.copyMakeBorder(cropped, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=[255, 255, 255])

def heal_broken_strokes_and_sharpen(gray_u8, density="dark"):
    """
    专治原图发虚、断笔、浅灰断线：
    1. 横纵微线性闭运算，把发虚断口自动桥接发实，且不让字体变胖
    2. 局部反差压黑，让淡字骨沉黑清晰
    """
    # 1. 在白底黑字中，灰度闭运算（MORPH_CLOSE）会将夹在深色笔画之间的浅灰断缝填充为深色
    # 使用极细的 1x2 纵向核与 2x1 横向核，只修补断笔，绝不增加笔画整体宽度
    k_h = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 1))
    k_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 2))
    healed_h = cv2.morphologyEx(gray_u8, cv2.MORPH_CLOSE, k_h)
    healed_v = cv2.morphologyEx(gray_u8, cv2.MORPH_CLOSE, k_v)
    # 取两者最小值（最黑实的部分），把横竖断线全部连上
    healed = np.minimum(healed_h, healed_v)

    # 2. 与原图按比例融合，保留圆润自然的抗锯齿边
    blend_ratio = 0.75 if density == "ultra" else (0.55 if density == "dark" else 0.30)
    merged = cv2.addWeighted(healed, blend_ratio, gray_u8, 1.0 - blend_ratio, 0)

    # 3. 轻度高频边缘收紧（Unsharp Mask），消除拍摄失焦带来的发虚雾感
    blur = cv2.GaussianBlur(merged, (0, 0), 1.0)
    crisp = cv2.addWeighted(merged, 1.25, blur, -0.25, 0)
    return crisp

def render_vector_like_document(img, is_color=False, density="dark"):
    """
    矢量级纤细高清文本引擎（含原图发虚断线发实修复）
    """
    h, w = img.shape[:2]

    # 1. 双尺度平滑光照估计
    tiny_scale = 320.0 / max(h, w)
    tiny_img = cv2.resize(img, (int(w * tiny_scale), int(h * tiny_scale)), interpolation=cv2.INTER_NEAREST)
    bg_tiny = cv2.GaussianBlur(tiny_img, (0, 0), sigmaX=8, sigmaY=8)
    bg = cv2.resize(bg_tiny, (w, h), interpolation=cv2.INTER_LINEAR)

    # 2. 照度归一消除纸张泛黄与阴影
    norm = cv2.divide(img, bg, scale=255)

    if not is_color:
        gray = np.min(norm, axis=2)

        # 3. 针对原图发虚断线进行方向性桥接发实
        healed_gray = heal_broken_strokes_and_sharpen(gray, density=density).astype(np.float32)

        # 4. 档位阶调控制（拉黑浅灰字骨，推平白底，保持字体苗条）
        if density == "ultra":
            bp = 50.0   # 强力压黑浅灰断字
            wp = 195.0  # 推平透背
            gamma = 0.78
        elif density == "normal":
            bp = 20.0
            wp = 215.0
            gamma = 1.00
        else:  # dark (推荐)
            bp = 35.0
            wp = 205.0
            gamma = 0.88

        stretched = np.clip((healed_gray - bp) / (wp - bp), 0.0, 1.0)
        v_out = np.power(stretched, gamma) * 255.0
        result = np.clip(v_out, 0, 255).astype(np.uint8)
        result[result >= 235] = 255

        return cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    else:
        # 彩色模式保持原色彩并推白背景
        hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV).astype(np.float32)
        s = hsv[:, :, 1]
        v = hsv[:, :, 2]

        color_mask = (s > 15) & (v > 35)
        hsv[:, :, 1] = np.where(color_mask, np.clip(s * 1.25, 0, 255), s)
        v_stretched = np.clip((v - 25.0) / (210.0 - 25.0) * 255.0, 0, 255)
        hsv[:, :, 2] = np.where(color_mask, v, v_stretched)

        result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        gray_chk = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
        result[gray_chk >= 235] = [255, 255, 255]
        return result

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome", density="dark"):
    try:
        if not HAVE_OPENCV:
            return raw_bytes
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        img = detect_and_crop_paper_content(img)

        h, w = img.shape[:2]
        if max(h, w) > 1600:
            scale = 1600.0 / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        is_color = (str(color_mode).strip().lower() == "color")
        result = render_vector_like_document(img, is_color=is_color, density=density)
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
