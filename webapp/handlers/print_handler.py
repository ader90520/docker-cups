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

def process_image_fast_enhance(in_path, out_path, color_mode="monochrome"):
    """
    高保真出纸增强算法：
    既保证运算速度控制在 1 秒以内，又保证输出给物理打印机的文字饱满厚重、笔划不发虚
    """
    if not HAVE_OPENCV:
        return False

    try:
        img = cv2.imread(in_path, cv2.IMREAD_COLOR)
        if img is None:
            return False

        h, w = img.shape[:2]
        max_dim = max(h, w)
        scale = 1.0
        # 打印分辨率保护：出纸保留 1800px 级精度，兼顾 ARM 性能与 A4 纸文字清晰度
        if max_dim > 1800:
            scale = 1800.0 / max_dim
            calc_img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        else:
            calc_img = img

        ch, cw = calc_img.shape[:2]

        # 1. 微缩提取光照底色 (320px 尺度)，极速运算
        tiny_scale = 320.0 / max(ch, cw)
        tiny_img = cv2.resize(calc_img, (int(cw * tiny_scale), int(ch * tiny_scale)), interpolation=cv2.INTER_NEAREST)
        bg_tiny = cv2.GaussianBlur(tiny_img, (0, 0), sigmaX=8, sigmaY=8)
        bg = cv2.resize(bg_tiny, (cw, ch), interpolation=cv2.INTER_LINEAR)

        # 2. 照度除法归一
        norm = cv2.divide(calc_img, bg, scale=255)

        is_color = (str(color_mode).strip().lower() == "color")

        if not is_color:
            # 灰阶抗锯齿白底拉伸（不丢边缘）
            min_gray = np.min(norm, axis=2).astype(np.float32)
            v_stretched = np.clip((min_gray - 40.0) / (200.0 - 40.0) * 255.0, 0, 255).astype(np.uint8)
            v_stretched[v_stretched >= 235] = 255

            # USM 锐化文字骨架
            blurred = cv2.GaussianBlur(v_stretched, (0, 0), 1)
            result = cv2.addWeighted(v_stretched, 2.2, blurred, -1.2, 0)
        else:
            hsv = cv2.cvtColor(norm, cv2.COLOR_BGR2HSV).astype(np.float32)
            s_c = hsv[:, :, 1]
            v_c = hsv[:, :, 2]
            color_mask = (s_c > 18) & (v_c > 38)
            s_c = np.where(color_mask, np.clip(s_c * 1.3, 0, 255), s_c)
            v_c = np.where(color_mask, v_c, np.clip((v_c - 30.0) / (220.0 - 30.0) * 255.0, 0, 255))
            hsv[:, :, 1] = s_c
            hsv[:, :, 2] = v_c
            result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
            gray_chk = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
            result[gray_chk >= 235] = [255, 255, 255]

        # 还原回高解析原图尺寸输出
        if scale != 1.0:
            result = cv2.resize(result, (w, h), interpolation=cv2.INTER_LINEAR)

        cv2.imwrite(out_path, result, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return True
    except Exception as e:
        print(f"[FastEnhance] 出纸增强异常: {e}", flush=True)
        return False

def clean_old_tmp_files(upload_dir, max_age=60):
    """即时清理辅助"""
    try:
        now = time.time()
        for f in os.listdir(upload_dir):
            p = os.path.join(upload_dir, f)
            if os.path.isfile(p) and (now - os.path.getmtime(p) > max_age):
                try: os.remove(p)
                except Exception: pass
    except Exception:
        pass

class PrintHandler(BaseHandler):
    def post(self):
        saved_files = []
        try:
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()
            duplex = self.get_argument("duplex", "none").strip()
            color_mode = self.get_argument("color_mode", "monochrome").strip().lower()
            media = self.get_argument("media", "A4").strip().upper()
            enhance_val = self.get_argument("enhance", "false").strip().lower()
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
                merged_path = os.path.join(UPLOAD_DIR, f"merged_{token}.jpg")
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

                    canvas.save(merged_path, format="JPEG", quality=95, dpi=(300, 300))
                    ok, jid = self.execute_lp(printer, copies, merged_path, extra_opts=extra_opts, timeout=20, auto_unlink=True)
                    if ok:
                        self.write_json(True, f"多图拼版已下发打印！", job=jid)
                    else:
                        self.write_json(False, f"CUPS拒绝: {jid}")
                    return

            # 模式 B: 独立多图流式出纸 (PDF直接放行，图片根据开关极速增强)
            for f_item in files:
                fname = f_item["filename"]
                ext = os.path.splitext(fname)[-1].lower()
                token = uuid.uuid4().hex[:6]
                tmp_src = os.path.join(UPLOAD_DIR, f"task_{token}_{fname}")
                with open(tmp_src, "wb") as f_out:
                    f_out.write(f_item["body"])
                saved_files.append(tmp_src)

                target_to_print = tmp_src

                if need_enhance and ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                    tmp_enhanced = os.path.join(UPLOAD_DIR, f"task_{token}_enh_{fname}.jpg")
                    saved_files.append(tmp_enhanced)
                    t0 = time.time()
                    if process_image_fast_enhance(tmp_src, tmp_enhanced, color_mode=color_mode):
                        target_to_print = tmp_enhanced
                        print(f"[FastEnhance] 去黑底完成，耗时: {round((time.time()-t0)*1000, 1)}ms", flush=True)

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
            self.clean_old_files()
