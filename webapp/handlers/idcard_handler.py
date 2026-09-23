#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image
from handlers.base_handler import BaseHandler, UPLOAD_DIR

class IdCardPrintHandler(BaseHandler):
    """
    身份证双面排版打印处理器：
    支持前端动态裁剪幅度调节、智能背景去杂物与 300DPI 国标 A4 打印
    """

    @staticmethod
    def crop_card_with_margin(pil_img, margin_factor=1.0):
        """
        根据用户在前端调节的幅度，智能识别卡片核心并动态裁剪多余边缘
        margin_factor: 1.0 为标准紧凑裁剪，>1.0 保留更多周边，<1.0 深度收紧裁边
        """
        try:
            gray = pil_img.convert("L")
            small = gray.copy()
            small.thumbnail((400, 400), Image.Resampling.BILINEAR)
            arr = np.array(small, dtype=np.uint8)

            # 采样周边背景灰度
            border_pixels = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
            bg_val = np.median(border_pixels)

            # 计算色差核心目标区域
            diff = np.abs(arr.astype(np.int16) - bg_val)
            mask = diff > 25

            coords = np.argwhere(mask)
            if len(coords) < 100:
                # 找不到明显边界则直接使用原图
                return pil_img

            scale_y = pil_img.height / arr.shape[0]
            scale_x = pil_img.width / arr.shape[1]

            y0, x0 = coords.min(axis=0)
            y1, x1 = coords.max(axis=0)

            # 根据前端滑块动态浮动边距 (默认 2% 基础冗余 * 用户幅度因子)
            base_pad_x = (x1 - x0) * 0.03 * margin_factor
            base_pad_y = (y1 - y0) * 0.03 * margin_factor

            # 计算裁剪矩形
            cx = (x0 + x1) / 2.0
            cy = (y0 + y1) / 2.0
            half_w = ((x1 - x0) / 2.0 + base_pad_x) * scale_x
            half_h = ((y1 - y0) / 2.0 + base_pad_y) * scale_y

            crop_box = (
                max(0, int(cx * scale_x - half_w)),
                max(0, int(cy * scale_y - half_h)),
                min(pil_img.width, int(cx * scale_x + half_w)),
                min(pil_img.height, int(cy * scale_y + half_h))
            )
            cropped = pil_img.crop(crop_box)
            return cropped
        except Exception:
            return pil_img

    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            # 接收前端动态裁剪幅度参数
            margin_factor = float(self.get_argument("crop_margin", "1.0"))
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

            # 300 DPI 下的标准 A4 纸 (2480 x 3508)
            # 国标二代身份证标准物理尺寸 85.6mm x 54mm -> 300DPI 下为 1012 x 638 像素
            a4_w, a4_h = 2480, 3508
            card_w, card_h = 1012, 638

            canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
            positions = [((a4_w - card_w) // 2, 580), ((a4_w - card_w) // 2, 1780)]

            for idx, path in enumerate(saved_paths):
                try:
                    with Image.open(path) as img:
                        # 1. 自动根据宽高比纠正为横向卡片
                        if img.height > img.width:
                            img = img.rotate(270, expand=True)

                        # 2. 依据动态窗口幅度进行背景裁剪
                        card_cropped = self.crop_card_with_margin(img, margin_factor)

                        # 3. 按真实国标卡片像素尺寸输出，防止畸变变形
                        card_ready = card_cropped.resize((card_w, card_h), Image.Resampling.LANCZOS)
                        canvas.paste(card_ready, positions[idx])
                except Exception as e:
                    print(f"[IdCard] 单张处理异常: {e}")

            out_file = os.path.join(UPLOAD_DIR, f"idcard_final_{uuid.uuid4().hex[:8]}.jpg")
            canvas.save(out_file, format="JPEG", quality=90)

            # 提交 CUPS 打印
            res = self.execute_lp(printer, copies, out_file)
            if res.returncode == 0:
                self.write_json(True, "身份证已按动态裁剪视口成功排版打印", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS 拒绝: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"合成处理异常: {str(e)}")
