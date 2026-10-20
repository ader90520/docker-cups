#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import uuid
import subprocess
import numpy as np
from PIL import Image, ImageOps
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.enhance_handler import enhance_camscanner_precise

ALLOWED_PRINT_EXTS = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.webp', '.heic'}

CANVAS_SIZES_200DPI = {
    "A4": (1654, 2338),
    "A5": (1169, 1654),
    "B5": (1385, 1968),
    "A6": (827, 1169)
}

def log_debug(msg):
    try:
        with open("/tmp/dewarp_debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(f"[PrintLog] {msg}", flush=True)

def fit_to_a4_fast_frame(img, fill_ratio=0.96, media="A4"):
    media_key = media.upper() if media.upper() in CANVAS_SIZES_200DPI else "A4"
    target_w, target_h = CANVAS_SIZES_200DPI[media_key]
    orig_w, orig_h = img.size

    ratio = min((target_w * fill_ratio) / orig_w, (target_h * fill_ratio) / orig_h)
    new_w = max(1, int(orig_w * ratio))
    new_h = max(1, int(orig_h * ratio))

    resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
    pos_x = (target_w - new_w) // 2
    pos_y = (target_h - new_h) // 2
    canvas.paste(resized_img, (pos_x, pos_y))
    return canvas

def merge_images_n_up(image_paths, media="A4"):
    media_key = media.upper() if media.upper() in CANVAS_SIZES_200DPI else "A4"
    page_w, page_h = CANVAS_SIZES_200DPI[media_key]
    canvas = Image.new("RGB", (page_w, page_h), (255, 255, 255))

    count = len(image_paths)
    if count == 1:
        with Image.open(image_paths[0]) as im:
            return fit_to_a4_fast_frame(ImageOps.exif_transpose(im).convert("RGB"), fill_ratio=0.96, media=media)

    if count == 2:
        cols, rows = 1, 2
    elif count in (3, 4):
        cols, rows = 2, 2
    elif count in (5, 6):
        cols, rows = 2, 3
    else:
        cols, rows = 2, 4

    cell_w = page_w // cols
    cell_h = page_h // rows
    margin = 35

    for idx, img_p in enumerate(image_paths[:cols * rows]):
        try:
            with Image.open(img_p) as raw_img:
                img = ImageOps.exif_transpose(raw_img).convert("RGB")
                ratio = min((cell_w - margin * 2) / img.width, (cell_h - margin * 2) / img.height)
                target_w = max(10, int(img.width * ratio))
                target_h = max(10, int(img.height * ratio))
                resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

                col = idx % cols
                row = idx // cols
                pos_x = col * cell_w + (cell_w - target_w) // 2
                pos_y = row * cell_h + (cell_h - target_h) // 2
                canvas.paste(resized, (pos_x, pos_y))
        except Exception as e:
            log_debug(f"单图拼版受阻: {e}")

    return canvas

def execute_camscanner_engine(input_path, output_path, media="A4", color_mode="monochrome"):
    """直接接入 enhance_handler 的工业级 OpenCV 扫描全能王管线"""
    try:
        t0 = time.time()
        with open(input_path, "rb") as f:
            raw_bytes = f.read()

        # 调用已验证生效的全局扫描王处理模块
        processed_bytes = enhance_camscanner_precise(raw_bytes, color_mode=color_mode)
        
        # 封装进标准页面画幅
        import io
        pil_img = Image.open(io.BytesIO(processed_bytes)).convert("RGB")
        final_canvas = fit_to_a4_fast_frame(pil_img, fill_ratio=0.96, media=media)
        final_canvas.save(output_path, format="JPEG", quality=95, dpi=(200, 200))

        log_debug(f"✔ 打印图像经扫描王引擎增强完成: {time.time() - t0:.2f}s -> {output_path}")
        return True
    except Exception as e:
        log_debug(f"扫描王打印增强异常: {e}")
        return False

def clean_old_tmp_files(directory, max_age_seconds=1800):
    try:
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            return
        now = time.time()
        for f in os.listdir(directory):
            p = os.path.join(directory, f)
            if os.path.isfile(p) and (now - os.path.getmtime(p) > max_age_seconds):
                try:
                    os.remove(p)
                except Exception:
                    pass
    except Exception:
        pass

class PrintHandler(BaseHandler):
    def post(self):
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            duplex = self.get_argument("duplex", "none").strip()
            color_mode = self.get_argument("color_mode", "monochrome").strip()
            media = self.get_argument("media", "A4").strip()
            enhance = self.get_argument("enhance", "true").strip().lower() == "true"
            page_ranges = self.get_argument("page_ranges", "").strip()
            multi_merge = self.get_argument("multi_merge", "false").strip().lower() == "true"
            files = self.request.files.get("file", [])

            if not files:
                self.write_json(False, "未收到上传文件")
                return

            if not printer or printer.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:+]+$', printer):
                self.write_json(False, "非法打印机设备名称")
                return

            try:
                copies_int = int(copies)
                if not (1 <= copies_int <= 99):
                    copies_int = 1
            except ValueError:
                copies_int = 1

            if media.startswith("-") or not re.match(r'^[a-zA-Z0-9_\-]+$', media):
                media = "A4"

            os.makedirs(UPLOAD_DIR, exist_ok=True)
            jobs = []
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            image_raw_paths = []
            pdf_files = []
            other_files = []

            for f in files:
                raw_ext = os.path.splitext(f["filename"])[-1].lower()
                clean_ext = re.sub(r'[^a-zA-Z0-9.]', '', raw_ext)

                if clean_ext not in ALLOWED_PRINT_EXTS:
                    self.write_json(False, f"不支持的文件格式: {clean_ext}")
                    return

                token = uuid.uuid4().hex[:8]
                src_path = os.path.join(UPLOAD_DIR, f"raw_{token}{clean_ext}")
                with open(src_path, "wb") as out:
                    out.write(f["body"])

                if clean_ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
                    image_raw_paths.append(src_path)
                elif clean_ext == ".pdf":
                    pdf_files.append(src_path)
                else:
                    other_files.append(src_path)

            tasks = []

            if image_raw_paths:
                if multi_merge and len(image_raw_paths) > 1:
                    merged_canvas = merge_images_n_up(image_raw_paths, media=media)
                    merged_path = os.path.join(UPLOAD_DIR, f"merged_{uuid.uuid4().hex[:8]}.jpg")
                    merged_canvas.save(merged_path, format="JPEG", quality=95, dpi=(200, 200))
                    
                    if enhance:
                        opt_path = os.path.join(UPLOAD_DIR, f"opt_merged_{uuid.uuid4().hex[:8]}.jpg")
                        if execute_camscanner_engine(merged_path, opt_path, media=media, color_mode=color_mode):
                            tasks.append({"path": opt_path, "is_pdf": False})
                        else:
                            tasks.append({"path": merged_path, "is_pdf": False})
                    else:
                        tasks.append({"path": merged_path, "is_pdf": False})
                else:
                    for src_path in image_raw_paths:
                        target_f = src_path
                        if enhance:
                            token = uuid.uuid4().hex[:8]
                            opt_path = os.path.join(UPLOAD_DIR, f"opt_{token}.jpg")
                            if execute_camscanner_engine(src_path, opt_path, media=media, color_mode=color_mode):
                                target_f = opt_path
                        tasks.append({"path": target_f, "is_pdf": False})

            for p_f in pdf_files:
                tasks.append({"path": p_f, "is_pdf": True})

            for o_f in other_files:
                tasks.append({"path": o_f, "is_pdf": False})

            for item in tasks:
                target_file = item["path"]
                is_pdf = item["is_pdf"]

                cmd = [
                    "lp",
                    "-d", printer,
                    "-n", str(copies_int),
                    "-o", f"media={media}",
                    "-o", f"PageSize={media}",
                    "-o", "fit-to-page",
                    "-o", "scaling=100",
                    "-o", "position=center"
                ]

                if color_mode == "color":
                    cmd.extend(["-o", "ColorModel=RGB", "-o", "print-color-mode=color"])
                else:
                    cmd.extend(["-o", "ColorModel=K", "-o", "ColorModel=Gray", "-o", "print-color-mode=monochrome"])

                if duplex == "long":
                    cmd.extend(["-o", "sides=two-sided-long-edge"])
                elif duplex == "short":
                    cmd.extend(["-o", "sides=two-sided-short-edge"])

                if is_pdf and page_ranges and re.match(r'^[0-9,\-]+$', page_ranges):
                    cmd.extend(["-o", f"page-ranges={page_ranges}"])

                cmd.append(target_file)
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=45)

                if res.returncode == 0:
                    jobs.append(res.stdout.strip())
                else:
                    self.write_json(False, f"CUPS拒绝: {res.stderr.strip()}")
                    return

            clean_old_tmp_files(UPLOAD_DIR)
            self.write_json(True, f"共 {len(tasks)} 个文件成功送达打印队列", job=", ".join(jobs))
        except subprocess.TimeoutExpired:
            self.write_json(False, "打印任务派发超时，打印机可能处于离线、卡纸或通信受阻状态")
        except Exception as e:
            log_debug(f"打印服务异常: {e}")
            self.write_json(False, f"打印服务异常: {str(e)}")
