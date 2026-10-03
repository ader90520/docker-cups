#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import numpy as np
from PIL import Image, ImageOps
from handlers.base_handler import BaseHandler

def process_paper_lightweight(img_bytes, color_mode="monochrome"):
    """
    轻量级去黑底与字迹加黑算法：
    纯 PIL + NumPy 矩阵向量化加速，0 额外依赖，极低内存消耗
    """
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return img_bytes

    w, h = img.size

    # 1. 降采样背景光照估算（消除不均匀光照与阴影）
    scale = 400.0 / max(w, h)
    small_w, small_h = max(1, int(w * scale)), max(1, int(h * scale))
    small = img.resize((small_w, small_h), Image.Resampling.BILINEAR)

    # 提取亮度灰度图并用最大值滤波提取白场背景
    gray_small = ImageOps.grayscale(small)
    bg_small = gray_small.filter(Image.MaxFilter(size=19))
    bg_full = bg_small.resize((w, h), Image.Resampling.BILINEAR)

    img_np = np.array(img, dtype=np.float32)
    gray_full = np.array(ImageOps.grayscale(img), dtype=np.float32)
    bg_np = np.array(bg_full, dtype=np.float32)
    bg_np[bg_np < 1.0] = 1.0

    # 2. 背景除法归一化：消除拍照暗角
    diff = (gray_full / bg_np) * 255.0
    diff = np.clip(diff, 0, 255)

    # 3. 增强文本反差与加黑（S 型曲线拉伸）
    diff = np.where(diff > 185, 255.0, diff)
    diff = np.where(diff < 115, diff * 0.65, diff)
    enhanced_luma = np.clip(diff, 0, 255).astype(np.uint8)

    # 4. 色彩模式判断与保留
    if color_mode == "color":
        r, g, b = img_np[:, :, 0], img_np[:, :, 1], img_np[:, :, 2]
        max_c = np.maximum(np.maximum(r, g), b)
        min_c = np.minimum(np.minimum(r, g), b)
        sat = np.where(max_c > 0, (max_c - min_c) / (max_c + 0.001), 0)
        
        # 饱和度高的区域保留原彩色（红笔、印章、彩图），其余灰色背景纯白化
        color_mask = (sat > 0.20) & (max_c > 45)
        color_mask = color_mask[:, :, None]

        gray_3ch = np.repeat(enhanced_luma[:, :, None], 3, axis=2)
        out_np = np.where(color_mask, img_np * 1.1, gray_3ch)
        out_np = np.clip(out_np, 0, 255).astype(np.uint8)
        out_img = Image.fromarray(out_np)
    else:
        out_img = Image.fromarray(enhanced_luma)

    buf = io.BytesIO()
    out_img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()

class EnhancePreviewHandler(BaseHandler):
    """供前端网页实时拉取去黑底效果的预览接口"""
    def post(self):
        try:
            files = self.request.files.get("file", [])
            color_mode = self.get_argument("color_mode", "monochrome").strip()
            
            if not files:
                self.set_status(400)
                self.write("未收到预览文件")
                return

            raw_bytes = files[0]["body"]
            processed_bytes = process_paper_lightweight(raw_bytes, color_mode=color_mode)

            self.set_header("Content-Type", "image/jpeg")
            self.set_header("Cache-Control", "no-cache")
            self.write(processed_bytes)
            self.finish()
        except Exception as e:
            self.set_status(500)
            self.write(str(e))
