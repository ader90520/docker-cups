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

def detect_and_crop_paper_content(img):
    """
    基于亮度投影分析精准切除非纸面杂质（彻底清除上下桌面、杂物、黑边）
    同时确保 100% 不伤及纸面内部文字
    """
    if img is None:
        return img

    h, w = img.shape[:2]
    # 缩小至 300px 计算投影
    scale = 300.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    # 计算水平方向（行）平均亮度
    row_means = np.mean(gray, axis=1)
    # 计算纸张核心区域的大致亮度
    mid_brightness = np.median(row_means)
    # 门槛设定：低于中间亮度 65% 的行判定为深色桌面/阴影杂质
    thresh_val = max(55.0, mid_brightness * 0.65)

    # 从顶部向下寻找白纸起始行
    top = 0
    for r in range(len(row_means)):
        if row_means[r] >= thresh_val:
            top = r
            break

    # 从底部向上寻找白纸结束行
    bottom = len(row_means) - 1
    for r in range(len(row_means) - 1, -1, -1):
        if row_means[r] >= thresh_val:
            bottom = r
            break

    # 计算垂直方向（列）平均亮度
    col_means = np.mean(gray, axis=0)
    left = 0
    for c in range(len(col_means)):
        if col_means[c] >= thresh_val:
            left = c
            break

    right = len(col_means) - 1
    for c in range(len(col_means) - 1, -1, -1):
        if col_means[c] >= thresh_val:
            right = c
            break

    # 映射回原图坐标
    y1 = int(top / scale)
    y2 = int(bottom / scale)
    x1 = int(left / scale)
    x2 = int(right / scale)

    # 安全保护：如果切掉面积过大，回退为兜底微切 1.5%
    if (y2 - y1) < h * 0.5 or (x2 - x1) < w * 0.5:
        pad_x, pad_y = int(w * 0.015), int(h * 0.015)
        y1, y2, x1, x2 = pad_y, h - pad_y, pad_x, w - pad_x

    # 在裁切边界处额外内收 4 个像素，彻底切断残留毛刺
    y1 = min(h - 10, y1 + 4)
    y2 = max(y1 + 10, y2 - 4)
    x1 = min(w - 10, x1 + 4)
    x2 = max(x1 + 10, x2 - 4)

    cropped = img[y1:y2, x1:x2]
    return cv2.copyMakeBorder(cropped, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=[255, 255, 255])

def render_crisp_pure_document(img, is_color=False, density="dark"):
    """
    全能王级自然保真白底算法（深度加黑与笔骨饱满优化）：
    1. 彻底解决文字被除法冲淡泛白的问题
    2. 显著拉开三档文字浓黑度差异，所选即所见
    3. 保留抗锯齿平滑边缘，文字圆润饱满绝不断笔
    """
    h, w = img.shape[:2]

    # 1. 超轻量尺度提取漫反射平滑背景底色
    tiny_scale = 320.0 / max(h, w)
    tiny_img = cv2.resize(img, (int(w * tiny_scale), int(h * tiny_scale)), interpolation=cv2.INTER_NEAREST)
    bg_tiny = cv2.GaussianBlur(tiny_img, (0, 0), sigmaX=8, sigmaY=8)
    bg = cv2.resize(bg_tiny, (w, h), interpolation=cv2.INTER_LINEAR)

    # 2. 照度除法归一（消除大面积黄色纸底与背光暗角）
    norm = cv2.divide(img, bg, scale=255)

    if not is_color:
        gray = np.min(norm, axis=2).astype(np.float32)

        # 显著拉大档位反差与黑场沉降点，确保切换档位时肉眼变化极其明显
        if density == "ultra":
            black_point = 55.0      # 大幅下沉黑场，浅灰字直接变纯黑
            white_point = 195.0     # 强力阻断透背印痕
            gamma = 0.65            # 显著加深
            extra_thicken = True    # 笔划物理增粗 0.5 像素
        elif density == "normal":
            black_point = 10.0      # 温和自然
            white_point = 220.0
            gamma = 0.95
            extra_thicken = False
        else:  # dark (推荐档)
            black_point = 35.0      # 适中浓黑饱满
            white_point = 210.0
            gamma = 0.80
            extra_thicken = False

        # 3. 非线性自适应色阶映射：黑场强力下压，白场推平，杜绝文字整体提亮泛白
        scaled = np.clip((gray - black_point) / (white_point - black_point), 0.0, 1.0)
        v_out = np.power(scaled, gamma) * 255.0
        result = np.clip(v_out, 0, 255).astype(np.uint8)
        result[result >= 235] = 255

        # 4. 极度深黑档位：对字骨进行物理增粗，彻底根治断笔与发虚
        if extra_thicken:
            kernel = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8)
            eroded = cv2.erode(result, kernel, iterations=1)
            result = cv2.addWeighted(result, 0.4, eroded, 0.6, 0)

        return cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    else:
        # 彩色模式：色彩饱和度保真 + 底色漂白
        hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV).astype(np.float32)
        s = hsv[:, :, 1]
        v = hsv[:, :, 2]

        color_mask = (s > 15) & (v > 35)
        hsv[:, :, 1] = np.where(color_mask, np.clip(s * 1.30, 0, 255), s)
        v_stretched = np.clip((v - 25.0) / (215.0 - 25.0) * 255.0, 0, 255)
        hsv[:, :, 2] = np.where(color_mask, v, v_stretched)

        result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        gray_chk = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
        result[gray_chk >= 235] = [255, 255, 255]
        return result

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome", density="dark"):
    try:
        if not HAVE_OPENCV:
            return raw_bytes
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        # 1. 先进行亮度投影切边，彻底切除上下的桌面和杂物
        img = detect_and_crop_paper_content(img)

        # 2. 预览分辨率控制在 1600px，保证 PC 端视窗清晰锐利
        h, w = img.shape[:2]
        if max(h, w) > 1600:
            scale = 1600.0 / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        is_color = (str(color_mode).strip().lower() == "color")
        result = render_crisp_pure_document(img, is_color=is_color, density=density)
        if result is None:
            result = img

        _, enc = cv2.imencode(".jpg", result, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return enc.tobytes()
    except Exception:
        return raw_bytes

class EnhancePreviewHandler(BaseHandler):
    def post(self):
        try:
            files = self.request.files.get("file", [])
            color_mode = self.get_argument("color_mode", "monochrome").strip()
            density = self.get_argument("density", "dark").strip().lower()

            if not files:
                self.set_status(400)
                self.write("未收到预览数据")
                return

            raw_bytes = files[0]["body"]
            processed_bytes = enhance_camscanner_precise(raw_bytes, color_mode=color_mode, density=density)
            self.set_header("Content-Type", "image/jpeg")
            self.set_header("Content-Length", str(len(processed_bytes)))
            self.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.write(processed_bytes)
            self.finish()
        except Exception as e:
            self.set_status(500)
            self.write(str(e))
