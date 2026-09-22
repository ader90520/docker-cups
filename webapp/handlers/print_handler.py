#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import subprocess
import json
import tornado.web

UPLOAD_DIR = "/tmp/cups_web_uploads"

def enhance_homework_image(input_path, output_path):
    """
    智能处理手机拍照作业/试卷：
    1. 自动根据文字倾斜角度做仿射纠偏 (Deskew)
    2. 局部阴影消除与纯白背景提取 (白底黑字高清省墨)
    """
    try:
        import cv2
        import numpy as np
        img = cv2.imread(input_path)
        if img is None:
            return False
            
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 1. 歪斜校正检测
        coords = np.column_stack(np.where(gray < 200))
        if coords.shape[0] > 50:
            angle = cv2.minAreaRect(coords)[-1]
            angle = -(90 + angle) if angle < -45 else -angle
            if 0.5 < abs(angle) < 15.0:
                (h, w) = img.shape[:2]
                center = (w // 2, h // 2)
                M = cv2.getRotationMatrix2D(center, angle, 1.0)
                gray = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

        # 2. 阴影消除与纯白背景提取 (形态学闭运算)
        dilated = cv2.dilate(gray, np.ones((7, 7), np.uint8))
        bg = cv2.medianBlur(dilated, 21)
        diff = 255 - cv2.absdiff(gray, bg)
        norm = cv2.normalize(diff, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)
        
        # 3. 增强字符对比度
        _, thresh = cv2.threshold(norm, 230, 255, cv2.THRESH_TRUNC)
        clean = cv2.normalize(thresh, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)

        cv2.imwrite(output_path, clean)
        return True
    except Exception:
        return False

class PrintUploadHandler(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            fitplot = self.get_argument("fitplot", "true").strip()
            enhance = self.get_argument("enhance", "false").strip().lower() == "true"
            
            if not printer:
                self.set_status(400)
                self.write(json.dumps({"success": False, "msg": "请选择目标打印机"}))
                return

            file_metas = self.request.files.get('file', None)
            if not file_metas:
                self.set_status(400)
                self.write(json.dumps({"success": False, "msg": "未找到上传的文件"}))
                return

            meta = file_metas[0]
            ext = os.path.splitext(meta['filename'])[1].lower()
            save_name = f"print_{int(time.time())}_{meta['filename']}"
            save_path = os.path.join(UPLOAD_DIR, save_name)

            with open(save_path, 'wb') as f:
                f.write(meta['body'])

            final_print_file = save_path
            if enhance and ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]:
                enhanced_path = os.path.join(UPLOAD_DIR, f"enhanced_{save_name}.png")
                if enhance_homework_image(save_path, enhanced_path):
                    final_print_file = enhanced_path

            lp_cmd = ["lp", "-d", printer, "-n", copies]
            if fitplot.lower() == "true":
                lp_cmd.extend(["-o", "fit-to-page"])
            lp_cmd.append(final_print_file)

            res = subprocess.run(lp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                self.set_status(500)
                self.write(json.dumps({"success": False, "msg": f"打印失败: {res.stderr}"}))
            else:
                self.write(json.dumps({"success": True, "msg": f"任务已提交: {res.stdout.strip()}"}))

            for p in [save_path, os.path.join(UPLOAD_DIR, f"enhanced_{save_name}.png")]:
                if os.path.exists(p):
                    try: os.remove(p)
                    except Exception: pass

        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))
