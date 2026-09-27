#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import uuid
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

def safe_imread(file_path):
    """防中文路径乱码安全读取"""
    if not HAS_CV2:
        return None
    try:
        return cv2.imdecode(np.fromfile(file_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None

def auto_crop_document_safe(bgr_img):
    """
    智能四点透视校正（高安全门槛 + 宽裕安全呼吸区）
    坚决防止误把正文边缘当纸张外框削掉序号与插画
    """
    if not HAS_CV2 or bgr_img is None:
        return bgr_img
    try:
        h, w = bgr_img.shape[:2]
        scale = 800.0 / max(h, w)
        small = cv2.resize(bgr_img, (int(w * scale), int(h * scale)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        blurred = cv2.GaussianBlur(gray, (7, 7), 0)
        edged = cv2.Canny(blurred, 25, 100)
        
        contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
        
        doc_cnt = None
        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            # 严格门槛：面积必须占整张照片 75% 以上，才认定为拍摄到整张试卷
            if len(approx) == 4 and cv2.contourArea(c) > (small.shape[0] * small.shape[1] * 0.75):
                doc_cnt = approx
                break
                
        # 未能 100% 确认外框时直接返回原图，宁留桌面背景绝不裁坏文字
        if doc_cnt is None:
            return bgr_img

        pts = doc_cnt.reshape(4, 2) / scale
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        
        (tl, tr, br, bl) = rect
        widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        maxWidth = max(int(widthA), int(widthB))

        heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        maxHeight = max(int(heightA), int(heightB))

        # 向外安全回弹 4.5% 呼吸空间，彻底护住左侧题号一二三四五及右侧插图
        pad_w = int(maxWidth * 0.045)
        pad_h = int(maxHeight * 0.045)

        dst = np.array([
            [pad_w, pad_h],
            [maxWidth - 1 - pad_w, pad_h],
            [maxWidth - 1 - pad_w, maxHeight - 1 - pad_h],
            [pad_w, maxHeight - 1 - pad_h]], dtype="float32")

        M = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(bgr_img, M, (maxWidth, maxHeight), borderMode=cv2.BORDER_REPLICATE)
    except Exception as e:
        print(f"[AutoCropSafe] 异常跳过: {e}")
        return bgr_img

def fast_detect_skew(gray_img):
    """快速倾斜角探测"""
    try:
        w, h = gray_img.size
        scale = 160.0 / max(w, h)
        small = gray_img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.NEAREST)
        arr = np.array(small, dtype=np.float32)

        grad = np.abs(arr[2:, :] - arr[:-2, :])
        grad[grad < 35] = 0.0

        best_angle = 0.0
        max_var = 0.0
        for angle in [-2.0, 0.0, 2.0]:
            rot = Image.fromarray(grad).rotate(angle, resample=Image.Resampling.NEAREST)
            proj = np.sum(np.array(rot), axis=1)
            var = np.var(proj)
            if var > max_var:
                max_var = var
                best_angle = angle
        return best_angle
    except Exception:
        return 0.0

def process_camscanner_color_stream(input_path, output_path):
    """
    扫描全能王同款高保真去底引擎：
    1. 彻底解决大题编号与右侧插画被吞问题
    2. 护住拼音四线格虚线与手绘简笔画线稿
    """
    try:
        raw_img = None
        if HAS_CV2:
            cv_img = safe_imread(input_path)
            if cv_img is not None:
                cv_img = auto_crop_document_safe(cv_img)
                raw_rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
                raw_img = Image.fromarray(raw_rgb)
        
        if raw_img is None:
            with Image.open(input_path) as disk_img:
                raw_img = disk_img.copy()

        # 校验 EXIF 朝向，优先立正构图
        img = ImageOps.exif_transpose(raw_img)
        if img.width > img.height:
            img = img.rotate(270, expand=True)

        gray_small = img.convert("L")
        angle = fast_detect_skew(gray_small)
        if abs(angle) >= 1.0:
            img = img.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=(255, 255, 255))

        # 【关键修正 1】：严禁盲目切内缩边距！彻底剔除强制削边代码
        if max(img.size) > 2200:
            img.thumbnail((2200, 2200), Image.Resampling.BILINEAR)

        # 【关键修正 2】：平滑背景除法（温和对比度曲线保护浅色线稿）
        rgb = img.convert("RGB")
        channels = [np.array(c, dtype=np.float32) for c in rgb.split()]
        cleaned_channels = []

        for c_arr in channels:
            c_pil = Image.fromarray(np.clip(c_arr, 0, 255).astype(np.uint8))
            # 采用 35px 大核，确保纸张整体光照被滤出，而不吞并细文字
            bg = c_pil.filter(ImageFilter.BoxBlur(radius=35))
            bg_arr = np.array(bg, dtype=np.float32) + 1.0

            divided = (c_arr / bg_arr) * 255.0

            out = np.zeros_like(divided)
            # 阈值调宽至 228，拼音四线格虚线、音调、手绘线稿 100% 完整保留
            out[divided >= 228] = 255.0

            mask_ink = divided < 228
            ink_val = np.clip((divided[mask_ink] - 25.0) * (235.0 / (228.0 - 25.0)), 0, 255)
            # 墨水增强曲线：深字黑亮，浅线条自然
            ink_val = (ink_val / 235.0) ** 1.15 * 195.0
            out[mask_ink] = ink_val
            cleaned_channels.append(np.clip(out, 0, 255).astype(np.uint8))

        clean_rgb = Image.merge("RGB", [Image.fromarray(c) for c in cleaned_channels])
        sharp_rgb = clean_rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=125, threshold=2))

        # 标准 200 DPI A4 画布排版 (1654 x 2338)
        a4_w, a4_h = 1654, 2338
        canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
        
        # 页面留白边距仅保留 16 像素，最大化打印视野
        margin = 16
        target_w, target_h = a4_w - margin * 2, a4_h - margin * 2

        ratio = min(target_w / sharp_rgb.width, target_h / sharp_rgb.height)
        new_w, new_h = int(sharp_rgb.width * ratio), int(sharp_rgb.height * ratio)

        resized = sharp_rgb.resize((new_w, new_h), Image.Resampling.BILINEAR)
        canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))

        canvas.save(output_path, format="JPEG", quality=94)
        return True
    except Exception as e:
        print(f"[CamScannerColor] 处理异常: {e}")
        return False

def clean_old_tmp_files(directory, max_age_seconds=1800):
    """自动清理临时文件"""
    try:
        now = time.time()
        for f in os.listdir(directory):
            p = os.path.join(directory, f)
            if os.path.isfile(p) and (now - os.path.getmtime(p) > max_age_seconds):
                os.remove(p)
    except Exception:
        pass

class PrintHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            whiten = self.get_argument("whiten", "0")
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到文件")
                return

            jobs = []
            for f in files:
                ext = os.path.splitext(f["filename"])[-1].lower()
                token = uuid.uuid4().hex[:8]
                src_path = os.path.join(UPLOAD_DIR, f"raw_{token}{ext}")
                with open(src_path, "wb") as out:
                    out.write(f["body"])

                target_path = src_path
                if whiten == "1" and ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                    enhanced_path = os.path.join(UPLOAD_DIR, f"cam_{token}.jpg")
                    if process_camscanner_color_stream(src_path, enhanced_path):
                        target_path = enhanced_path

                res = self.execute_lp(printer, copies, target_path)
                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            clean_old_tmp_files(UPLOAD_DIR)
            self.write_json(True, f"共 {len(files)} 个文件任务已派发", job=", ".join(jobs))
        except Exception as e:
            self.write_json(False, f"打印服务异常: {str(e)}")
