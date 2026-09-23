cat << 'EOF' > /tmp/idcard_handler.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import numpy as np
from PIL import Image, ImageOps
from handlers.base_handler import BaseHandler, UPLOAD_DIR

class IdCardPrintHandler(BaseHandler):
    @staticmethod
    def crop_single_card(pil_img, margin_factor=1.0):
        try:
            # 自动应用手机拍照 EXIF 姿态
            pil_img = ImageOps.exif_transpose(pil_img)

            # 强制横向排版：如果高度大于宽度，逆时针旋转 90 度
            if pil_img.height > pil_img.width:
                pil_img = pil_img.rotate(270, expand=True)

            gray = pil_img.convert("L")
            small = gray.copy()
            small.thumbnail((400, 400), Image.Resampling.BILINEAR)
            arr = np.array(small, dtype=np.uint8)

            border_pixels = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
            bg_val = np.median(border_pixels)

            diff = np.abs(arr.astype(np.int16) - bg_val)
            mask = diff > 25

            coords = np.argwhere(mask)
            if len(coords) < 100:
                return pil_img

            scale_y = pil_img.height / arr.shape[0]
            scale_x = pil_img.width / arr.shape[1]

            y0, x0 = coords.min(axis=0)
            y1, x1 = coords.max(axis=0)

            base_pad_x = (x1 - x0) * 0.03 * margin_factor
            base_pad_y = (y1 - y0) * 0.03 * margin_factor

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
            return pil_img.crop(crop_box)
        except Exception:
            return pil_img

    def post(self):
        try:
            printer = self.get_argument("printer", "")
            copies = self.get_argument("copies", "1")
            
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

            # 300DPI 标准国标尺寸：A4(2480x3508), 卡片(1012x638)
            a4_w, a4_h = 2480, 3508
            card_w, card_h = 1012, 638

            canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
            positions = [((a4_w - card_w) // 2, 580), ((a4_w - card_w) // 2, 1780)]
            factors = [crop_front, crop_back]

            for idx, path in enumerate(saved_paths):
                try:
                    with Image.open(path) as img:
                        cropped = self.crop_single_card(img, factors[idx] if idx < len(factors) else 1.0)
                        # 确保最终卡片必为国标横向
                        if cropped.height > cropped.width:
                            cropped = cropped.rotate(270, expand=True)
                        card_ready = cropped.resize((card_w, card_h), Image.Resampling.LANCZOS)
                        canvas.paste(card_ready, positions[idx])
                except Exception as e:
                    print(f"[IdCard] 处理异常: {e}")

            out_file = os.path.join(UPLOAD_DIR, f"idcard_final_{uuid.uuid4().hex[:8]}.jpg")
            canvas.save(out_file, format="JPEG", quality=90)

            res = self.execute_lp(printer, copies, out_file)
            if res.returncode == 0:
                self.write_json(True, "身份证双面合成打印任务已提交", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS 拒绝: {res.stderr.strip()}")
        except Exception as e:
            self.write_json(False, f"合成异常: {str(e)}")
EOF

docker cp /tmp/idcard_handler.py cups:/opt/webapp/handlers/idcard_handler.py
