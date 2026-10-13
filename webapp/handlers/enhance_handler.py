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

MODELS_PATH = "/opt/webapp/models"
MODEL_PATH = os.path.join(MODELS_PATH, "doc_corner.onnx")
net = None

def init_onnx_model():
    global net
    if not HAVE_OPENCV:
        net = None
        return
    if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 1024 * 1024:
        try:
            loaded = cv2.dnn.readNetFromONNX(MODEL_PATH)
            loaded.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            loaded.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            net = loaded
        except Exception:
            net = None
    else:
        net = None

init_onnx_model()

def camscanner_core_render(img, is_color=False):
    """大核形态学光照估计除法归一化"""
    if img is None:
        return None
    h, w = img.shape[:2]
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (51, 51))
    bg_l = cv2.morphologyEx(l_channel, cv2.MORPH_CLOSE, kernel)
    bg_l = cv2.GaussianBlur(bg_l, (45, 45), 0).astype(np.float32) + 1.0

    norm_l = np.clip((l_channel.astype(np.float32) / bg_l) * 255.0, 0, 255).astype(np.uint8)

    lut_text = np.zeros(256, dtype=np.uint8)
    for i in range(256):
        if i >= 185:
            lut_text[i] = 255
        elif i <= 130:
            lut_text[i] = int((i / 130.0) ** 1.35 * 35.0)
        else:
            lut_text[i] = int(35.0 + ((i - 130.0) / 55.0) * (255.0 - 35.0))
    enhanced_l = cv2.LUT(norm_l, lut_text)

    if not is_color:
        return cv2.cvtColor(enhanced_l, cv2.COLOR_GRAY2BGR)

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    _, s_c, v_c = cv2.split(hsv)
    color_mask = (s_c > 38) & (v_c > 45)
    color_mask = cv2.GaussianBlur(color_mask.astype(np.uint8) * 255, (5, 5), 0) > 100

    a_new = np.where(color_mask, a_channel, 128)
    b_new = np.where(color_mask, b_channel, 128)
    l_final = np.where(color_mask & (enhanced_l < 240), np.clip(l_channel * 1.1, 0, 245).astype(np.uint8), enhanced_l)

    lab_merged = cv2.merge([l_final, a_new.astype(np.uint8), b_new.astype(np.uint8)])
    out_bgr = cv2.cvtColor(lab_merged, cv2.COLOR_LAB2BGR)
    out_bgr[enhanced_l >= 252] = [255, 255, 255]
    return out_bgr

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
