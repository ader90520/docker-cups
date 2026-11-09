#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import io
import time
import uuid
import subprocess
import numpy as np
from PIL import Image
from handlers.base_handler import BaseHandler, UPLOAD_DIR, get_cups_env

HAVE_OPENCV = False
try:
    import cv2
    HAVE_OPENCV = True
except Exception:
    HAVE_OPENCV = False

def ensure_upload_dir():
    try:
        if not os.path.exists(UPLOAD_DIR):
            os.makedirs(UPLOAD_DIR, mode=0o777, exist_ok=True)
    except Exception as e:
        print(f"[UploadDir] 创建目录失败: {e}", flush=True)

def detect_and_crop_paper_content(img):
    """打印端切边：基于亮度投影彻底切除非纸面杂质"""
    if img is None:
        return img

    h, w = img.shape[:2]
    scale = 300.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    row_means = np.mean(gray, axis=1)
    mid_brightness = np.median(row_means)
    thresh_val = max(55.0, mid_brightness * 0.65)

    top = 0
    for r in range(len(row_means)):
        if row_means[r] >= thresh_val:
            top = r
            break

    bottom = len(row_means) - 1
    for r in range(len(row_means) - 1, -1, -1):
        if row_means[r] >= thresh_val:
            bottom = r
            break

    col_means = np.mean(gray, axis=0)
    left = 0
    for c in range(len(col_means)):
        if col_means[c] >= thresh_val:
            left = c
            break

    right = len(col_means) - 1
    for c in range(len(col_means) - 1, -1, -1):
        if col_means[c] >= thresh_val:
            right = c
            break

    y1 = int(top / scale)
    y2 = int(bottom / scale)
    x1 = int(left / scale)
    x2 = int(right / scale)

    if (y2 - y1) < h * 0.5 or (x2 - x1) < w * 0.5:
        pad_x, pad_y = int(w * 0.015), int(h * 0.015)
        y1, y2, x1, x2 = pad_y, h - pad_y, pad_x, w - pad_x

    y1 = min(h - 10, y1 + 4)
    y2 = max(y1 + 10, y2 - 4)
    x1 = min(w - 10, x1 + 4)
    x2 = max(x1 + 10, x2 - 4)

    cropped = img[y1:y2, x1:x2]
    return cv2.copyMakeBorder(cropped, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=[255, 255, 255])

def render_crisp_pure_document(img, is_color=False, density="dark"):
    h, w = img.shape[:2]

    tiny_scale = 320.0 / max(h, w)
    tiny_img = cv2.resize(img, (int(w * tiny_scale), int(h * tiny_scale)), interpolation=cv2.INTER_NEAREST)
    bg_tiny = cv2.GaussianBlur(tiny_img, (0, 0), sigmaX=8, sigmaY=8)
    bg = cv2.resize(bg_tiny, (w, h), interpolation=cv2.INTER_LINEAR)

    norm = cv2.divide(img, bg, scale=255)

    if not is_color:
        gray = np.min(norm, axis=2).astype(np.float32)

        # 显著拉开档位反差，使肉眼及出纸对比极其明显
        if density == "ultra":
            black_point = 55.0
            white_point = 195.0
            gamma = 0.65
            extra_erode = True
        elif density == "normal":
            black_point = 10.0
            white_point = 220.0
            gamma = 0.95
            extra_erode = False
        else:  # dark (推荐)
            black_point = 35.0
            white_point = 210.0
            gamma = 0.80
            extra_erode = False

        scaled = np.clip((gray - black_point) / (white_point - black_point), 0.0, 1.0)
        v_out = np.power(scaled, gamma) * 255.0
        result = np.clip(v_out, 0, 255).astype(np.uint8)
        result[result >= 235] = 255

        if extra_erode:
            kernel = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8)
            eroded = cv2.erode(result, kernel, iterations=1)
            result = cv2.addWeighted(result, 0.4, eroded, 0.6, 0)

        return cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    else:
        hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV).astype(np.float32)
        s = hsv[:, :, 1]
        v = hsv[:, :, 2]

        color_mask = (s > 15) & (v > 35)
        hsv[:, :, 1] = np.where(color_mask, np.clip(s * 1.30, 0, 255), s)
        v_stretched = np.clip((v - 25.0) / (215.0 - 25.0) * 255.0, 0, 255)
        hsv[:, :, 2] = np.where(color_mask, v, v_stretched)

        result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        gray_chk = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
        result[gray_chk >= 235] = [255, 255, 255]
        return result

def process_image_fast_enhance(in_path, out_path, color_mode="monochrome", density="dark"):
    if not HAVE_OPENCV:
        return False

    try:
        raw_img = cv2.imread(in_path, cv2.IMREAD_COLOR)
        if raw_img is None:
            return False

        # 1. 投影切边彻底清除多余桌面
        img = detect_and_crop_paper_content(raw_img)
        h, w = img.shape[:2]

        # 2. 一次性保持 2600px 高分辨率输出
        max_dim = max(h, w)
        if max_dim > 2600:
            scale = 2600.0 / max_dim
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        is_color = (str(color_mode).strip().lower() == "color")
        result = render_crisp_pure_document(img, is_color=is_color, density=density)

        # 3. 无损 PNG 输出，送给 CUPS
        cv2.imwrite(out_path, result)
        return True
    except Exception as e:
        print(f"[FastEnhance] 出纸增强异常: {e}", flush=True)
        return False

def clean_old_tmp_files(upload_dir, max_age=60):
    try:
        if not os.path.exists(upload_dir):
            return
        now = time.time()
        for f in os.listdir(upload_dir):
            p = os.path.join(upload_dir, f)
            if os.path.isfile(p) and (now - os.path.getmtime(p) > max_age):
                try: 
                    os.remove(p)
                except Exception: 
                    pass
    except Exception:
        pass

class PrintHandler(BaseHandler):
    def post(self):
        saved_files = []
        try:
            ensure_upload_dir()

            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            duplex = self.get_argument("duplex", "none").strip()
            color_mode = self.get_argument("color_mode", "monochrome").strip().lower()
            media = self.get_argument("media", "A4").strip().upper()
            enhance_val = self.get_argument("enhance", "false").strip().lower()
            density = self.get_argument("density", "dark").strip().lower()
            page_ranges = self.get_argument("page_ranges", "").strip()
            multi_merge = self.get_argument("multi_merge", "false").strip().lower() == "true"
            rotation = int(self.get_argument("rotation", "0").strip() or 0)

            need_enhance = (enhance_val in ["true", "1", "yes", "on"])

            if not printer:
                self.write_json(False, "未选定物理打印机")
                return

            files = self.request.files.get("file", [])
            if not files:
                self.write_json(False, "未收到上传文件")
                return

            extra_opts = [
                f"media={media}",
                f"PageSize={media}",
            ]
            if duplex == "long":
                extra_opts.append("sides=two-sided-long-edge")
            elif duplex == "short":
                extra_opts.append("sides=two-sided-short-edge")
            else:
                extra_opts.append("sides=one-sided")

            if color_mode == "monochrome":
                extra_opts.extend(["ColorModel=K", "print-color-mode=monochrome"])
            else:
                extra_opts.extend(["ColorModel=RGB", "print-color-mode=color"])

            if page_ranges:
                extra_opts.append(f"page-ranges={page_ranges}")

            if rotation in [90, 180, 270]:
                extra_opts.append(f"orientation-requested={(rotation // 90) + 3}")

            job_ids = []

            # 模式 A: 多图拼版合为单张输出
            if multi_merge and len(files) > 1:
                token = uuid.uuid4().hex[:8]
                merged_path = os.path.join(UPLOAD_DIR, f"merged_{token}.png")
                saved_files.append(merged_path)

                imgs = []
                for f_item in files:
                    ext = os.path.splitext(f_item["filename"])[-1].lower()
                    if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                        im = Image.open(io.BytesIO(f_item["body"])).convert("RGB")
                        imgs.append(im)

                if imgs:
                    count = len(imgs)
                    targetW, targetH = (2480, 3508) if media == "A4" else (1748, 2480)
                    canvas = Image.new("RGB", (targetW, targetH), (255, 255, 255))
                    cols, rows = (1, 2)
                    if 3 <= count <= 4: cols, rows = 2, 2
                    elif 5 <= count <= 6: cols, rows = 2, 3
                    elif count > 6: cols, rows = 2, 4

                    cellW, cellH = targetW // cols, targetH // rows
                    pad = 30
                    for idx, im in enumerate(imgs[:cols*rows]):
                        c, r = idx % cols, idx // cols
                        max_w, max_h = cellW - pad * 2, cellH - pad * 2
                        im.thumbnail((max_w, max_h), Image.Resampling.BICUBIC)
                        pos_x = c * cellW + (cellW - im.width) // 2
                        pos_y = r * cellH + (cellH - im.height) // 2
                        canvas.paste(im, (pos_x, pos_y))

                    canvas.save(merged_path, format="PNG", dpi=(300, 300))
                    ok, jid = self.execute_lp(printer, copies, merged_path, extra_opts=extra_opts, timeout=20, auto_unlink=True)
                    if ok:
                        self.write_json(True, f"多图拼版已下发打印！", job=jid)
                    else:
                        self.write_json(False, f"CUPS拒绝: {jid}")
                    return

            # 模式 B: 独立多图流式出纸 (无损 PNG 提交)
            for f_item in files:
                fname = f_item["filename"]
                ext = os.path.splitext(fname)[-1].lower()
                token = uuid.uuid4().hex[:6]
                safe_fname = re.sub(r'[^a-zA-Z0-9_\.-]', '_', fname)
                tmp_src = os.path.join(UPLOAD_DIR, f"task_{token}_{safe_fname}")
                
                with open(tmp_src, "wb") as f_out:
                    f_out.write(f_item["body"])
                saved_files.append(tmp_src)

                target_to_print = tmp_src

                if need_enhance and ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                    tmp_enhanced = os.path.join(UPLOAD_DIR, f"task_{token}_enh_{safe_fname}.png")
                    saved_files.append(tmp_enhanced)
                    t0 = time.time()
                    if process_image_fast_enhance(tmp_src, tmp_enhanced, color_mode=color_mode, density=density):
                        target_to_print = tmp_enhanced
                        print(f"[FastEnhance] 出纸投影切边与文字保真完成，耗时: {round((time.time()-t0)*1000, 1)}ms", flush=True)

                ok, jid = self.execute_lp(printer, copies, target_to_print, extra_opts=extra_opts, timeout=20, auto_unlink=False)
                if ok:
                    job_ids.append(jid)
                else:
                    self.write_json(False, f"打印机拒绝文件 {fname}: {jid}")
                    return

            self.write_json(True, f"文件已成功发送至打印机！", jobs=job_ids)
        except Exception as e:
            self.write_json(False, f"出纸调度异常: {str(e)}")
        finally:
            for p in saved_files:
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            clean_old_tmp_files(UPLOAD_DIR)
