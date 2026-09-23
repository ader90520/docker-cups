#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image
from handlers.base_handler import BaseHandler, UPLOAD_DIR

class PrintHandler(BaseHandler):
    """通用/试卷打印处理器"""
    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            mode = self.get_argument("mode", "normal")  # normal / exam
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到文件")
                return

            f = files[0]
            ext = os.path.splitext(f["filename"])[-1].lower()
            token = uuid.uuid4().hex[:8]
            src_path = os.path.join(UPLOAD_DIR, f"print_{token}{ext}")
            with open(src_path, "wb") as out:
                out.write(f["body"])

            target_path = src_path

            # 试卷去黑底模式（NumPy 矢量加速计算，耗时 < 0.2 秒）
            if mode == "exam" and ext in [".jpg", ".jpeg", ".png"]:
                try:
                    with Image.open(src_path).convert("L") as img:
                        # 降分辨率保护（如果原图超过 2000px，适度降采样提升运算速度）
                        if max(img.size) > 2000:
                            img.thumbnail((2000, 2000), Image.Resampling.BILINEAR)
                        arr = np.array(img, dtype=np.float32)
                        # 阶梯拉伸：浅灰强制漂白，深墨色强化
                        arr = np.clip((arr - 50) * (255.0 / (205 - 50)), 0, 255).astype(np.uint8)
                        target_path = os.path.join(UPLOAD_DIR, f"exam_{token}.jpg")
                        Image.fromarray(arr).save(target_path, quality=85)
                except Exception:
                    target_path = src_path

            res = self.execute_lp(printer, copies, target_path)
            if res.returncode == 0:
                self.write_json(True, "任务派发成功", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")

        except Exception as e:
            self.write_json(False, f"打印服务异常: {str(e)}")
