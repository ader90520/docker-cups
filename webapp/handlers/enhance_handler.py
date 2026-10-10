#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
import math
import numpy as np
import cv2
from handlers.base_handler import BaseHandler

# 预留 AI 模型入口，模型缺失或推理异常时系统将自动降级使用纯 OpenCV 寻边算法
MODEL_PATH = "/opt/webapp/models/doc_corner.onnx"
net = None
if os.path.exists(MODEL_PATH):
    try:
        net = cv2.dnn.readNetFromONNX(MODEL_PATH)
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    except Exception:
        pass

def order_points_cv(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def auto_perspective_warp(cv_img):
    """双引擎透视拉平与安全切边：AI 模型优先，纯 OpenCV 形态学强力兜底"""
    h, w = cv_img.shape[:2]

    # === 引擎 1：ONNX 轻量角点模型优先 ===
    if net is not None:
        try:
            orig = cv_img.copy()
            blob = cv2.dnn.blobFromImage(cv_img, 1.0 / 255.0, (256, 256), swapRB=True, crop=False)
            net.setInput(blob)
            out = net.forward()
            
            corners = out[0].reshape(4, 2)
            if corners.max() <= 1.5:
                corners[:, 0] *= w
                corners[:, 1] *= h
            else:
                corners[:, 0] *= (w / 256.0)
                corners[:, 1] *= (h / 256.0)

            rect = order_points_cv(corners)
            (tl, tr, br, bl) = rect
            
            widthA = np.hypot(br[0] - bl[0], br[1] - bl[1])
            widthB = np.hypot(tr[0] - tl[0], tr[1] - tl[1])
            maxWidth = max(int(widthA), int(widthB))

            heightA = np.hypot(tr[0] - br[0], tr[1] - br[1])
            heightB = np.hypot(tl[0] - bl[0], tl[1] - bl[1])
            maxHeight = max(int(heightA), int(heightB))
            
            if maxWidth > 100 and maxHeight > 100:
                dst = np.array([[0, 0], [maxWidth - 1, 0], [maxWidth - 1, maxHeight - 1], [0, maxHeight - 1]], dtype="float32")
                M = cv2.getPerspectiveTransform(rect, dst)
                warped = cv2.warpPerspective(orig, M, (maxWidth, maxHeight), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                pad_y, pad_x = max(int(maxHeight * 0.015), 10), max(int(maxWidth * 0.015), 10)
                cropped = warped[pad_y:maxHeight - pad_y, pad_x:maxWidth - pad_x]
                return cv2.copyMakeBorder(cropped, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_CONSTANT, value=[255, 255, 255])
        except Exception:
            pass

    # === 引擎 2：纯 OpenCV 轮廓形态学兜底 ===
    try:
        orig = cv_img.copy()
        ratio = h / 600.0
        small_w = int(w / ratio)
        small = cv2.resize(cv_img, (small_w, 600), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        edged = cv2.Canny(blurred, 30, 120)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)

        cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:5]

        screen_cnt = None
        for c in cnts:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.contourArea(c) > (small_w * 600 * 0.2):
                screen_cnt = approx
                break

        if screen_cnt is not None:
            pts = screen_cnt.reshape(4, 2) * ratio
            rect = order_points_cv(pts)
            (tl, tr, br, bl) = rect
            max_w = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)))
            max_h = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)))

            if max_w > 100 and max_h > 100:
                dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype="float32")
                M = cv2.getPerspectiveTransform(rect, dst)
                warped = cv2.warpPerspective(orig, M, (max_w, max_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                pad_y, pad_x = max(int(max_h * 0.015), 10), max(int(max_w * 0.015), 10)
                cropped = warped[pad_y:max_h - pad_y, pad_x:max_w - pad_x]
                return cv2.copyMakeBorder(cropped, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_CONSTANT, value=[255, 255, 255])
    except Exception:
        pass

    return cv_img

def dewarp_by_column_envelope(img):
    """逐列包络线对齐：捕获文字基线波浪并垂直反向展开，消除文字凹陷与拱起"""
    try:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        roi_h = int(h * 0.40)
        roi_gray = gray[:roi_h, :]

        k_w = max(int(w * 0.08), 35)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k_w, 3))
        _, bin_inv = cv2.threshold(roi_gray, 210, 255, cv2.THRESH_BINARY_INV)

        bin_inv[:, :int(w * 0.08)] = 0
        bin_inv[:, int(w * 0.92):] = 0
        dilated = cv2.dilate(bin_inv, kernel, iterations=2)

        num_cols = 32
        step = w // num_cols
        tops = []

        for i in range(num_cols):
            strip = dilated[:, i * step:(i + 1) * step]
            ys = np.where(strip > 0)[0]
            if len(ys) > 50:
                tops.append(float(np.percentile(ys, 15)))
            else:
                tops.append(np.nan)

        tops = np.array(tops)
        valid = ~np.isnan(tops)
        if np.sum(valid) >= 12:
            xs = np.arange(num_cols)
            tops[~valid] = np.interp(xs[~valid], xs[valid], tops[valid])

            target_baseline = np.min(tops)
            displacements = tops - target_baseline

            if 4.0 < np.max(displacements) < 45.0:
                x_full = np.linspace(0, w, num_cols)
                poly = np.polyfit(x_full, displacements, 3)

                grid_x, grid_y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
                vertical_decay = np.linspace(1.0, 0.2, h, dtype=np.float32)[:, None]
                shift_map = (np.polyval(poly, grid_x) * vertical_decay).astype(np.float32)

                map_y = np.clip(grid_y - shift_map, 0, h - 1).astype(np.float32)
                return cv2.remap(img, grid_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    except Exception:
        pass
    return img

def sanitize_borders_and_covers(img, is_color=True):
    """边缘彻底清洗：消除书皮黄/蓝/黑条纹杂色、装订打孔与透视切边残影"""
    h, w = img.shape[:2]

    # 1. 颜色掩膜：黄色/橙色/棕色与蓝色书皮
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lower_yellow = np.array([5, 20, 30], dtype=np.uint8)
    upper_yellow = np.array([45, 255, 255], dtype=np.uint8)
    mask_y = cv2.inRange(hsv, lower_yellow, upper_yellow)

    lower_blue = np.array([85, 30, 40], dtype=np.uint8)
    upper_blue = np.array([125, 255, 255], dtype=np.uint8)
    mask_b = cv2.inRange(hsv, lower_blue, upper_blue)

    color_cover = cv2.bitwise_or(mask_y, mask_b)

    border_zone = np.zeros((h, w), dtype=np.uint8)
    top_limit = int(h * 0.14)
    bot_limit = int(h * 0.08)
    side_limit = int(w * 0.08)

    border_zone[:top_limit, :] = 255
    border_zone[h - bot_limit:, :] = 255
    border_zone[:, :side_limit] = 255
    border_zone[:, w - side_limit:] = 255

    erase_cover = cv2.bitwise_and(color_cover, border_zone)
    img[erase_cover > 0] = (255, 255, 255)

    # 2. 几何切边保底
    m_y = max(int(h * 0.015), 10)
    m_x = max(int(w * 0.015), 10)
    img[:m_y, :] = (255, 255, 255)
    img[h - m_y:, :] = (255, 255, 255)
    img[:, :m_x] = (255, 255, 255)
    img[:, w - m_x:] = (255, 255, 255)

    # 3. 黑白模式：清空大块边缘黑斑
    if not is_color:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        dirty_mask = (gray < 150).astype(np.uint8) * 255

        edge_band = np.zeros((h, w), dtype=np.uint8)
        edge_band[:int(h * 0.07), :] = 255
        edge_band[h - int(h * 0.05):, :] = 255
        edge_band[:, :int(w * 0.05)] = 255
        edge_band[:, w - int(w * 0.05):] = 255

        suspect = cv2.bitwise_and(dirty_mask, edge_band)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(suspect)
        for i in range(1, num_labels):
            if stats[i, cv2.CC_STAT_AREA] > 80:
                img[labels == i] = (255, 255, 255)

    return img

def camscanner_core_render(img, is_color=True):
    """全能王核心漂白引擎：文字行拉直 + 自适应照度背景除法 + 柔和色彩/笔画增强"""
    # 1. 消除文字波浪扭曲
    img = dewarp_by_column_envelope(img)

    # 2. 背景除法光照补偿
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 35))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
    bg = cv2.GaussianBlur(bg, (31, 31), 0).astype(np.float32) + 1.0

    bg_3d = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
    norm = (img.astype(np.float32) / bg_3d) * 255.0
    norm = np.clip(norm, 0, 255).astype(np.uint8)

    if not is_color:
        norm_gray = cv2.cvtColor(norm, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(norm_gray, (0, 0), 1.0)
        sharp = cv2.addWeighted(norm_gray, 1.5, blur, -0.5, 0)
        res = np.zeros_like(sharp, dtype=np.float32)
        sharp_f = sharp.astype(np.float32)

        res[sharp_f >= 210.0] = 255.0
        mask_mid = (sharp_f >= 130.0) & (sharp_f < 210.0)
        res[mask_mid] = 130.0 + ((sharp_f[mask_mid] - 130.0) / 80.0) * 125.0
        mask_dark = sharp_f < 130.0
        res[mask_dark] = (sharp_f[mask_dark] / 130.0) ** 1.5 * 50.0
        out = cv2.cvtColor(np.clip(res, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        return sanitize_borders_and_covers(out, is_color=False)

    # 彩色增强
    hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    blur_v = cv2.GaussianBlur(v, (0, 0), 1.0)
    v_sharp = cv2.addWeighted(v, 1.4, blur_v, -0.4, 0)
    v_f = v_sharp.astype(np.float32)

    v_res = np.zeros_like(v_f)
    v_res[v_f >= 220.0] = 255.0
    mask_mid = (v_f >= 125.0) & (v_f < 220.0)
    v_res[mask_mid] = 125.0 + ((v_f[mask_mid] - 125.0) / 95.0) * 130.0
    mask_dark = v_f < 125.0
    v_res[mask_dark] = (v_f[mask_dark] / 125.0) ** 1.4 * 60.0
    v_final = np.clip(v_res, 0, 255).astype(np.uint8)

    s_f = s.astype(np.float32)
    s_boost = np.clip(s_f * 1.5, 0, 255).astype(np.uint8)
    s_final = np.where((v_final > 248) & (s_f < 18), 0, s_boost)

    merged = cv2.merge([h, s_final.astype(np.uint8), v_final])
    out = cv2.cvtColor(merged, cv2.COLOR_HSV2BGR)
    return sanitize_borders_and_covers(out, is_color=True)

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes
        img = auto_perspective_warp(img)
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
