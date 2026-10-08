#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import math
import numpy as np
import cv2
from handlers.base_handler import BaseHandler

def order_points(pts):
    """排序四边形顶点：左上、右上、右下、左下"""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def perspective_warp_document(img):
    """
    全能王核心算子：四角透视展平（强制切除外围木纹并拉直矩形）
    """
    try:
        h, w = img.shape[:2]
        # 降采样加速边缘检测
        calc_h = 800
        ratio = h / float(calc_h)
        calc_w = int(w / ratio)
        small = cv2.resize(img, (calc_w, calc_h), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)

        # 结合 Canny 与自适应阈值，强化纸张与木纹边缘
        edges = cv2.Canny(blur, 40, 150)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        dilated = cv2.dilate(edges, kernel, iterations=2)

        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img

        # 找面积最大的凸轮廓（即纸张主体）
        contours = sorted(contours, key=cv2.contourArea, reverse=True)
        target_box = None

        for c in contours[:3]:
            area = cv2.contourArea(c)
            # 纸张面积一般占画面的 40% 以上
            if area > (calc_w * calc_h * 0.40):
                hull = cv2.convexHull(c)
                rot_rect = cv2.minAreaRect(hull)
                box = cv2.boxPoints(rot_rect)
                target_box = box
                break

        if target_box is not None:
            # 还原到原图真实尺寸坐标
            pts = target_box * ratio
            rect = order_points(pts)
            (tl, tr, br, bl) = rect

            # 计算展开后的目标矩形尺寸
            w_top = np.hypot(tr[0] - tl[0], tr[1] - tl[1])
            w_bot = np.hypot(br[0] - bl[0], br[1] - bl[1])
            max_w = int(max(w_top, w_bot))

            h_left = np.hypot(bl[0] - tl[0], bl[1] - tl[1])
            h_right = np.hypot(br[0] - tr[0], br[1] - tr[1])
            max_h = int(max(h_left, h_right))

            # 约束尺寸合理性，防止畸变
            if max_w > w * 0.70 and max_h > h * 0.70:
                dst = np.array([
                    [0, 0],
                    [max_w - 1, 0],
                    [max_w - 1, max_h - 1],
                    [0, max_h - 1]
                ], dtype="float32")

                M = cv2.getPerspectiveTransform(rect, dst)
                warped = cv2.warpPerspective(img, M, (max_w, max_h), flags=cv2.INTER_CUBIC,
                                             borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
                return warped
    except Exception:
        pass
    return img

def remove_residual_margins(img):
    """
    次级清洗：切除四周残留的微弱暗边与暗角木纹
    """
    try:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mid_val = np.percentile(gray[int(h * 0.3):int(h * 0.7), int(w * 0.2):int(w * 0.8)], 80)
        thresh = mid_val * 0.78

        top, bot, left, right = 0, h, 0, w

        # 顶端检测
        for y in range(int(h * 0.08)):
            if np.mean(gray[y, :]) >= thresh:
                top = y
                break

        # 底端检测
        for y in range(h - 1, int(h * 0.92), -1):
            if np.mean(gray[y, :]) >= thresh:
                bot = y + 1
                break

        # 左右检测
        for x in range(int(w * 0.04)):
            if np.mean(gray[:, x]) >= thresh:
                left = x
                break

        for x in range(w - 1, int(w * 0.96), -1):
            if np.mean(gray[:, x]) >= thresh:
                right = x + 1
                break

        top = min(top, int(h * 0.06))
        bot = max(bot, int(h * 0.94))
        left = min(left, int(w * 0.03))
        right = max(right, int(w * 0.97))

        return img[top:bot, left:right]
    except Exception:
        pass
    return img

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    """
    全能王同款处理流：
    1. OpenCV 四角凸包非线性透视变换（强制拉平展直）
    2. 残留暗边轻微切除
    3. Lab 空间光照除法彻底漂白（白底推至 255 纯白亮透）
    4. S型深墨下潜 + 彩色元素增艳保真
    """
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        # 1. 核心透视展开，将倾斜的梯形纸张展平为矩形
        img = perspective_warp_document(img)

        # 2. 扫尾切除四周暗边
        img = remove_residual_margins(img)

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

            # 白场彻底漂白：大于 175 的全部推至 255 纯白亮透
            norm_clean = np.where(norm_l > 175.0, 255.0, norm_l)
            # 文字深墨浓黑
            deep_l = np.where(norm_clean < 150.0, (norm_clean / 150.0) ** 1.6 * 60.0, norm_clean)
            l_enhanced = np.clip(deep_l, 0, 255).astype(np.uint8)

            # 饱和度增艳：对手掌肉色、粉红小圆圈、红色四线格高保真
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
