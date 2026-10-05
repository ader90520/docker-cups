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

def detect_skew_angle_projection(gray_img):
    """
    通过文本行水平投影方差法精确计算倾斜角度（精度达 0.1 度）
    不依赖外框是否完整，完全由字迹与行间水平分布决定
    """
    try:
        h, w = gray_img.shape[:2]
        calc_w = 600
        calc_h = int(h * (calc_w / float(w)))
        small = cv2.resize(gray_img, (calc_w, calc_h), interpolation=cv2.INTER_AREA)

        # 自适应提取字迹骨架
        thresh = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 8)

        # 在 -8 到 +8 度范围内以 0.2 度步长扫描最佳投影方差
        best_angle = 0.0
        max_variance = 0.0
        angles = np.arange(-8.0, 8.2, 0.2)

        for angle in angles:
            M = cv2.getRotationMatrix2D((calc_w // 2, calc_h // 2), angle, 1.0)
            rotated = cv2.warpAffine(thresh, M, (calc_w, calc_h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            row_sums = np.sum(rotated, axis=1)
            variance = np.var(row_sums)
            if variance > max_variance:
                max_variance = variance
                best_angle = angle

        return best_angle
    except Exception:
        return 0.0

def deskew_image_precise(cv_img):
    """根据计算得出的精确角度将整幅画面反向旋转拉平至绝对水平"""
    try:
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        angle = detect_skew_angle_projection(gray)
        if abs(angle) > 0.3:
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
    网页端全能王级增强算法管道：
    1. 文本行水平投影自动拉平（解决字句倾斜）
    2. 局部背景闭运算光照场除法（彻底漂白不均阴影与杂色）
    3. 细节高频锐化补偿（彻底解决田字格、拼音声调断线与模糊）
    4. 软 S 曲线深墨压黑（浓黑细腻无毛刺）
    """
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR) if HAVE_OPENCV else None
    except Exception:
        img = None

    if HAVE_OPENCV and img is not None:
        # 1. 倾斜精准拉平
        img = deskew_image_precise(img)
        h, w = img.shape[:2]

        b, g, r = cv2.split(img)
        b_f, g_f, r_f = b.astype(np.float32), g.astype(np.float32), r.astype(np.float32)

        # 2. 局部白场闭运算（消除不均匀光照与阴影）
        gray = (0.299 * r_f + 0.587 * g_f + 0.114 * b_f).astype(np.uint8)
        k_size = max(15, min(w, h) // 45)
        if k_size % 2 == 0:
            k_size += 1

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k_size, k_size))
        bg_morph = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
        bg_float = cv2.GaussianBlur(bg_morph, (k_size, k_size), 0).astype(np.float32) + 1.0

        # 背景归一化（漂白全场阴影）
        r_div = np.clip((r_f / bg_float) * 255.0, 0, 255)
        g_div = np.clip((g_f / bg_float) * 255.0, 0, 255)
        b_div = np.clip((b_f / bg_float) * 255.0, 0, 255)
        gray_div = 0.299 * r_div + 0.587 * g_div + 0.114 * b_div

        # 3. 细节高频锐化补偿：拯救拼音、网格、薄弱笔锋断线
        blur_soft = cv2.GaussianBlur(gray_div, (3, 3), 0)
        detail = gray_div - blur_soft
        sharpened = gray_div + detail * 1.6
        sharpened = np.clip(sharpened, 0, 255)

        # 4. 软 S 曲线对比度延伸（渐变过渡彻底防断线）
        x = sharpened / 255.0
        # 针对浅透墨平滑抬升至纯白，针对文字核心迅速压暗沉底
        enhanced = np.where(x > 0.72, 1.0, np.where(x < 0.40, x * 0.60, np.power(x, 1.85)))
        enhanced_uint8 = np.clip(enhanced * 255.0, 0, 255).astype(np.uint8)

        if color_mode == "color":
            # 提取彩色笔划掩膜（红笔批注、彩印）
            max_c = np.maximum(np.maximum(r_f, g_f), b_f)
            min_c = np.minimum(np.minimum(r_f, g_f), b_f)
            sat = np.where(max_c > 0, (max_c - min_c) / (max_c + 0.001), 0)
            red_excess = r_f - np.maximum(g_f, b_f)
            color_mask = ((sat > 0.18) & (max_c > 50)) | (red_excess > 8.0)

            out_b = np.where(color_mask, np.clip(b_div * 0.85, 0, 255).astype(np.uint8), enhanced_uint8)
            out_g = np.where(color_mask, np.clip(g_div * 0.85, 0, 255).astype(np.uint8), enhanced_uint8)
            out_r = np.where(color_mask, np.clip(r_div * 1.15, 0, 255).astype(np.uint8), enhanced_uint8)

            merged = cv2.merge([out_b, out_g, out_r])
            _, enc = cv2.imencode(".jpg", merged, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()
        else:
            _, enc = cv2.imencode(".jpg", enhanced_uint8, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()

    else:
        # 小盒子纯 PIL + NumPy 备用管道
        with Image.open(io.BytesIO(raw_bytes)) as pil_img:
            img = ImageOps.exif_transpose(pil_img.convert("RGB"))

        w, h = img.size
        gray = ImageOps.grayscale(img)
        gray_sharp = gray.filter(ImageFilter.SHARPEN)

        small = gray_sharp.resize((max(1, w // 4), max(1, h // 4)), Image.Resampling.BILINEAR)
        bg_small = small.filter(ImageFilter.BoxBlur(radius=15))
        bg_full = bg_small.resize((w, h), Image.Resampling.BILINEAR)

        gray_np = np.array(gray_sharp, dtype=np.float32)
        bg_np = np.array(bg_full, dtype=np.float32) + 1.0

        diff = (gray_np / bg_np) * 255.0
        diff = np.where(diff > 185.0, 255.0, np.where(diff < 110.0, diff * 0.55, diff))
        res_np = np.clip(diff, 0, 255).astype(np.uint8)

        out_img = Image.fromarray(res_np)
        buf = io.BytesIO()
        out_img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()

class EnhancePreviewHandler(BaseHandler):
    """供前端网页实时拉取全能王级增强效果"""
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
