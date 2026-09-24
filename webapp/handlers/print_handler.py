#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def detect_skew_angle(gray_img):
    try:
        w, h = gray_img.size
        scale = 320.0 / max(w, h)
        small = gray_img.resize((int(w * scale), int(h * scale)), Image.Resampling.NEAREST)
        arr = np.array(small, dtype=np.float32)

        grad = np.abs(arr[2:, :] - arr[:-2, :])
        grad[grad < 30] = 0.0

        best_angle = 0.0
        max_var = 0.0
        for angle in np.arange(-4.0, 4.5, 0.5):
            rot = Image.fromarray(grad).rotate(angle, resample=Image.Resampling.NEAREST)
            proj = np.sum(np.array(rot), axis=1)
            var = np.var(proj)
            if var > max_var:
                max_var = var
                best_angle = angle
        return best_angle
    except Exception:
        return 0.0

def process_camscanner_a4(input_path, output_path):
    """
    全能王 v5 细节线稿保全与纯净去底引擎:
    1. EXIF 纠正与文字倾斜拉平
    2. 大窗口高斯局部背景除法 (消除大面积阴影)
    3. 形态学局部暗线保护 (保留树叶细脉、鸟羽、微弱拼音与虚线框)
    4. 红色线稿与迷宫折线强化
    5. USM 线稿锐化与 A4 300DPI 居中排版
    """
    try:
        with Image.open(input_path) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            if img.width > img.height:
                img = img.rotate(270, expand=True)

            # 倾斜纠正
            gray_deskew = img.convert("L")
            angle = detect_skew_angle(gray_deskew)
            if abs(angle) > 0.15:
                img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(255, 255, 255))

            # 切除周边暗影杂边 (3.5%)
            w, h = img.size
            cx, cy = int(w * 0.035), int(h * 0.035)
            img = img.crop((cx, cy, w - cx, h - cy))

            if max(img.size) > 2400:
                img.thumbnail((2400, 2400), Image.Resampling.BILINEAR)

            rgb = img.convert("RGB")
            arr = np.array(rgb, dtype=np.float32)

            # 1. 大核高斯模糊分离光照背景 (radius=50，避免细线被模糊成背景)
            bg = rgb.filter(ImageFilter.GaussianBlur(radius=50))
            bg_arr = np.array(bg, dtype=np.float32) + 1e-4

            divided = (arr / bg_arr) * 255.0

            r, g, b = divided[:, :, 0], divided[:, :, 1], divided[:, :, 2]
            max_c = np.maximum(np.maximum(r, g), b)
            min_c = np.minimum(np.minimum(r, g), b)
            chroma = max_c - min_c

            # 识别偏红元素（红虚线、迷宫折线、红数字）
            is_red = (r > (g + 12.0)) & (r > (b + 12.0)) & (chroma > 15.0)
            lum = 0.299 * r + 0.587 * g + 0.114 * b

            # 对红线深度加黑，对其他彩色适度暗化
            effective_lum = np.where(is_red, lum * 0.50, lum)
            effective_lum = np.where(~is_red & (chroma > 15.0), effective_lum * 0.75, effective_lum)

            # 2. 核心细节保全：提取细小暗线（局部 Min Filter 补偿）
            # 用 3x3 最小滤波找出周围最暗的点，专门捕获纤细叶脉和小鸟羽毛
            lum_pil = Image.fromarray(effective_lum.astype(np.uint8))
            min_filtered = lum_pil.filter(ImageFilter.MinFilter(size=3))
            min_arr = np.array(min_filtered, dtype=np.float32)

            # 将细线特征融合进原亮度（细线处加重 30% 黑色）
            line_detail = np.maximum(0.0, effective_lum - min_arr)
            enhanced_lum = effective_lum - line_detail * 0.55

            # 3. 平缓的扫描全能王对比度拉伸曲线（放宽上限，不扼杀淡线）
            # 50 以下为纯黑，220 以上为纯白，中间留足 170 阶过渡空间
            boosted = np.clip((enhanced_lum - 45.0) * (255.0 / (220.0 - 45.0)), 0, 255)

            # 强化中暗部（让文字和细线浓度更深）
            boosted = (boosted / 255.0) ** 1.25 * 255.0

            # 纯白底色截断：高于 216 的全部设为 255 纯白
            boosted[boosted > 216] = 255
            # 暗部沉降：低于 125 的线条强力加深，保证激光打印机喷实碳粉
            boosted[boosted < 125] = boosted[boosted < 125] * 0.40

            clean_img = Image.fromarray(boosted.astype(np.uint8))

            # 4. USM 高频线稿边缘锐化（半径 1.5，强度 180%，阈值 2）
            sharp_img = clean_img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=180, threshold=2))

            # 5. 排版至标准 300DPI A4 (2480 x 3508)
            a4_w, a4_h = 2480, 3508
            canvas = Image.new("L", (a4_w, a4_h), 255)
            margin = 50
            target_w, target_h = a4_w - margin * 2, a4_h - margin * 2

            ratio = min(target_w / sharp_img.width, target_h / sharp_img.height)
            new_w, new_h = int(sharp_img.width * ratio), int(sharp_img.height * ratio)

            resized = sharp_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))

            canvas.save(output_path, format="JPEG", quality=95)
            return True
    except Exception as e:
        print(f"[CamScannerEngine] 异常: {e}")
        return False

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
                    if process_camscanner_a4(src_path, enhanced_path):
                        target_path = enhanced_path

                res = self.execute_lp(printer, copies, target_path)
                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            self.write_json(True, f"共 {len(files)} 个文件已按印刷级全能王标准排版打印", job=", ".join(jobs))
        except Exception as e:
            self.write_json(False, f"打印服务异常: {str(e)}")
