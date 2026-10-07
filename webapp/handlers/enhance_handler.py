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
    自动寻找纸张四边形边缘并做透视拉平，
    裁掉桌面木纹、背景阴影。若未拍全纸张边缘，则自动安全回退。
    """
    try:
        h, w = img.shape[:2]
        small_h = 600
        ratio = h / float(small_h)
        small_w = int(w / ratio)
        small = cv2.resize(img, (small_w, small_h), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blur, 40, 150)

        # 闭运算连接断裂的白纸边缘
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4:
                area = cv2.contourArea(approx)
                # 纸张面积必须占整张画面的 40% 以上才做透视裁剪
                if area > (small_w * small_h * 0.40):
                    pts = approx.reshape(4, 2) * ratio
                    rect = order_points(pts)
                    (tl, tr, br, bl) = rect

                    w_a = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
                    w_b = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
                    max_w = max(int(w_a), int(w_b))

                    h_a = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
                    h_b = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
                    max_h = max(int(h_a), int(h_b))

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
    """
    针对未触发完整四边形裁切的图片，使用霍夫直线与行间梯度精确纠偏水平倾角（±12度以内）
    """
    try:
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 裁剪页面中间 60% 区域计算倾角，杜绝顶部木纹影响
        sub_gray = gray[int(h * 0.2):int(h * 0.8), int(w * 0.1):int(w * 0.9)]
        edges = cv2.Canny(sub_gray, 50, 200, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 80, minLineLength=sub_gray.shape[1] // 8, maxLineGap=10)

        angles = []
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if abs(x2 - x1) > 20:
                    deg = math.degrees(math.atan2(y2 - y1, x2 - x1))
                    if abs(deg) < 12.0:
                        angles.append(deg)

        if len(angles) >= 3:
            median_deg = float(np.median(angles))
            if abs(median_deg) > 0.4:
                center = (w // 2, h // 2)
                M = cv2.getRotationMatrix2D(center, median_deg, 1.0)
                rotated = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
                return rotated
    except Exception:
        pass
    return img

def enhance_camscanner_precise(raw_bytes, color_mode="monochrome"):
    """
    扫描全能王级增强流水线：
    1. 纸张边缘四角定位与透视校正（去除桌面木纹及倾斜）
    2. 辅助霍夫直线二次水平微调
    3. Lab 空间照度场除法（底色纯白，浅肉色/彩色插图/虚线 100% 完整保留，字迹浓黑）
    """
    try:
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR) if HAVE_OPENCV else None
    except Exception:
        img = None

    is_color = (str(color_mode).strip().lower() == "color")

    if HAVE_OPENCV and img is not None:
        # 1. 自动寻找纸张轮廓进行透视拉正并去除背景
        img = auto_perspective_transform(img)

        # 2. 文本行二次精细拉平
        img = detect_and_deskew_text(img)
        h, w = img.shape[:2]

        # 3. 颜色模式：基于 Lab 空间分别处理“明度”与“色彩”，避免浅色被当作白底冲刷
        if is_color:
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l_channel, a_channel, b_channel = cv2.split(lab)
            l_float = l_channel.astype(np.float32)

            # 估计白场背景亮度 (使用大核高斯滤波)
            bg_l = cv2.GaussianBlur(l_channel, (0, 0), sigmaX=25, sigmaY=25).astype(np.float32) + 1.0
            
            # 明度除法漂白：将灰黄底色推向 255
            norm_l = (l_float / bg_l) * 255.0
            norm_l = np.clip(norm_l, 0, 255)

            # S 型非线性对比度延伸：让黑色字体沉降至深墨，保留中间浅色调
            deep_l = np.where(norm_l < 165.0, (norm_l / 165.0) ** 1.5 * 75.0, norm_l)
            deep_l = np.where(deep_l > 220.0, 255.0, deep_l)
            l_enhanced = np.clip(deep_l, 0, 255).astype(np.uint8)

            # 适度增加色彩饱和度（让肉色、插图更鲜活，避免发虚）
            a_boost = np.clip((a_channel.astype(np.float32) - 128.0) * 1.35 + 128.0, 0, 255).astype(np.uint8)
            b_boost = np.clip((b_channel.astype(np.float32) - 128.0) * 1.35 + 128.0, 0, 255).astype(np.uint8)

            merged_lab = cv2.merge([l_enhanced, a_boost, b_boost])
            out_bgr = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)

            _, enc = cv2.imencode(".jpg", out_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()

        else:
            # 纯黑白模式：仅处理灰度与深墨化
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            bg_gray = cv2.GaussianBlur(gray, (0, 0), sigmaX=25, sigmaY=25).astype(np.float32) + 1.0
            norm = (gray.astype(np.float32) / bg_gray) * 255.0
            norm = np.clip(norm, 0, 255)

            # 浓黑压暗
            deep = np.where(norm < 175.0, (norm / 175.0) ** 1.6 * 75.0, norm)
            deep = np.where(deep > 200.0, 255.0, deep)
            out_mono = np.clip(deep, 0, 255).astype(np.uint8)

            _, enc = cv2.imencode(".jpg", out_mono, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            return enc.tobytes()

    else:
        # PIL 备用降级逻辑
        with Image.open(io.BytesIO(raw_bytes)) as pil_img:
            img = ImageOps.exif_transpose(pil_img.convert("RGB"))

        w, h = img.size
        gray = ImageOps.grayscale(img)
        bg = gray.filter(ImageFilter.GaussianBlur(radius=20))
        gray_np = np.array(gray, dtype=np.float32)
        bg_np = np.array(bg, dtype=np.float32) + 1.0

        diff = (gray_np / bg_np) * 255.0
        diff = np.where(diff < 170.0, (diff / 170.0) ** 1.6 * 80.0, diff)
        diff = np.where(diff > 205.0, 255.0, diff)
        mono_res = np.clip(diff, 0, 255).astype(np.uint8)

        if is_color:
            r, g, b = img.split()
            r_np = np.clip((np.array(r, dtype=np.float32) / bg_np) * 255.0 * 1.15, 0, 255)
            g_np = np.clip((np.array(g, dtype=np.float32) / bg_np) * 255.0 * 1.15, 0, 255)
            b_np = np.clip((np.array(b, dtype=np.float32) / bg_np) * 255.0 * 1.15, 0, 255)
            
            rgb_arr = np.stack([r_np, g_np, b_np], axis=-1).astype(np.uint8)
            out_img = Image.fromarray(rgb_arr, mode="RGB")
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
