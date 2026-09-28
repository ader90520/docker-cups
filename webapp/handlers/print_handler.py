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

def dewarp_textlines_flatten(pil_img):
    """
    自适应文本行展平（类似扫描全能王逐列位移补偿）
    拉直页面拱起引起的波浪文字行
    """
    try:
        w, h = pil_img.size
        # 降采样提取全局几何形变
        target_w = 400
        scale = target_w / float(w)
        target_h = int(h * scale)

        gray = pil_img.convert("L").resize((target_w, target_h), Image.Resampling.BILINEAR)
        arr = np.array(gray, dtype=np.float32)

        # 粗背景去除，定位墨迹笔画
        bg = gray.filter(ImageFilter.BoxBlur(radius=12))
        bg_arr = np.array(bg, dtype=np.float32) + 1.0
        div = (arr / bg_arr) * 255.0
        ink = div < 190.0

        # 横向形态学膨胀：把同行的拼音与字连成水平色块
        kernel_w = 25
        kernel = np.ones(kernel_w, dtype=np.float32)
        dilated = np.zeros_like(ink, dtype=bool)
        for r in range(0, target_h, 2):
            row = ink[r, :]
            if np.any(row):
                dilated[r, :] = np.convolve(row.astype(np.float32), kernel, mode='same') > 0.1

        # 沿 X 轴切成 16 个竖条，抓取每条中文字行的垂向中心
        num_strips = 16
        strip_w = target_w // num_strips
        
        # 统计行黑度，寻找具有代表性的主文字行
        row_density = np.mean(dilated, axis=1)
        valid_rows = np.where(row_density > 0.08)[0]

        if len(valid_rows) >= 15:
            # 划分为 3~4 个纵向分块，追踪各区段的弧度
            sections = np.array_split(valid_rows, 4)
            curves = []

            for sec in sections:
                if len(sec) < 3:
                    continue
                y_list = []
                for s in range(num_strips):
                    col_start = s * strip_w
                    col_end = min((s + 1) * strip_w, target_w)
                    sub = dilated[sec[0]:sec[-1], col_start:col_end]
                    if np.sum(sub) > 5:
                        y_idxs, _ = np.where(sub)
                        y_list.append(np.mean(y_idxs) + sec[0])
                    else:
                        y_list.append(np.nan)

                y_arr = np.array(y_list)
                valid = ~np.isnan(y_arr)
                if np.sum(valid) >= 8:
                    xs = np.arange(num_strips)[valid]
                    ys = y_arr[valid]
                    # 拟合该行的下垂抛物线
                    p = np.polyfit(xs, ys - np.mean(ys), 2)
                    smooth_c = np.polyval(p, np.arange(num_strips))
                    curves.append(smooth_c)

            if curves:
                # 取中位数避免个别图形与拼音四线格干扰
                median_curve = np.median(curves, axis=0)
                # 插值映射到原图全宽 w
                x_coarse = np.linspace(0, w, num_strips)
                x_fine = np.arange(w)
                disp_fine = np.interp(x_fine, x_coarse, median_curve) / scale
                disp_fine -= np.mean(disp_fine)

                # 垂直拉平图像
                full_arr = np.array(pil_img)
                out_arr = np.full_like(full_arr, 255)

                for x in range(w):
                    shift = int(round(disp_fine[x]))
                    if shift > 0:
                        out_arr[:-shift, x] = full_arr[shift:, x]
                    elif shift < 0:
                        out_arr[-shift:, x] = full_arr[:shift, x]
                    else:
                        out_arr[:, x] = full_arr[:, x]

                log_debug(f"文本行曲面拉直成功完成，补偿幅度: {np.ptp(disp_fine):.1f}px")
                return Image.fromarray(out_arr)

    except Exception as e:
        log_debug(f"文本拉直降级: {e}")
    return pil_img

def process_image_for_print(input_path, output_path):
    """
    全能王级漂白与抗透墨处理引擎
    1. EXIF方向校正与竖排对齐
    2. 多行骨架弯曲文字横平拉直
    3. 灰度大核除法 + 激进透字压白
    4. 保全正面字迹、人物线条与四线格
    """
    try:
        log_debug(f"开始处理图像: {input_path}")
        with Image.open(input_path) as disk_img:
            img = ImageOps.exif_transpose(disk_img.convert("RGB"))

        # 竖向排版对齐
        if img.width > img.height:
            img = img.rotate(270, expand=True)

        # 1. 弯曲文字展平
        img = dewarp_textlines_flatten(img)

        # 限制分辨率防小设备溢出
        if max(img.size) > 2200:
            img.thumbnail((2200, 2200), Image.Resampling.BILINEAR)

        # 2. 彻底剥离彩色通道，转纯灰度运算
        gray = img.convert("L")
        gray_arr = np.array(gray, dtype=np.float32)

        # 3. 超大半径 BoxBlur (45px)：彻底吸纳背面透墨为基底光照
        bg = gray.filter(ImageFilter.BoxBlur(radius=45))
        bg_arr = np.array(bg, dtype=np.float32) + 1.0

        # 背景除法归一化
        divided = (gray_arr / bg_arr) * 255.0

        # 4. 全能王核心阈值分层处理：
        # divided < 192: 绝对是正面清晰文字或四线格
        # 192 <= divided < 218: 背面透字（浅灰影子）、纸质轻微发黄
        # divided >= 218: 纯白纸面
        out = np.full_like(divided, 255.0)

        # 彻底切除背面透字：阈值收紧到 195
        mask_front = divided < 195.0

        ink_vals = divided[mask_front]
        # 对正面字迹重新拉伸：深黑字更黑，浅细线条保全
        clean_ink = np.clip((ink_vals - 20.0) * (200.0 / (195.0 - 20.0)), 0, 255)
        clean_ink = (clean_ink / 200.0) ** 1.25 * 180.0
        out[mask_front] = clean_ink

        clean_gray = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

        # 5. 微锐化还原字迹边缘印刷感
        sharp = clean_gray.filter(ImageFilter.UnsharpMask(radius=1.2, percent=130, threshold=2))
        sharp_rgb = Image.merge("RGB", [sharp, sharp, sharp])

        sharp_rgb.save(output_path, format="JPEG", quality=95, dpi=(300, 300))
        log_debug(f"图像增强与去透字完成，保存至: {output_path}")
        return True

    except Exception as e:
        log_debug(f"处理失败异常: {e}")
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
            whiten = self.get_argument("whiten", "0").strip()
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

                # 无论前端传参是 whiten=1 还是图片上传，都经过增强引擎
                if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
                    enhanced_path = os.path.join(UPLOAD_DIR, f"opt_{token}.jpg")
                    # 默认强制执行去透字与水平展平
                    if process_image_for_print(src_path, enhanced_path):
                        target_file = enhanced_path
                    else:
                        log_debug("增强失败，采用基础EXIF回正输出")
                        with Image.open(src_path) as raw_img:
                            im = ImageOps.exif_transpose(raw_img.convert("RGB"))
                            im.save(enhanced_path, format="JPEG", quality=95, dpi=(300, 300))
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
