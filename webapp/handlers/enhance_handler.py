#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import math
import numpy as np
import cv2
from handlers.base_handler import BaseHandler

def auto_crop_borders(img):
    """
    全能王核心算子 1：
    上下四周边界暗区扫描，切除顶部、底部露出的桌面木纹与阴影
    """
    try:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 获取整张纸面中部的白纸特征亮度参考值 (75分位)
        ref_light = float(np.percentile(gray[int(h * 0.25):int(h * 0.75), int(w * 0.2):int(w * 0.8)], 75))
        thresh = ref_light * 0.82

        # 1. 扫描顶部 (最多扫描 15%)
        top_cut = 0
        top_limit = int(h * 0.15)
        for y in range(top_limit):
            row_mean = np.mean(gray[y, :])
            if row_mean >= thresh:
                top_cut = y
                break

        # 2. 扫描底部 (最多扫描 15%)
        bot_cut = h
        bot_limit = int(h * 0.85)
        for y in range(h - 1, bot_limit, -1):
            row_mean = np.mean(gray[y, :])
            if row_mean >= thresh:
                bot_cut = y + 1
                break

        # 3. 扫描左右边缘 (最多各扫描 5%)
        left_cut = 0
        left_limit = int(w * 0.05)
        for x in range(left_limit):
            col_mean = np.mean(gray[:, x])
            if col_mean >= thresh:
                left_cut = x
                break

        right_cut = w
        right_limit = int(w * 0.95)
        for x in range(w - 1, right_limit, -1):
            col_mean = np.mean(gray[:, x])
            if col_mean >= thresh:
                right_cut = x + 1
                break

        # 安全约束，防止误切正文
        top_cut = min(top_cut, int(h * 0.10))
        bot_cut = max(bot_cut, int(h * 0.90))
        left_cut = min(left_cut, int(w * 0.04))
        right_cut = max(right_cut, int(w * 0.96))

        return img[top_cut:bot_cut, left_cut:right_cut]
    except Exception:
        pass
    return img

def deskew_by_projection(img):
    """
    全能王核心算子 2：
    基于印刷文本行密集区 Radon 投影方差的自适应水平拉正。
    即使课本上全是碎字、没有长线，也能以 0.2° 步长高精度修正倾斜。
    """
    try:
        h, w = img.shape[:2]
        # 裁剪正文中部 60% 区域作为计算样本
        sub = img[int(h * 0.2):int(h * 0.8), int(w * 0.15):int(w * 0.85)]
        sub_gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
        
        # 降采样加速计算
        calc_w = 400
        calc_h = int(sub_gray.shape[0] * (calc_w / float(sub_gray.shape[1])))
        small = cv2.resize(sub_gray, (calc_w, calc_h), interpolation=cv2.INTER_AREA)

        # 二值化提取文字笔画
        thresh = np.mean(small) - 15
        binary = (small < thresh).astype(np.float32)

        best_angle = 0.0
        max_var = 0.0
        center = (calc_w // 2, calc_h // 2)

        # 在 -6° 到 +6° 范围内高频精扫
        for angle in np.arange(-6.0, 6.2, 0.2):
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            rot = cv2.warpAffine(binary, M, (calc_w, calc_h), flags=cv2.INTER_NEAREST)
            row_sums = np.sum(rot, axis=1)
            v = np.var(row_sums)
            if v > max_var:
                max_var = v
                best_angle = angle

        if abs(best_angle) >= 0.2:
            real_center = (w // 2, h // 2)
            M_real = cv2.getRotationMatrix2D(real_center, best_angle, 1.0)
            rotated = cv2.warpAffine(img, M_real, (w, h), flags=cv2.INTER_CUBIC,
                                     borderMode=cv2.BORDER_CONSTANT,
                                     borderValue=(255, 255, 255))
            return rotated
    except Exception:
        pass
    return img

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    """
    扫描全能王同款图像增强处理流：
    1. 上下四周桌面木纹/黑边自动切除
    2. 投影方差高精度文字行水平展平
    3. 自适应大核背景照度除法 + 白场极限归一化 (背景彻底纯白 255)
    4. S型深墨沉降 + Lab 彩色饱和度高保真 (粉红单元框、小人肉色、四线格鲜艳全彩)
    """
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        # 1. 切除上下木纹暗边
        img = auto_crop_borders(img)

        # 2. 文本水平基准线拉平
        img = deskew_by_projection(img)

        h, w = img.shape[:2]
        is_color = (str(color_mode).strip().lower() == "color")

        if is_color:
            # 彩色模式：转为 Lab 空间，独立分离光照与色彩
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l_channel, a_channel, b_channel = cv2.split(lab)

            # 估计白场背景亮度
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (31, 31))
            bg_l = cv2.morphologyEx(l_channel, cv2.MORPH_CLOSE, kernel)
            bg_l = cv2.GaussianBlur(bg_l, (25, 25), 0).astype(np.float32) + 1.0

            # 照度除法漂白
            norm_l = (l_channel.astype(np.float32) / bg_l) * 255.0

            # 全能王核心白平衡映射：高于 180 的底色直接推向 255 纯白亮透
            norm_clean = np.where(norm_l > 180.0, 255.0, norm_l)
            # 低于 155 的字迹沉降浓墨黑
            deep_l = np.where(norm_clean < 155.0, (norm_clean / 155.0) ** 1.65 * 60.0, norm_clean)
            l_enhanced = np.clip(deep_l, 0, 255).astype(np.uint8)

            # 色度增艳（粉红标题圆圈、人物浅肉色衣服、红色四线格）
            a_float = a_channel.astype(np.float32)
            b_float = b_channel.astype(np.float32)
            a_boost = np.clip((a_float - 128.0) * 1.55 + 128.0, 0, 255).astype(np.uint8)
            b_boost = np.clip((b_float - 128.0) * 1.45 + 128.0, 0, 255).astype(np.uint8)

            merged_lab = cv2.merge([l_enhanced, a_boost, b_boost])
            out_bgr = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)

            _, enc = cv2.imencode(".jpg", out_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()

        else:
            # 黑白试卷模式
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (31, 31))
            bg_gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
            bg_gray = cv2.GaussianBlur(bg_gray, (25, 25), 0).astype(np.float32) + 1.0

            norm = (gray.astype(np.float32) / bg_gray) * 255.0
            norm_clean = np.where(norm > 178.0, 255.0, norm)
            deep = np.where(norm_clean < 150.0, (norm_clean / 150.0) ** 1.7 * 55.0, norm_clean)
            out_mono = np.clip(deep, 0, 255).astype(np.uint8)

            _, enc = cv2.imencode(".jpg", out_mono, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
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
