#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import math
import numpy as np
import cv2
from handlers.base_handler import BaseHandler

def remove_wood_and_dark_margins(img):
    """
    全能王核心算子 1：
    上下四周边界暗区扫描，彻底切掉顶部、底部露出的深色木纹桌面与暗角
    """
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 纸张主体白场的基准亮度（取中间区域的 80 分位数）
    mid_gray = gray[int(h * 0.25):int(h * 0.75), int(w * 0.2):int(w * 0.8)]
    paper_white = float(np.percentile(mid_gray, 80))
    thresh = paper_white * 0.78  # 凡是低于白纸亮度 78% 的边缘判定为木纹或黑边

    # 1. 扫描顶部木纹 (最多扫描 15%)
    top = 0
    for y in range(int(h * 0.15)):
        if np.mean(gray[y, :]) >= thresh:
            top = y
            break

    # 2. 扫描底部木纹 (最多扫描 15%)
    bot = h
    for y in range(h - 1, int(h * 0.85), -1):
        if np.mean(gray[y, :]) >= thresh:
            bot = y + 1
            break

    # 3. 扫描左右黑边 (最多扫描 6%)
    left = 0
    for x in range(int(w * 0.06)):
        if np.mean(gray[:, x]) >= thresh:
            left = x
            break

    right = w
    for x in range(w - 1, int(w * 0.94), -1):
        if np.mean(gray[:, x]) >= thresh:
            right = x + 1
            break

    # 保底：若原图底部确实有木纹（实拍图常见），至少安全切掉底部极边缘
    if (h - bot) < int(h * 0.02):
        # 探测最下部 3% 是否有连续暗行
        if np.mean(gray[int(h * 0.97):, :]) < thresh:
            bot = int(h * 0.96)

    # 裁剪生效（限制在合理正文范围）
    top = min(top, int(h * 0.10))
    bot = max(bot, int(h * 0.92))
    left = min(left, int(w * 0.04))
    right = max(right, int(w * 0.96))

    return img[top:bot, left:right]

def deskew_text_lines(img):
    """
    全能王核心算子 2：
    基于文本行水平投影方差法的自适应水平拉正。
    专门纠正 -5° 到 +5° 的拍摄微倾斜。
    """
    h, w = img.shape[:2]
    # 截取中部 60% 区域，避开四周边缘与二维码
    sub = img[int(h * 0.2):int(h * 0.8), int(w * 0.15):int(w * 0.8)]
    sub_gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)

    # 缩小加速计算
    calc_w = 400
    calc_h = int(sub_gray.shape[0] * (calc_w / float(sub_gray.shape[1])))
    small = cv2.resize(sub_gray, (calc_w, calc_h), interpolation=cv2.INTER_AREA)

    # 二值化提取正文字迹
    thresh = float(np.mean(small)) - 15.0
    binary = (small < thresh).astype(np.float32)

    best_angle = 0.0
    max_var = 0.0
    center = (calc_w // 2, calc_h // 2)

    # 以 0.25° 步长高精扫描
    for angle in np.arange(-4.5, 4.75, 0.25):
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rot = cv2.warpAffine(binary, M, (calc_w, calc_h), flags=cv2.INTER_NEAREST)
        row_sums = np.sum(rot, axis=1)
        v = float(np.var(row_sums))
        if v > max_var:
            max_var = v
            best_angle = angle

    if abs(best_angle) >= 0.25:
        real_center = (w // 2, h // 2)
        M_real = cv2.getRotationMatrix2D(real_center, best_angle, 1.0)
        rotated = cv2.warpAffine(img, M_real, (w, h), flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_CONSTANT,
                                 borderValue=(255, 255, 255))
        return rotated
    return img

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    """
    全能王同款处理流：
    1. 彻底切除上下木纹暗边
    2. 文字行水平纠偏拉正
    3. 背景彻底漂白（白场推向 255 纯白）
    4. 字迹加深 + 彩色饱和度提升
    """
    nparr = np.frombuffer(raw_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return raw_bytes

    # 1. 切除木纹与阴影暗边
    img = remove_wood_and_dark_margins(img)

    # 2. 纠正页面倾斜，水平拉直
    img = deskew_text_lines(img)

    h, w = img.shape[:2]
    is_color = (str(color_mode).strip().lower() == "color")

    if is_color:
        # 彩色模式：转 Lab 空间解耦亮度与色彩通道
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)

        # 形态学滤波提取光照背景
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 35))
        bg_l = cv2.morphologyEx(l_channel, cv2.MORPH_CLOSE, kernel)
        bg_l = cv2.GaussianBlur(bg_l, (27, 27), 0).astype(np.float32) + 1.0

        # 背景归一化除法
        norm_l = (l_channel.astype(np.float32) / bg_l) * 255.0

        # 全能王强效白场截断：只要归一化后大于 175，直接推向 255 纯白亮透
        norm_clean = np.where(norm_l > 175.0, 255.0, norm_l)
        # 低于 150 的文字笔画，加黑压暗
        deep_l = np.where(norm_clean < 150.0, (norm_clean / 150.0) ** 1.6 * 60.0, norm_clean)
        l_enhanced = np.clip(deep_l, 0, 255).astype(np.uint8)

        # 饱和度增强：对手掌肉色、粉红标题框、红色四线格适度增艳
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
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 35))
        bg_gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
        bg_gray = cv2.GaussianBlur(bg_gray, (27, 27), 0).astype(np.float32) + 1.0

        norm = (gray.astype(np.float32) / bg_gray) * 255.0
        norm_clean = np.where(norm > 175.0, 255.0, norm)
        deep = np.where(norm_clean < 150.0, (norm_clean / 150.0) ** 1.7 * 55.0, norm_clean)
        out_mono = np.clip(deep, 0, 255).astype(np.uint8)

        _, enc = cv2.imencode(".jpg", out_mono, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return enc.tobytes()

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
