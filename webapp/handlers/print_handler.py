#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import uuid
import subprocess
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def log_debug(msg):
    try:
        with open("/tmp/dewarp_debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(f"[DewarpLog] {msg}", flush=True)

def measure_line_skew(gray_img):
    """通过梯度水平方差提取局部文本基线倾角"""
    try:
        w, h = gray_img.size
        tw = 320
        th = max(10, int(h * (320.0 / w)))
        small = gray_img.resize((tw, th), Image.Resampling.BILINEAR)
        arr = np.array(small, dtype=np.float32)

        grad = np.abs(arr[2:, :] - arr[:-2, :])
        grad[grad < 35] = 0.0

        best_angle = 0.0
        max_var = 0.0
        for angle in np.linspace(-6.0, 6.0, 25):
            rot = Image.fromarray(grad).rotate(angle, resample=Image.Resampling.NEAREST)
            var = np.var(np.sum(np.array(rot), axis=1))
            if var > max_var:
                max_var = var
                best_angle = angle
        return best_angle
    except Exception:
        return 0.0

def adaptive_perspective_flatten(pil_img):
    """双基准透视拉平，防止局部非线性拉扯导致插图弯曲"""
    try:
        w, h = pil_img.size
        gray = pil_img.convert("L")

        top_crop = gray.crop((0, 0, w, int(h * 0.35)))
        bot_crop = gray.crop((0, int(h * 0.65), w, h))

        deg_top = measure_line_skew(top_crop)
        deg_bot = measure_line_skew(bot_crop)
        log_debug(f"基线探测 -> 顶部: {deg_top:.2f}°, 底部: {deg_bot:.2f}°")

        dy_top = int(round(np.tan(np.radians(deg_top)) * w))
        dy_bot = int(round(np.tan(np.radians(deg_bot)) * w))

        tl_y = max(0, -dy_top)
        tr_y = max(0, dy_top)
        bl_y = h - max(0, dy_bot)
        br_y = h - max(0, -dy_bot)

        if abs(dy_top) >= 3 or abs(dy_bot) >= 3:
            quad = (0, tl_y, 0, bl_y, w, br_y, w, tr_y)
            pil_img = pil_img.transform((w, h), Image.Transform.QUAD, quad, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
            log_debug("曲率透视拉直完成")
    except Exception as e:
        log_debug(f"拉直失败: {e}")
    return pil_img

def auto_crop_paper_boundaries(img):
    """自动探测纸张边界，切除外部背景桌面阴影与深暗装订边，拉伸填充满版"""
    try:
        w, h = img.size
        small = img.resize((300, int(h * (300.0 / w))), Image.Resampling.BILINEAR)
        gray = np.array(small.convert("L"), dtype=np.uint8)

        sw, sh = small.size
        left, top, right, bottom = 0, 0, sw, sh

        # 扫描左侧
        for x in range(int(sw * 0.08)):
            if np.mean(gray[:, x]) > 130:
                left = max(0, x - 2)
                break
        # 扫描右侧
        for x in range(sw - 1, int(sw * 0.90), -1):
            if np.mean(gray[:, x]) > 130:
                right = min(sw, x + 2)
                break
        # 扫描底部
        for y in range(sh - 1, int(sh * 0.90), -1):
            if np.mean(gray[y, :]) > 130:
                bottom = min(sh, y + 2)
                break

        scale_x = w / float(sw)
        scale_y = h / float(sh)
        crop_box = (
            int(left * scale_x),
            int(top * scale_y),
            int(right * scale_x),
            int(bottom * scale_y)
        )

        if crop_box[2] - crop_box[0] > w * 0.85 and crop_box[3] - crop_box[1] > h * 0.85:
            cropped = img.crop(crop_box)
            log_debug(f"切除外部黑边: {crop_box} -> 拉伸填充满版")
            return cropped.resize((w, h), Image.Resampling.BICUBIC)
    except Exception as e:
        log_debug(f"自动切除黑边异常: {e}")
    return img

def process_image_for_print(input_path, output_path):
    """
    全能王级处理：
    1. 自动切除外部桌面阴影
    2. 双基准刚性透视校正
    3. 彩色通道饱和度保护与加黑
    4. 45px 大核 BoxBlur 光照归一化去透墨
    5. 边缘暗角清理与微锐化
    """
    try:
        log_debug(f"开始处理图像: {input_path}")
        with Image.open(input_path) as disk_img:
            img = ImageOps.exif_transpose(disk_img.convert("RGB"))

        if img.width > img.height:
            img = img.rotate(270, expand=True)

        # 1. 切除外部拍摄背景桌面与中缝黑边
        img = auto_crop_paper_boundaries(img)

        # 2. 梯形与翘曲透视拉平
        img = adaptive_perspective_flatten(img)

        if max(img.size) > 2200:
            img.thumbnail((2200, 2200), Image.Resampling.BILINEAR)

        w, h = img.size
        r, g, b = img.split()
        r_arr = np.array(r, dtype=np.float32)
        g_arr = np.array(g, dtype=np.float32)
        b_arr = np.array(b, dtype=np.float32)

        # 3. 彩色通道保护与强化（保护淡红阶梯线、蓝色底框、彩色图标）
        max_c = np.maximum(np.maximum(r_arr, g_arr), b_arr)
        min_c = np.minimum(np.minimum(r_arr, g_arr), b_arr)
        color_diff = max_c - min_c

        gray_arr = 0.299 * r_arr + 0.587 * g_arr + 0.114 * b_arr

        # 针对带色彩的区域强制深度加黑，保证在黑白激光机上清晰可见
        color_boost_mask = color_diff > 18.0
        gray_arr[color_boost_mask] = np.clip(gray_arr[color_boost_mask] - color_diff[color_boost_mask] * 1.5, 0, 255)

        contrast_img = Image.fromarray(gray_arr.astype(np.uint8))

        # 4. 45px 大核滤镜计算漫反射背景与背面透墨
        bg = contrast_img.filter(ImageFilter.BoxBlur(radius=45))
        bg_arr = np.array(bg, dtype=np.float32) + 1.0

        # 5. 光照除法归一化
        divided = (gray_arr / bg_arr) * 255.0
        out = np.full_like(divided, 255.0)

        # 6. 双阶切分：加深真实笔画，纯白化无色彩漫反射灰底透墨
        mask_front = divided < 210.0
        ink_vals = divided[mask_front]
        clean_ink = np.clip((ink_vals - 10.0) * (210.0 / (210.0 - 10.0)), 0, 255)
        clean_ink = (clean_ink / 210.0) ** 1.35 * 180.0
        out[mask_front] = clean_ink

        # 7. 兜底边缘羽化压白
        pad_x = max(1, int(w * 0.012))
        pad_y = max(1, int(h * 0.012))
        out[0:pad_y, :] = 255.0
        out[h-pad_y:h, :] = 255.0
        out[:, 0:pad_x] = 255.0
        out[:, w-pad_x:w] = 255.0

        clean_gray = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

        # 8. 微锐化还原细节
        sharp = clean_gray.filter(ImageFilter.UnsharpMask(radius=1.2, percent=140, threshold=2))
        sharp_rgb = Image.merge("RGB", [sharp, sharp, sharp])

        sharp_rgb.save(output_path, format="JPEG", quality=95, dpi=(300, 300))
        log_debug(f"处理完成，保存至: {output_path}")
        return True
    except Exception as e:
        log_debug(f"处理异常: {e}")
        return False

def clean_old_tmp_files(directory, max_age_seconds=1800):
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
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到上传文件")
                return

            jobs = []
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            for f in files:
                ext = os.path.splitext(f["filename"])[-1].lower()
                token = uuid.uuid4().hex[:8]
                src_path = os.path.join(UPLOAD_DIR, f"raw_{token}{ext}")
                with open(src_path, "wb") as out:
                    out.write(f["body"])

                target_file = src_path

                if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
                    enhanced_path = os.path.join(UPLOAD_DIR, f"opt_{token}.jpg")
                    if process_image_for_print(src_path, enhanced_path):
                        target_file = enhanced_path

                cmd = [
                    "lp",
                    "-d", printer,
                    "-n", str(copies),
                    "-o", "media=A4",
                    "-o", "PageSize=A4",
                    "-o", "natural-scaling=90",
                    "-o", "position=center",
                    target_file
                ]

                log_debug(f"派发CUPS打印指令: {' '.join(cmd)}")
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            clean_old_tmp_files(UPLOAD_DIR)
            self.write_json(True, f"共 {len(files)} 个文件任务已派发", job=", ".join(jobs))
        except Exception as e:
            log_debug(f"全局打印异常: {e}")
            self.write_json(False, f"打印服务异常: {str(e)}")
