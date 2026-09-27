#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import uuid
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def fast_detect_skew(gray_img):
    """微采样水平倾斜角度估计（耗时 < 0.05s）"""
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

def fit_to_a4_maximally(pil_img):
    """
    智能撑满 A4 幅面引擎：
    1. 动态裁剪原图自带的多余空白边缘，提取完整有效内容包围盒
    2. 按 A4 纸可打印极限铺满，消除大缩放与四周多余大白边，使字体清晰饱满
    """
    # 步骤 1：探测墨迹有效边界，裁除图片四周多余的大空旷白边
    gray = pil_img.convert("L")
    arr = np.array(gray)
    ink_mask = arr < 248

    if np.any(ink_mask):
        ymin, ymax = np.where(ink_mask.any(axis=1))[0][[0, -1]]
        xmin, xmax = np.where(ink_mask.any(axis=0))[0][[0, -1]]

        w, h = pil_img.size
        # 往外保留 12 像素呼吸缓冲，严禁切除边缘笔画与题号
        crop_box = (
            max(0, xmin - 12),
            max(0, ymin - 12),
            min(w, xmax + 12),
            min(h, ymax + 12)
        )
        content_img = pil_img.crop(crop_box)
    else:
        content_img = pil_img

    # 步骤 2：200 DPI 标准 A4 画布尺寸: 1654 x 2338
    a4_w, a4_h = 1654, 2338
    canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))

    # 仅预留激光打印机物理走纸不可打印死区 (左右各 28 像素 ≈ 3.5mm，上下各 35 像素)
    phys_margin_x = 28
    phys_margin_y = 35
    target_w = a4_w - phys_margin_x * 2
    target_h = a4_h - phys_margin_y * 2

    # 最大化铺满等比缩放
    ratio = min(target_w / content_img.width, target_h / content_img.height)
    new_w = int(content_img.width * ratio)
    new_h = int(content_img.height * ratio)

    resized = content_img.resize((new_w, new_h), Image.Resampling.BILINEAR)
    canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))
    return canvas

def process_camscanner_color_stream(input_path, output_path):
    """
    扫描全能王同款高保真去底引擎：
    1. 彻底禁用误切正文的四点透视，保留 100% 原始图像边界
    2. 平滑背景除法漂白，护住拼音四线格虚线与手绘线稿
    3. 最大化撑满 A4 画布，杜绝字变小与多余空旷留白
    """
    try:
        with Image.open(input_path) as disk_img:
            img = ImageOps.exif_transpose(disk_img.convert("RGB"))

        # 校验 EXIF 朝向，优先竖向立正
        if img.width > img.height:
            img = img.rotate(270, expand=True)

        # 轻微倾斜校正
        gray_small = img.convert("L")
        angle = fast_detect_skew(gray_small)
        if abs(angle) >= 1.0:
            img = img.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=(255, 255, 255))

        # 分辨率标准化
        if max(img.size) > 2200:
            img.thumbnail((2200, 2200), Image.Resampling.BILINEAR)

        # 背景除法柔和漂白（保护浅色线条与字迹）
        channels = [np.array(c, dtype=np.float32) for c in img.split()]
        cleaned_channels = []

        for c_arr in channels:
            c_pil = Image.fromarray(np.clip(c_arr, 0, 255).astype(np.uint8))
            # 40px 大平滑核滤除大面积灰底与光照阴影
            bg = c_pil.filter(ImageFilter.BoxBlur(radius=40))
            bg_arr = np.array(bg, dtype=np.float32) + 1.0

            divided = (c_arr / bg_arr) * 255.0

            out = np.zeros_like(divided)
            # 阈值放宽至 232，浅灰色线条、四线格虚线与线稿小插画完整保留
            out[divided >= 232] = 255.0

            mask_ink = divided < 232
            ink_val = np.clip((divided[mask_ink] - 20.0) * (240.0 / (232.0 - 20.0)), 0, 255)
            ink_val = (ink_val / 240.0) ** 1.12 * 195.0
            out[mask_ink] = ink_val
            cleaned_channels.append(np.clip(out, 0, 255).astype(np.uint8))

        clean_rgb = Image.merge("RGB", [Image.fromarray(c) for c in cleaned_channels])
        sharp_rgb = clean_rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=2))

        # 最大化排版至 A4 画布
        canvas = fit_to_a4_maximally(sharp_rgb)

        canvas.save(output_path, format="JPEG", quality=95)
        return True
    except Exception as e:
        print(f"[ProcessColorStream] 异常: {e}")
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

                # 下发 CUPS 打印
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
