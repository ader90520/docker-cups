#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
import math
import numpy as np
import cv2
from handlers.base_handler import BaseHandler

# 预留 AI 模型入口，缺失时系统将自动降级使用强大的纯 OpenCV 寻边算法
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
    """双引擎透视拉平：AI 模型优先，纯 OpenCV 形态学强力兜底"""
    if net is not None:
        try:
            orig = cv_img.copy()
            h, w = cv_img.shape[:2]
            blob = cv2.dnn.blobFromImage(cv_img, 1.0/255.0, (256, 256), swapRB=True, crop=False)
            net.setInput(blob)
            out = net.forward()
            
            corners = out[0].reshape(4, 2)
            corners[:, 0] *= w
            corners[:, 1] *= h
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
                warped = cv2.warpPerspective(orig, M, (maxWidth, maxHeight), flags=cv2.INTER_LINEAR)
                pad_y, pad_x = int(maxHeight * 0.015), int(maxWidth * 0.015)
                return warped[pad_y:maxHeight-pad_y, pad_x:maxWidth-pad_x]
        except Exception:
            pass

    # === 纯 OpenCV 强力兜底 (如果没有模型，会自动执行这里) ===
    try:
        orig = cv_img.copy()
        h, w = cv_img.shape[:2]
        ratio = h / 600.0
        small_w = int(w / ratio)
        small = cv2.resize(cv_img, (small_w, 600), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        # 边缘检测与形态学闭运算，将断裂的纸张边缘连成一个整体
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
                warped = cv2.warpPerspective(orig, M, (max_w, max_h), flags=cv2.INTER_LINEAR)
                
                pad_y, pad_x = int(max_h * 0.015), int(max_w * 0.015)
                return warped[pad_y:max_h-pad_y, pad_x:max_w-pad_x]
    except Exception:
        pass

    return cv_img

def camscanner_core_render(img, is_color=True):
    """文档漂白引擎：消除灰斑，自适应光照补偿"""
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
        
        # 柔和非线性映射，防止硬截断引起的笔画虚线断裂
        res[sharp_f >= 220.0] = 255.0
        mask_mid = (sharp_f >= 140.0) & (sharp_f < 220.0)
        res[mask_mid] = 140.0 + ((sharp_f[mask_mid] - 140.0) / 80.0) * 115.0
        mask_dark = sharp_f < 140.0
        res[mask_dark] = (sharp_f[mask_dark] / 140.0) ** 1.5 * 60.0
        return cv2.cvtColor(np.clip(res, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)

    hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    blur_v = cv2.GaussianBlur(v, (0, 0), 1.0)
    v_sharp = cv2.addWeighted(v, 1.5, blur_v, -0.5, 0)
    
    v_res = np.zeros_like(v_sharp, dtype=np.float32)
    v_f = v_sharp.astype(np.float32)
    v_res[v_f >= 220.0] = 255.0
    mask_mid = (v_f >= 140.0) & (v_f < 220.0)
    v_res[mask_mid] = 140.0 + ((v_f[mask_mid] - 140.0) / 80.0) * 115.0
    mask_dark = v_f < 140.0
    v_res[mask_dark] = (v_f[mask_dark] / 140.0) ** 1.5 * 60.0
    
    v_final = np.clip(v_res, 0, 255).astype(np.uint8)
    s_res = np.where(v_final > 240, 0, np.clip(s.astype(np.float32) * 1.6, 0, 255))
    merged = cv2.merge([h, s_res.astype(np.uint8), v_final])
    return cv2.cvtColor(merged, cv2.COLOR_HSV2BGR)

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return raw_bytes
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
