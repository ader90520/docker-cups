#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler, UPLOAD_DIR

def process_camscanner_a4(input_path, output_path):
    """
    工业级扫描全能王去底引擎 (CamScanner Level):
    1. EXIF 方向校正 & 竖版自动摆正
    2. 主动切除外沿 3.5% 拍摄黑边/桌布/装订暗影
    3. RGB 局部背景相除 (Background Division) 彻底漂白，消除背面透字
    4. 文本加深锐化 & 规范填充至 300DPI A4 标准画布
    """
    try:
        with Image.open(input_path) as raw_img:
            # 1. 姿态纠正
            img = ImageOps.exif_transpose(raw_img)
            if img.width > img.height:
                img = img.rotate(270, expand=True)

            # 2. 切除外围黑边与杂边 (边缘 3.5% 容错切除，彻底去掉床单与装订线黑框)
            w, h = img.size
            crop_x = int(w * 0.035)
            crop_y = int(h * 0.035)
            img = img.crop((crop_x, crop_y, w - crop_x, h - crop_y))

            # 缩放至适中尺寸提高处理效率与防 OOM
            if max(img.size) > 2400:
                img.thumbnail((2400, 2400), Image.Resampling.BILINEAR)

            # 3. 局部高斯模糊估计光照背景
            rgb_img = img.convert("RGB")
            bg = rgb_img.filter(ImageFilter.GaussianBlur(radius=30))

            orig_arr = np.array(rgb_img, dtype=np.float32)
            bg_arr = np.array(bg, dtype=np.float32) + 1e-4

            # 背景除法：消除阴影、暗斑和背面透光虚影
            divided = (orig_arr / bg_arr) * 255.0

            # 动态非线性拉伸：消除背底灰度，加深文字黑色
            clean = np.clip((divided - 70.0) * (255.0 / (200.0 - 70.0)), 0, 255)

            # 阈值白化兜底：凡是大于 215 的灰阶强制设为纯白
            mask = clean > 215
            clean[mask] = 255

            whitened_img = Image.fromarray(clean.astype(np.uint8))

            # 4. 规范排版至标准 300DPI A4 纸张 (2480 x 3508)
            a4_w, a4_h = 2480, 3508
            canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))

            # 四周留 60px 打印安全边距
            margin = 60
            target_w, target_h = a4_w - margin * 2, a4_h - margin * 2

            ratio = min(target_w / whitened_img.width, target_h / whitened_img.height)
            new_w = int(whitened_img.width * ratio)
            new_h = int(whitened_img.height * ratio)

            resized = whitened_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))

            canvas.save(output_path, format="JPEG", quality=95)
            return True
    except Exception as e:
        print(f"[CamScannerEngine] 处理异常: {e}")
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
                # 勾选去黑底漂白时，执行全能王级漂白
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

            self.write_json(True, f"共 {len(files)} 个文件任务已按全能王标准排版打印", job=", ".join(jobs))
        except Exception as e:
            self.write_json(False, f"打印服务异常: {str(e)}")
