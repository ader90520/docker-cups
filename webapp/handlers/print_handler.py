#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import subprocess
import tornado.web
from PIL import Image, ImageEnhance, ImageFilter
import numpy as np

UPLOAD_DIR = "/tmp/cups_web_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def enhance_homework_image(input_path, output_path):
    """
    轻量级试卷/文档增强算法 (纯 PIL + NumPy 矩阵运算)
    无任何庞大第三方 C 库依赖，秒级去阴影、背景漂白、字迹锐化
    """
    try:
        with Image.open(input_path) as img:
            gray = img.convert('L')
            arr = np.array(gray, dtype=np.float32)

            # 极大值滤波模拟局部光照背景
            bg = gray.filter(ImageFilter.MaxFilter(25))
            bg_arr = np.array(bg, dtype=np.float32)
            bg_arr[bg_arr < 1.0] = 1.0

            # 差分光照除法，消灭大面积阴影与底灰
            normalized = (arr / bg_arr) * 255.0
            normalized = np.clip(normalized, 0, 255).astype(np.uint8)

            result = Image.fromarray(normalized)

            # 对比度强化与笔画边缘锐化
            enh_contrast = ImageEnhance.Contrast(result)
            result = enh_contrast.enhance(1.8)
            result = result.filter(ImageFilter.SHARPEN)

            result.save(output_path, "PNG", optimize=True)
            return True
    except Exception as e:
        print(f"[Enhance Error] 图像处理异常: {e}")
        return False

class PrintHandler(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            printer = self.get_body_argument("printer", "")
            copies = self.get_body_argument("copies", "1")
            fitplot = self.get_body_argument("fitplot", "true")
            enhance = self.get_body_argument("enhance", "false") == "true"

            if not printer:
                self.write({"success": False, "msg": "未指定打印机！"})
                return

            if 'file' not in self.request.files:
                self.write({"success": False, "msg": "未上传文件！"})
                return

            upload_file = self.request.files['file'][0]
            filename = upload_file['filename']
            ext = os.path.splitext(filename)[1].lower()

            save_path = os.path.join(UPLOAD_DIR, f"{int(time.time())}_{filename}")
            with open(save_path, "wb") as f:
                f.write(upload_file['body'])

            final_print_file = save_path
            # 若勾选增强且为常见格式
            if enhance and ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]:
                enhanced_path = os.path.join(UPLOAD_DIR, f"enh_{int(time.time())}.png")
                if enhance_homework_image(save_path, enhanced_path):
                    final_print_file = enhanced_path

            cmd = ["lp", "-d", printer, "-n", str(copies)]
            if fitplot.lower() == "true":
                cmd.extend(["-o", "fit-to-page"])
            cmd.append(final_print_file)

            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)

            # 清理临时文件
            for p in [save_path, os.path.join(UPLOAD_DIR, f"enh_{int(time.time())}.png")]:
                if os.path.exists(p):
                    try: os.remove(p)
                    except Exception: pass

            if res.returncode == 0:
                self.write({"success": True, "msg": f"打印任务提交成功！(Job: {res.stdout.strip()})"})
            else:
                self.write({"success": False, "msg": f"打印错误: {res.stderr.strip()}"})

        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "msg": str(e)})

# 兼容两种命名导入，彻底根治 ImportError
PrintUploadHandler = PrintHandler
