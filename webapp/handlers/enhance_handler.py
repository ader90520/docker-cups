#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import math
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler

HAVE_OPENCV = False
try:
    import cv2
    HAVE_OPENCV = True
except Exception:
    HAVE_OPENCV = False

def detect_skew_angle_robust(gray_img):
    """
    鲁棒性文字行倾斜检测：
    优先分析页面中段文字密集区的水平条状形态学连通块方向
    """
    try:
        h, w = gray_img.shape[:2]
        # 裁剪中段 70% 区域，避开书页边框与顶部留白干扰
        y1, y2 = int(h * 0.15), int(h * 0.85)
        x1, x2 = int(w * 0.05), int(w * 0.95)
        crop = gray_img[y1:y2, x1:x2]

        calc_w = 800
        calc_h = int(crop.shape[0] * (calc_w / float(crop.shape[1])))
        small = cv2.resize(crop, (calc_w, calc_h), interpolation=cv2.INTER_AREA)

        # Otsu 自适应二值化提取字迹
        blurred = cv2.GaussianBlur(small, (5, 5), 0)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # 形态学横向膨胀，把每个单词/汉字连成长水平条
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
        dilated = cv2.dilate(thresh, kernel, iterations=2)

        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        angles = []

        for c in contours:
            if cv2.contourArea(c) < 300:
                continue
            rect = cv2.minAreaRect(c)
            (cx, cy), (rw, rh), angle = rect
            if rw < rh:
                rw, rh = rh, rw
                angle += 90.0
            
            # 过滤非横向条状物
            if rw / float(rh + 0.001) > 2.5:
                # 规范化角度到 [-45, 45]
                while angle > 45.0: angle -= 90.0
                while angle < -45.0: angle += 90.0
                if abs(angle) < 15.0:
                    angles.append(angle)

        if len(angles) >= 5:
            # 选用中位数，抗孤立噪点干扰
            median_angle = float(np.median(angles))
            return median_angle
    except Exception:
        pass
    return 0.0

def deskew_image_precise(cv_img):
    """精确反向拉平旋转图像"""
    try:
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        angle = detect_skew_angle_robust(gray)
        if abs(angle) > 0.4:
            h, w = cv_img.shape[:2]
            center = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated = cv2.warpAffine(cv_img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
            return rotated
    except Exception:
        pass
    return cv_img

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    """
    全能王级深度清晰化引擎：
    1. 文本行中段自动精确拉平
    2. 多尺度局部白场除法（消除不均匀光照与阴影）
    3. 笔画核心强力压黑，杜绝发虚发浅
    4. 颜色模式全保真：彩色笔画鲜艳保留，绝不变灰
    """
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR) if HAVE_OPENCV else None
    except Exception:
        img = None

    if HAVE_OPENCV and img is not None:
        # 1. 水平拉正
        img = deskew_image_precise(img)
        h, w = img.shape[:2]

        b, g, r = cv2.split(img)
        b_f, g_f, r_f = b.astype(np.float32), g.astype(np.float32), r.astype(np.float32)

        # 2. 估计白场背景 (小核保留细文字反差，大核吸收背景光斑)
        gray = (0.299 * r_f + 0.587 * g_f + 0.114 * b_f).astype(np.uint8)
        
        # 局部高斯滤波作为背景白场场强
        bg_blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=15, sigmaY=15).astype(np.float32) + 1.0
        
        # 背景除法归一化（白纸彻底推向 255）
        norm = (gray.astype(np.float32) / bg_blur) * 255.0
        norm = np.clip(norm, 0, 255)

        # 3. 增强深色墨迹沉降（解决字体虚、浅）
        # < 175 的细节全部线性向下强力拉深，使文字浓郁黑亮
        text_deep = np.where(norm < 175.0, (norm / 175.0) ** 1.6 * 85.0, norm)
        # > 190 的背透灰影与底色彻底切除为纯白 255
        text_deep = np.where(text_deep > 185.0, 255.0, text_deep)
        text_uint8 = np.clip(text_deep, 0, 255).astype(np.uint8)

        # 4. 判断色彩模式并合成输出
        is_color_requested = (color_mode.lower() == "color")

        if is_color_requested:
            # 严格提取彩色区域（如红字、批改、彩图）
            # 转 HSV 检测饱和度
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            sat = hsv[:, :, 1]
            val = hsv[:, :, 2]
            
            # 只要饱和度大于 25，且亮度不是极端黑，认定为彩色笔迹
            color_mask = (sat > 25) & (val > 35)

            # 漂白后的底色通道
            enhanced_3ch = cv2.merge([text_uint8, text_uint8, text_uint8])
            
            # 彩色区域提亮增艳，背景采用漂白加黑后的文本底色
            color_boost = cv2.convertScaleAbs(img, alpha=1.15, beta=10)
            out_img = np.where(color_mask[:, :, None], color_boost, enhanced_3ch)

            _, enc = cv2.imencode(".jpg", out_img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()
        else:
            # 纯黑白模式：直接输出极度深黑单通道
            _, enc = cv2.imencode(".jpg", text_uint8, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()

    else:
        # 小盒子纯 PIL + NumPy 备用管道
        with Image.open(io.BytesIO(raw_bytes)) as pil_img:
            img = ImageOps.exif_transpose(pil_img.convert("RGB"))

        w, h = img.size
        gray = ImageOps.grayscale(img)
        bg = gray.filter(ImageFilter.GaussianBlur(radius=15))
        
        gray_np = np.array(gray, dtype=np.float32)
        bg_np = np.array(bg, dtype=np.float32) + 1.0

        diff = (gray_np / bg_np) * 255.0
        # 强力压黑加深
        diff = np.where(diff < 170.0, (diff / 170.0) ** 1.6 * 85.0, diff)
        diff = np.where(diff > 185.0, 255.0, diff)
        res_np = np.clip(diff, 0, 255).astype(np.uint8)

        out_img = Image.fromarray(res_np)
        buf = io.BytesIO()
        out_img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()

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
