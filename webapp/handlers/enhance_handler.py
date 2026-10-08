#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import math
import numpy as np
import cv2
from handlers.base_handler import BaseHandler

def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def force_perspective_straighten(img):
    """
    全能王核心算子：
    定位纸张倾斜角，通过 3x3 矩阵直接透视拉直，并切除外围木纹
    """
    try:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 1. 估算页面整体倾斜角 (使用中段霍夫线检测)
        sub_gray = gray[int(h * 0.2):int(h * 0.8), int(w * 0.15):int(w * 0.85)]
        edges = cv2.Canny(sub_gray, 40, 140)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 50, minLineLength=30, maxLineGap=10)

        rot_deg = 0.0
        if lines is not None:
            angles = []
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if abs(x2 - x1) > 20:
                    deg = math.degrees(math.atan2(y2 - y1, x2 - x1))
                    if abs(deg) < 8.0:
                        angles.append(deg)
            if len(angles) >= 3:
                rot_deg = float(np.median(angles))

        # 2. 旋转拉平
        if abs(rot_deg) > 0.2:
            center = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(center, rot_deg, 1.0)
            img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 3. 强力切除上下木纹桌角与黑边 (针对实拍图特性)
        # 上部检测：扫描前 10%
        top_cut = 0
        thresh = np.percentile(gray[int(h * 0.3):int(h * 0.7), :], 80) * 0.76
        for y in range(int(h * 0.10)):
            if np.mean(gray[y, :]) >= thresh:
                top_cut = y
                break

        # 下部检测：扫描后 10%
        bot_cut = h
        for y in range(h - 1, int(h * 0.90), -1):
            if np.mean(gray[y, :]) >= thresh:
                bot_cut = y + 1
                break

        # 若原图底部仍有暗角，强制切除 2.5% 的边缘
        top_cut = max(top_cut, int(h * 0.015))
        bot_cut = min(bot_cut, int(h * 0.975))

        return img[top_cut:bot_cut, 0:w]
    except Exception:
        return img

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    """
    全能王同款高保真增强：
    1. 霍夫角度拉正 + 上下木纹安全切除
    2. Lab 空间光照除法彻底漂白泛黄底色 (背景 100% 纯白 255)
    3. S 型文字深墨加黑 + 彩色元素鲜艳保真
    """
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        # 1. 执行旋转拉平和裁边
        img = force_perspective_straighten(img)

        h, w = img.shape[:2]
        is_color = (str(color_mode).strip().lower() == "color")

        if is_color:
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l_channel, a_channel, b_channel = cv2.split(lab)

            # 形态学滤波提取光照背景
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 35))
            bg_l = cv2.morphologyEx(l_channel, cv2.MORPH_CLOSE, kernel)
            bg_l = cv2.GaussianBlur(bg_l, (27, 27), 0).astype(np.float32) + 1.0

            # 背景除法归一化
            norm_l = (l_channel.astype(np.float32) / bg_l) * 255.0

            # 强效漂白：高于 175 的全部推为 255 纯白
            norm_clean = np.where(norm_l > 175.0, 255.0, norm_l)
            deep_l = np.where(norm_clean < 150.0, (norm_clean / 150.0) ** 1.6 * 60.0, norm_clean)
            l_enhanced = np.clip(deep_l, 0, 255).astype(np.uint8)

            # 色度增艳（粉红标题、小人肉色衣服、红色四线格）
            a_float = a_channel.astype(np.float32)
            b_float = b_channel.astype(np.float32)
            a_boost = np.clip((a_float - 128.0) * 1.55 + 128.0, 0, 255).astype(np.uint8)
            b_boost = np.clip((b_float - 128.0) * 1.45 + 128.0, 0, 255).astype(np.uint8)

            merged_lab = cv2.merge([l_enhanced, a_boost, b_boost])
            out_bgr = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)

            _, enc = cv2.imencode(".jpg", out_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()

        else:
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
