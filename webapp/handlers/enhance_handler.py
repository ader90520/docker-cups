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

def order_points(pts):
    """整理四边形角点坐标：左上、右上、右下、左下"""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def auto_perspective_transform(img):
    """
    全能王核心算子：智能定位白纸主体并做非线性透视变换，
    彻底裁除顶部木纹桌面与倾斜黑边。
    """
    try:
        h, w = img.shape[:2]
        small_h = 600
        ratio = h / float(small_h)
        small_w = int(w / ratio)
        small = cv2.resize(img, (small_w, small_h), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # 使用自适应阈值与形态学处理，即使试卷边缘有弯曲也能连通
        thresh = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                       cv2.THRESH_BINARY_INV, 15, 4)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img

        # 寻找面积最大的白纸主体
        contours = sorted(contours, key=cv2.contourArea, reverse=True)
        main_c = contours[0]
        area = cv2.contourArea(main_c)

        # 白纸面积必须超过画面的 30%
        if area > (small_w * small_h * 0.30):
            peri = cv2.arcLength(main_c, True)
            approx = cv2.approxPolyDP(main_c, 0.03 * peri, True)

            # 方案1：若近似多边形刚好为四边形
            if len(approx) == 4:
                pts = approx.reshape(4, 2) * ratio
                rect = order_points(pts)
            else:
                # 方案2（关键修复）：书页有弯曲无法拟合成严格 4 边时，使用最小凸外接多边形
                hull = cv2.convexHull(main_c)
                rot_rect = cv2.minAreaRect(hull)
                box = cv2.boxPoints(rot_rect)
                box = box * ratio
                rect = order_points(box)

            (tl, tr, br, bl) = rect
            w_a = np.hypot(br[0] - bl[0], br[1] - bl[1])
            w_b = np.hypot(tr[0] - tl[0], tr[1] - tl[1])
            max_w = int(max(w_a, w_b))

            h_a = np.hypot(tr[0] - br[0], tr[1] - br[1])
            h_b = np.hypot(tl[0] - bl[0], tl[1] - bl[1])
            max_h = int(max(h_a, h_b))

            # 约束尺寸合理性
            if max_w > w * 0.5 and max_h > h * 0.5:
                dst = np.array([
                    [0, 0],
                    [max_w - 1, 0],
                    [max_w - 1, max_h - 1],
                    [0, max_h - 1]
                ], dtype="float32")

                M = cv2.getPerspectiveTransform(rect, dst)
                warped = cv2.warpPerspective(img, M, (max_w, max_h), flags=cv2.INTER_CUBIC)
                return warped
    except Exception:
        pass
    return img

def detect_and_deskew_text(img):
    """中段文字行精细纠偏（补偿透视变换后的微小残留倾斜）"""
    try:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 裁剪页面中间区域，杜绝四周边缘干扰
        sub_gray = gray[int(h * 0.25):int(h * 0.75), int(w * 0.15):int(w * 0.85)]
        edges = cv2.Canny(sub_gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 70, minLineLength=sub_gray.shape[1] // 8, maxLineGap=12)

        angles = []
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if abs(x2 - x1) > 25:
                    deg = math.degrees(math.atan2(y2 - y1, x2 - x1))
                    if abs(deg) < 10.0:
                        angles.append(deg)

        if len(angles) >= 3:
            median_deg = float(np.median(angles))
            if abs(median_deg) > 0.3:
                center = (w // 2, h // 2)
                M = cv2.getRotationMatrix2D(center, median_deg, 1.0)
                rotated = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
                return rotated
    except Exception:
        pass
    return img

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    """
    全能王同款处理管线：
    1. 非线性凸包透视展平（切除木纹背景与黑边）
    2. 文本水平基准线校正
    3. 形态学光照场除法漂白（彻底纯白、绝不灰黄）
    4. S型字迹浓墨加深 + 彩色细节通道全真保留（手掌肉色、山丘、红框）
    """
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR) if HAVE_OPENCV else None
    except Exception:
        img = None

    is_color = (str(color_mode).strip().lower() == "color")

    if HAVE_OPENCV and img is not None:
        # 1. 执行四角/凸包透视拉正并裁掉木纹
        img = auto_perspective_transform(img)

        # 2. 文本水平基准线微调
        img = detect_and_deskew_text(img)
        h, w = img.shape[:2]

        if is_color:
            # 彩色增强：转为 Lab 空间，解耦照度与色彩通道
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l_channel, a_channel, b_channel = cv2.split(lab)

            # 使用形态学大核膨胀提取真实纸张白场背景（解决灰黄不均问题）
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
            bg_l = cv2.morphologyEx(l_channel, cv2.MORPH_CLOSE, kernel)
            bg_l = cv2.GaussianBlur(bg_l, (21, 21), 0).astype(np.float32) + 1.0

            # 照度除法彻底漂白
            norm_l = (l_channel.astype(np.float32) / bg_l) * 255.0
            norm_l = np.clip(norm_l, 0, 255)

            # 全能王强化阶调映射：>200 的底色直接推向 255 纯白亮透；<160 的字迹深墨下潜
            norm_l = np.where(norm_l > 195.0, 255.0, norm_l)
            deep_l = np.where(norm_l < 160.0, (norm_l / 160.0) ** 1.6 * 68.0, norm_l)
            l_enhanced = np.clip(deep_l, 0, 255).astype(np.uint8)

            # 饱和度增强：对手掌肉色、红色印迹、彩色虚线插图增艳
            a_float = a_channel.astype(np.float32)
            b_float = b_channel.astype(np.float32)
            a_boost = np.clip((a_float - 128.0) * 1.45 + 128.0, 0, 255).astype(np.uint8)
            b_boost = np.clip((b_float - 128.0) * 1.45 + 128.0, 0, 255).astype(np.uint8)

            merged_lab = cv2.merge([l_enhanced, a_boost, b_boost])
            out_bgr = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)

            _, enc = cv2.imencode(".jpg", out_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()

        else:
            # 黑白试卷增强模式
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
            bg_gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
            bg_gray = cv2.GaussianBlur(bg_gray, (21, 21), 0).astype(np.float32) + 1.0

            norm = (gray.astype(np.float32) / bg_gray) * 255.0
            norm = np.clip(norm, 0, 255)

            # 背景全白截断 + 浓黑加深
            norm = np.where(norm > 190.0, 255.0, norm)
            deep = np.where(norm < 160.0, (norm / 160.0) ** 1.7 * 65.0, norm)
            out_mono = np.clip(deep, 0, 255).astype(np.uint8)

            _, enc = cv2.imencode(".jpg", out_mono, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()

    else:
        # PIL 降级容错
        with Image.open(io.BytesIO(raw_bytes)) as pil_img:
            img = ImageOps.exif_transpose(pil_img.convert("RGB"))
        w, h = img.size
        gray = ImageOps.grayscale(img)
        bg = gray.filter(ImageFilter.BoxBlur(radius=20))
        gray_np = np.array(gray, dtype=np.float32)
        bg_np = np.array(bg, dtype=np.float32) + 1.0

        diff = np.clip((gray_np / bg_np) * 255.0, 0, 255)
        diff = np.where(diff > 195.0, 255.0, diff)
        diff = np.where(diff < 160.0, (diff / 160.0) ** 1.6 * 70.0, diff)
        mono_res = np.clip(diff, 0, 255).astype(np.uint8)

        if is_color:
            r, g, b = img.split()
            r_np = np.clip((np.array(r, dtype=np.float32) / bg_np) * 255.0 * 1.2, 0, 255)
            g_np = np.clip((np.array(g, dtype=np.float32) / bg_np) * 255.0 * 1.1, 0, 255)
            b_np = np.clip((np.array(b, dtype=np.float32) / bg_np) * 255.0 * 1.1, 0, 255)
            out_img = Image.fromarray(np.stack([r_np, g_np, b_np], axis=-1).astype(np.uint8), mode="RGB")
        else:
            out_img = Image.fromarray(mono_res)

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
