#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image
from handlers.base_handler import BaseHandler, UPLOAD_DIR

class IdCardPrintHandler(BaseHandler):
    """身份证双面排版打印处理器：支持桌面背景智能裁剪与A4居中排版"""

    @staticmethod
    def crop_card_background(pil_img):
        """自动裁剪身份证外围桌面多余背景，提取卡片核心区域"""
        try:
            gray = pil_img.convert("L")
            # 缩放加速计算边界
            small = gray.copy()
            small.thumbnail((400, 400), Image.Resampling.BILINEAR)
            arr = np.array(small, dtype=np.uint8)

            # 获取四周边框样本估算背景亮度
            border_pixels = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
            bg_val = np.median(border_pixels)

            # 寻找与背景色差绝对值大于 25 的像素区域
            diff = np.abs(arr.astype(np.int16) - bg_val)
            mask = diff > 25

            coords = np.argwhere(mask)
            if len(coords) < 100:
                return pil_img

            # 换算回原图尺寸
            scale_y = pil_img.height / arr.shape[0]
            scale_x = pil_img.width / arr.shape[1]

            y0, x0 = coords.min(axis=0)
            y1, x1 = coords.max(axis=0)

            # 留出 2% 安全冗余边界
            pad_x = int((x1 - x0) * 0.02)
            pad_y = int((y1 - y0) * 0.02)

            crop_box = (
                max(0, int((x0 - pad_x) * scale_x)),
                max(0, int((y0 - pad_y) * scale_y)),
                min(pil_img.width, int((x1 + pad_x) * scale_x)),
                min(pil_img.height, int((y1 + pad_y) * scale_y))
            )
            cropped = pil_img.crop(crop_box)
            # 若裁剪后过于畸变则降级使用原图
            ratio = cropped.width / max(1, cropped.height)
            if 1.1 <= ratio <= 2.0:
                return cropped
            return pil_img
        except Exception:
            return pil_img

    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到身份证文件")
                return

            saved_paths = []
            for f in files[:2]:
                path = os.path.join(UPLOAD_DIR, f"id_raw_{uuid.uuid4().hex[:8]}.jpg")
                with open(path, "wb") as out:
                    out.write(f["body"])
                saved_paths.append(path)

            # 300DPI 下的标准 A4 纸与身份证规范像素尺寸
            a4_w, a4_h = 2480, 3508
            card_w, card_h = 1012, 638

            canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
            positions = [((a4_w - card_w) // 2, 580), ((a4_w - card_w) // 2, 1780)]

            for idx, path in enumerate(saved_paths):
                try:
                    with Image.open(path) as img:
                        # 1. 自动校准横向
                        if img.height > img.width:
                            img = img.rotate(270, expand=True)
                        # 2. 智能裁剪杂乱桌面背景
                        card_cropped = self.crop_card_background(img)
                        # 3. 规范缩放到国标身份证尺寸
                        card_ready = card_cropped.resize((card_w, card_h), Image.Resampling.LANCZOS)
                        canvas.paste(card_ready, positions[idx])
                except Exception:
                    pass

            out_file = os.path.join(UPLOAD_DIR, f"idcard_final_{uuid.uuid4().hex[:8]}.jpg")
            canvas.save(out_file, format="JPEG", quality=90)

            res = self.execute_lp(printer, copies, out_file)
            if res.returncode == 0:
                self.write_json(True, "身份证双面合成打印任务已发送", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS 错误: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"合成异常: {str(e)}")
