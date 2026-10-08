#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import math
import numpy as np
import cv2
from handlers.base_handler import BaseHandler

def detect_and_crop_wood_border(img):
    """
    全能王核心算子 1 (OpenCV)：
    精准切除顶部露出的桌面斜木纹与拍照四周黑边
    """
    try:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 扫描顶部前 25% 区域，检测深色木纹向浅色白纸的跳变
        top_h = int(h * 0.25)
        top_gray = gray[:top_h, :]
        row_means = np.mean(top_gray, axis=1)
        
        # 统计中下部白纸主体的特征亮度
        paper_ref = np.percentile(gray[int(h * 0.3):int(h * 0.8), :], 75)
        
        crop_top = 0
        for y in range(top_h):
            # 当行平均亮度达到白纸参考亮度的 88% 以上，判定进入了正式正文区域
            if row_means[y] >= (paper_ref * 0.88):
                crop_top = y
                break
                
        # 若检测到顶部确实有木纹/深色暗区，执行裁切
        if 0 < crop_top < int(h * 0.18):
            return img[crop_top:h, 0:w]
    except Exception:
        pass
    return img

def detect_and_deskew_text(img):
    """
    全能王核心算子 2 (OpenCV 霍夫直线)：
    中段印刷课文基准线精细水平纠偏，自动拉平 -8° 到 +8° 的微小倾斜
    """
    try:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 截取中段文字密集区（避开顶部和底部装饰）
        sub_gray = gray[int(h * 0.2):int(h * 0.8), int(w * 0.15):int(w * 0.85)]
        edges = cv2.Canny(sub_gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=70, 
                                minLineLength=sub_gray.shape[1] // 8, maxLineGap=12)

        angles = []
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if abs(x2 - x1) > 25:
                    deg = math.degrees(math.atan2(y2 - y1, x2 - x1))
                    if abs(deg) < 9.0:
                        angles.append(deg)

        if len(angles) >= 3:
            median_deg = float(np.median(angles))
            if abs(median_deg) >= 0.25:
                center = (w // 2, h // 2)
                M = cv2.getRotationMatrix2D(center, median_deg, 1.0)
                # 使用双三次插值旋转展开，空白处填白色 (255, 255, 255)
                rotated = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, 
                                         borderMode=cv2.BORDER_CONSTANT, 
                                         borderValue=(255, 255, 255))
                return rotated
    except Exception:
        pass
    return img

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    """
    扫描全能王同款增强流水线 (原生 OpenCV 驱动)：
    1. OpenCV 梯度切除顶部桌面木纹与阴影
    2. OpenCV 霍夫直线文本行水平自拉平
    3. OpenCV 形态学闭运算照度除法（底色 100% 纯白，彻底解决灰黄偏暗）
    4. S型字迹浓黑压暗 + Lab通道彩色高保真（小手肉色、浅粉山峰、红字题号全彩保留）
    """
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return raw_bytes

        # 1. 切除顶部倾斜木纹与黑边
        img = detect_and_crop_wood_border(img)

        # 2. 文本基准线水平拉平
        img = detect_and_deskew_text(img)

        h, w = img.shape[:2]
        is_color = (str(color_mode).strip().lower() == "color")

        if is_color:
            # 彩色增强：转为 Lab 空间，独立处理亮度与色彩通道
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l_channel, a_channel, b_channel = cv2.split(lab)

            # 使用形态学大核闭运算提取真实纸张白场背景（解决灰黄不均问题）
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
            bg_l = cv2.morphologyEx(l_channel, cv2.MORPH_CLOSE, kernel)
            bg_l = cv2.GaussianBlur(bg_l, (21, 21), 0).astype(np.float32) + 1.0

            # 照度除法漂白
            norm_l = (l_channel.astype(np.float32) / bg_l) * 255.0
            norm_l = np.clip(norm_l, 0, 255)

            # 全能王强化阶调映射：>195 的底色直接推向 255 纯白亮透；<160 的字迹深墨浓黑
            norm_l = np.where(norm_l > 195.0, 255.0, norm_l)
            deep_l = np.where(norm_l < 160.0, (norm_l / 160.0) ** 1.65 * 62.0, norm_l)
            l_enhanced = np.clip(deep_l, 0, 255).astype(np.uint8)

            # 饱和度增强：对手掌肉色、红色印迹、彩色四线格增艳保真
            a_float = a_channel.astype(np.float32)
            b_float = b_channel.astype(np.float32)
            a_boost = np.clip((a_float - 128.0) * 1.45 + 128.0, 0, 255).astype(np.uint8)
            b_boost = np.clip((b_float - 128.0) * 1.45 + 128.0, 0, 255).astype(np.uint8)

            merged_lab = cv2.merge([l_enhanced, a_boost, b_boost])
            out_bgr = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)

            _, enc = cv2.imencode(".jpg", out_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()

        else:
            # 纯黑白试卷增强模式
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
            bg_gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
            bg_gray = cv2.GaussianBlur(bg_gray, (21, 21), 0).astype(np.float32) + 1.0

            norm = (gray.astype(np.float32) / bg_gray) * 255.0
            norm = np.clip(norm, 0, 255)

            # 背景全白截断 + 浓黑加深
            norm = np.where(norm > 190.0, 255.0, norm)
            deep = np.where(norm < 160.0, (norm / 160.0) ** 1.7 * 60.0, norm)
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
