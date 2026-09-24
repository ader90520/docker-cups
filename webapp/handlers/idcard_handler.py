#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image, ImageOps
from handlers.base_handler import BaseHandler, UPLOAD_DIR

class IdCardPrintHandler(BaseHandler):
    """
    身份证双面排版打印处理器：
    支持前端视口精准裁剪、桌面杂边物理截除与 300DPI 国标 A4 打印
    """

    @staticmethod
    def crop_by_viewport(pil_img, zoom_factor=1.0):
        """
        根据前端取景视口真实裁剪：
        zoom_factor > 1.0 表示拉近镜头（切除外部更多杂物）；
        zoom_factor < 1.0 表示拉远镜头（保留更广周边）。
        """
        try:
            # 1. 纠正手机拍照自带的 EXIF 方向
            pil_img = ImageOps.exif_transpose(pil_img)

            # 2. 保证卡片为横向角度
            if pil_img.height > pil_img.width:
                pil_img = pil_img.rotate(270, expand=True)

            w, h = pil_img.size

            # 国标二代身份证宽高比为 85.6 / 54.0 ≈ 1.585
            target_ratio = 85.6 / 54.0
            cur_ratio = w / h

            if cur_ratio > target_ratio:
                # 图像过宽，以高为基准
                box_h = h
                box_w = int(h * target_ratio)
            else:
                # 图像过高，以宽为基准
                box_w = w
                box_h = int(w / target_ratio)

            # 应用用户调节的取景视口缩放 (Zoom)
            factor = max(0.5, float(zoom_factor))
            crop_w = int(box_w / factor)
            crop_h = int(box_h / factor)

            # 防止超出原图边界
            crop_w = min(w, crop_w)
            crop_h = min(h, crop_h)

            cx, cy = w // 2, h // 2
            x0 = max(0, cx - crop_w // 2)
            y0 = max(0, cy - crop_h // 2)
            x1 = min(w, x0 + crop_w)
            y1 = min(h, y0 + crop_h)

            return pil_img.crop((x0, y0, x1, y1))
        except Exception:
            return pil_img

    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            
            # 获取前端视口取景比例参数
            crop_front = float(self.get_argument("crop_front", "1.0"))
            crop_back = float(self.get_argument("crop_back", "1.0"))

            files = self.request.files.get("file", [])
            if not files:
                self.write_json(False, "未收到身份证图片")
                return

            saved_paths = []
            for f in files[:2]:
                p = os.path.join(UPLOAD_DIR, f"id_raw_{uuid.uuid4().hex[:8]}.jpg")
                with open(p, "wb") as out:
                    out.write(f["body"])
                saved_paths.append(p)

            # 300DPI 标准国标尺寸：A4 纸张 (2480x3508)，身份证 (1012x638)
            a4_w, a4_h = 2480, 3508
            card_w, card_h = 1012, 638

            canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
            # 居中排版位置：正面靠上，反面靠下
            positions = [((a4_w - card_w) // 2, 580), ((a4_w - card_w) // 2, 1780)]
            zoom_list = [crop_front, crop_back]

            for idx, path in enumerate(saved_paths):
                try:
                    with Image.open(path) as img:
                        # 真正基于视口窗口大小物理截取
                        cropped = self.crop_by_viewport(img, zoom_list[idx] if idx < len(zoom_list) else 1.0)
                        card_ready = cropped.resize((card_w, card_h), Image.Resampling.LANCZOS)
                        canvas.paste(card_ready, positions[idx])
                except Exception as e:
                    print(f"[IdCard] 裁剪排版异常: {e}")

            out_file = os.path.join(UPLOAD_DIR, f"idcard_final_{uuid.uuid4().hex[:8]}.jpg")
            canvas.save(out_file, format="JPEG", quality=92)

            res = self.execute_lp(printer, copies, out_file)
            if res.returncode == 0:
                self.write_json(True, "身份证双面合成打印任务已提交", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS 拒绝: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"合成处理异常: {str(e)}")
