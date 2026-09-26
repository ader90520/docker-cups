#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import uuid
import cv2
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def safe_imread(file_path):
    """解决 cv2.imread 无法读取包含中文路径或特殊字符的文件问题"""
    try:
        return cv2.imdecode(np.fromfile(file_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None

def auto_crop_document(bgr_img):
    """
    阶段 1：智能识别书本四个顶点，切除书本以外的桌布杂乱背景（四点透视变换）
    采用轻量化 600px 快速采样，避免占用过多内存
    """
    try:
        h, w = bgr_img.shape[:2]
        scale = 600.0 / max(h, w)
        small = cv2.resize(bgr_img, (int(w * scale), int(h * scale)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blurred, 50, 150)
        
        contours, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
        
        doc_cnt = None
        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.contourArea(c) > (small.shape[0] * small.shape[1] * 0.25):
                doc_cnt = approx
                break
                
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

        dst = np.array([
            [0, 0],
            [maxWidth - 1, 0],
            [maxWidth - 1, maxHeight - 1],
            [0, maxHeight - 1]], dtype="float32")

        M = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(bgr_img, M, (maxWidth, maxHeight))
    except Exception as e:
        print(f"[AutoCrop] 切边异常: {e}")
        return bgr_img

def dewarp_curved_text(bgr_img):
    """阶段 2：检测书本中缝拱起，将弯曲的字行反向拉直平展"""
    try:
        h, w = bgr_img.shape[:2]
        gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
        
        sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        sobel_y = np.abs(sobel_y)
        
        num_slices = 20
        slice_w = w // num_slices
        col_offsets = []
        
        for i in range(num_slices):
            col_slice = sobel_y[:, i * slice_w : (i + 1) * slice_w]
            proj = np.sum(col_slice, axis=1)
            indices = np.arange(h)
            total_e = np.sum(proj)
            centroid = np.sum(indices * proj) / (total_e + 1e-5)
            col_offsets.append(centroid)
            
        col_offsets = np.array(col_offsets)
        mean_val = np.median(col_offsets)
        deflection = col_offsets - mean_val
        
        if np.max(np.abs(deflection)) < 5.0 or np.max(np.abs(deflection)) > h * 0.15:
            return bgr_img
            
        x_coords = np.linspace(0, w, num_slices)
        all_x = np.arange(w)
        smooth_dy = np.interp(all_x, x_coords, deflection)
        
        map_x, map_y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        map_y = map_y - smooth_dy.reshape(1, w).astype(np.float32)
        
        return cv2.remap(bgr_img, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    except Exception as e:
        print(f"[Dewarp] 拉直异常: {e}")
        return bgr_img

def fast_detect_skew(gray_img):
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
    """全能王真彩色保留去底引擎 (200 DPI RGB 流式输出)"""
    try:
        cv_img = safe_imread(input_path)
        if cv_img is not None:
            cv_img = auto_crop_document(cv_img)
            cv_img = dewarp_curved_text(cv_img)
            raw_rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
            raw_img = Image.fromarray(raw_rgb)
        else:
            raw_img = Image.open(input_path)

        img = ImageOps.exif_transpose(raw_img)
        if img.width > img.height:
            img = img.rotate(270, expand=True)

        gray_small = img.convert("L")
        angle = fast_detect_skew(gray_small)
        if abs(angle) >= 1.0:
            img = img.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=(255, 255, 255))

        w, h = img.size
        cx, cy = int(w * 0.03), int(h * 0.03)
        img = img.crop((cx, cy, w - cx, h - cy))

        if max(img.size) > 1600:
            img.thumbnail((1600, 1600), Image.Resampling.BILINEAR)

        rgb = img.convert("RGB")
        channels = [np.array(c, dtype=np.float32) for c in rgb.split()]
        cleaned_channels = []

        for c_arr in channels:
            c_pil = Image.fromarray(np.clip(c_arr, 0, 255).astype(np.uint8))
            bg = c_pil.filter(ImageFilter.BoxBlur(radius=25))
            bg_arr = np.array(bg, dtype=np.float32) + 1.0

            divided = (c_arr / bg_arr) * 255.0

            out = np.zeros_like(divided)
            out[divided >= 195] = 255.0

            mask_ink = divided < 195
            ink_val = np.clip((divided[mask_ink] - 40.0) * (205.0 / (195.0 - 40.0)), 0, 255)
            ink_val = (ink_val / 205.0) ** 1.25 * 190.0
            out[mask_ink] = ink_val
            cleaned_channels.append(np.clip(out, 0, 255).astype(np.uint8))

        clean_rgb = Image.merge("RGB", [Image.fromarray(c) for c in cleaned_channels])
        sharp_rgb = clean_rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=2))

        # 200 DPI 标准 A4 画布居中排版 (1654 x 2338)
        a4_w, a4_h = 1654, 2338
        canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
        margin = 35
        target_w, target_h = a4_w - margin * 2, a4_h - margin * 2

        ratio = min(target_w / sharp_rgb.width, target_h / sharp_rgb.height)
        new_w, new_h = int(sharp_rgb.width * ratio), int(sharp_rgb.height * ratio)

        resized = sharp_rgb.resize((new_w, new_h), Image.Resampling.BILINEAR)
        canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))

        canvas.save(output_path, format="JPEG", quality=90)
        return True
    except Exception as e:
        print(f"[CamScannerColor] 处理异常: {e}")
        return False

def clean_old_tmp_files(directory, max_age_seconds=1800):
    """自动清理超过 30 分钟的临时任务文件，防止机顶盒 /tmp 分区打满"""
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
