#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import gc
import re
import email
import imaplib
import subprocess
import requests
from email.header import decode_header
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# 动态探测 OpenCV 环境（Full 镜像使用 OpenCV 高阶纠偏，Slim 镜像自动回退 Pillow 算法）
HAVE_OPENCV = False
try:
    import cv2
    import numpy as np
    HAVE_OPENCV = True
except ImportError:
    HAVE_OPENCV = False

# ==================== 1. 配置与路径初始化 ====================
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.qq.com")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")
NOTIFY_URL = os.getenv("NOTIFY_URL", "https://www.pushplus.plus/send")
DEFAULT_PRINTER_ENV = os.getenv("DEFAULT_PRINTER", "")

TEMP_DIR = "/tmp/mail_print_tasks"
SCAN_DIR = os.getenv("SCAN_DIR", "/scans")
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)


# ==================== 2. 高阶图像纠偏与白底去黑边算法 ====================
def order_points_cv(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def four_point_transform_cv(image, pts):
    rect = order_points_cv(pts)
    (tl, tr, br, bl) = rect
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB), 200)

    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB), 200)

    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]
    ], dtype="float32")

    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (maxWidth, maxHeight))

def auto_scan_and_whiten_cv(image_path):
    """
    OpenCV 高阶文档纠偏与漂白：
    1. 修正手机 EXIF 旋转角（解决竖拍横读导致的识别失败）
    2. 多重闭合寻找纸张四边凸多边形进行透视摆正
    3. 大核高斯背景除法彻底消除阴影
    4. 激进 LUT 查找表推白灰底
    """
    try:
        # 1. 修正 EXIF 旋转
        with Image.open(image_path) as pil_raw:
            pil_corrected = ImageOps.exif_transpose(pil_raw).convert("RGB")
            orig = cv2.cvtColor(np.array(pil_corrected), cv2.COLOR_RGB2BGR)

        h, w = orig.shape[:2]
        scale_ratio = 800.0 / max(h, w)
        small_w = int(w * scale_ratio)
        small_h = int(h * scale_ratio)
        small = cv2.resize(orig, (small_w, small_h), interpolation=cv2.INTER_AREA)

        gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray_small, (5, 5), 0)

        # 2. 边缘检测与轮廓闭合
        edged = cv2.Canny(blurred, 30, 150)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]

        doc_contour = None
        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.contourArea(c) > (small_w * small_h * 0.15):
                doc_contour = approx
                break

        if doc_contour is not None:
            pts = doc_contour.reshape(4, 2) * (1.0 / scale_ratio)
            warped = four_point_transform_cv(orig, pts)
            print(f" [Auto-Scan] 成功识别轮廓并完成四角透视纠偏: {os.path.basename(image_path)}", flush=True)
        else:
            my, mx = int(h * 0.03), int(w * 0.03)
            warped = orig[my:h-my, mx:w-mx]
            print(f" [Auto-Scan] 未找到明显四边，执行安全边缘裁切: {os.path.basename(image_path)}", flush=True)

        # 3. 强力背景除法去阴影
        gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        bg = cv2.GaussianBlur(gray, (55, 55), 0)
        normalized = cv2.divide(gray, bg, scale=255)

        # 4. 激进纯白化：发灰区域 (>=160) 强制拉成纯白 255，字迹 (<=70) 强力拉黑
        lut = np.zeros(256, dtype=np.uint8)
        for i in range(256):
            if i >= 160:
                lut[i] = 255
            elif i <= 70:
                lut[i] = 0
            else:
                lut[i] = int(((i - 70) / (160 - 70)) * 255)

        clean = cv2.LUT(normalized, lut)
        cv2.imwrite(image_path, clean)
        print(f" [Auto-Scan] 白底纯净化与字迹锐化完成: {os.path.basename(image_path)}", flush=True)
        return True
    except Exception as e:
        print(f" [OpenCV Warning] 处理异常，回退 Pillow: {e}", flush=True)
        return auto_scan_and_whiten_pillow(image_path)

def auto_scan_and_whiten_pillow(image_path):
    """Pillow 纯轻量算法（专供 1GB 海思盒子，兼顾 EXIF 旋转与白底纯化）"""
    try:
        with Image.open(image_path) as raw_img:
            img = ImageOps.exif_transpose(raw_img).convert("RGB")
            w, h = img.size

            # 裁剪 2.5% 外缘暗区
            crop_box = (int(w * 0.025), int(h * 0.025), int(w * 0.975), int(h * 0.975))
            cropped = img.crop(crop_box)

            gray = cropped.convert("L").filter(ImageFilter.SHARPEN)
            enh = ImageEnhance.Contrast(gray)
            high_contrast = enh.enhance(1.8)

            # 查找表纯白化
            lut = []
            for i in range(256):
                if i > 160:
                    lut.append(255)
                elif i < 70:
                    lut.append(0)
                else:
                    lut.append(int(((i - 70) / 90.0) * 255))

            clean = high_contrast.point(lut, mode="L")
            clean.save(image_path, "JPEG", quality=92)
            print(f" [Auto-Scan Pillow] 轻量白底锐化完成: {os.path.basename(image_path)}", flush=True)
            return True
    except Exception as e:
        print(f" [Pillow Warning] 处理跳过: {e}", flush=True)
        return False

def auto_process_image(image_path):
    if HAVE_OPENCV:
        return auto_scan_and_whiten_cv(image_path)
    return auto_scan_and_whiten_pillow(image_path)

def images_to_single_pdf(image_paths, output_pdf_path):
    try:
        pil_images = []
        for img_p in image_paths:
            auto_process_image(img_p)
            with Image.open(img_p) as im:
                pil_images.append(im.convert("RGB"))

        if not pil_images:
            return False

        first = pil_images[0]
        others = pil_images[1:] if len(pil_images) > 1 else []
        first.save(output_pdf_path, save_all=True, append_images=others, resolution=300.0)
        return True
    except Exception as e:
        print(f" [PDF Merge Error] {e}", flush=True)
        return False

def convert_office_to_pdf(doc_path):
    if not os.path.exists("/usr/bin/libreoffice"):
        return None
    try:
        out_dir = os.path.dirname(doc_path)
        base_name = os.path.splitext(os.path.basename(doc_path))[0]
        target_pdf = os.path.join(out_dir, f"{base_name}.pdf")

        cmd = [
            "libreoffice",
            "--headless", "--invisible", "--nodefault",
            "--nofirststartwizard", "--nolockcheck", "--nologo", "--norestore",
            "--convert-to", "pdf",
            "--outdir", out_dir,
            doc_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=50)
        if os.path.exists(target_pdf):
            return target_pdf
    except Exception as e:
        print(f" [Office Convert Error] {e}", flush=True)
    return None


# ==================== 3. 硬件侦测与消息通知 ====================
def send_pushplus_notice(title, content):
    if not PUSHPLUS_TOKEN:
        return
    try:
        payload = {"token": PUSHPLUS_TOKEN.strip(), "title": title, "content": content, "template": "html"}
        requests.post(NOTIFY_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=8)
    except Exception:
        pass

def decode_mime_words(header_str):
    if not header_str: return ""
    fragments = decode_header(header_str)
    res = []
    for frag, charset in fragments:
        if isinstance(frag, bytes):
            try: res.append(frag.decode(charset or "utf-8", errors="ignore"))
            except Exception: res.append(frag.decode("utf-8", errors="ignore"))
        else: res.append(str(frag))
    return "".join(res)

def get_active_printers():
    default_p = None
    all_p = []
    try:
        env = dict(os.environ, LC_ALL="C")
        res_d = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, text=True, env=env, timeout=5)
        if "destination: " in res_d.stdout:
            default_p = res_d.stdout.split("destination: ")[-1].strip()
        res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, text=True, env=env, timeout=5)
        for line in res_p.stdout.splitlines():
            if line.startswith("printer "):
                all_p.append(line.split()[1].strip())
    except Exception:
        pass
    target = DEFAULT_PRINTER_ENV if DEFAULT_PRINTER_ENV in all_p else (default_p or (all_p[0] if all_p else None))
    return target, all_p

def diagnose_printer_hardware(printer_name):
    try:
        env = dict(os.environ, LC_ALL="C")
        res = subprocess.run(["lpstat", "-p", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
        out = res.stdout.lower()
        if any(w in out for w in ["out of paper", "media-empty", "paper empty", "input tray empty"]):
            return "打印机【缺纸】，请添加 A4 纸！"
        elif any(w in out for w in ["jam", "paper-jam"]):
            return "打印机【卡纸】，请清理纸槽！"
        elif any(w in out for w in ["offline", "not connected", "unable to locate"]):
            return "打印机【脱机】，请检查 USB 连线与电源！"
        elif any(w in out for w in ["door open", "cover open"]):
            return "打印机【机盖未闭合】！"
        elif "paused" in out or "disabled" in out:
            return "打印机已暂停/停用。"
    except Exception:
        pass
    return "硬件未响应"

def wait_for_job_real_print(printer_name, job_id, timeout=90):
    start_time = time.time()
    env = dict(os.environ, LC_ALL="C")
    num_match = re.search(r"\d+$", job_id)
    raw_num = num_match.group() if num_match else job_id

    while time.time() - start_time < timeout:
        time.sleep(2.5)
        p_check = subprocess.run(["lpstat", "-p", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
        if any(err in p_check.stdout.lower() for err in ["disabled", "media-empty", "paper-jam", "out of paper"]):
            err_reason = diagnose_printer_hardware(printer_name)
            subprocess.run(["cancel", job_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return False, err_reason

        res_active = subprocess.run(["lpstat", "-o", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
        if job_id not in res_active.stdout and raw_num not in res_active.stdout:
            time.sleep(1)
            return True, "物理出纸完成"

    subprocess.run(["cancel", job_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return False, f"打印超时（{diagnose_printer_hardware(printer_name)}）"

def print_file(filepath, filename):
    printer_name, _ = get_active_printers()
    if not printer_name:
        send_pushplus_notice("❌ 打印失败", "未检测到可用打印机，请在 631 后台添加设备。")
        return False

    try:
        cmd = [
            "lp", "-d", printer_name,
            "-o", "media=A4", "-o", "fit-to-page",
            "-o", "Resolution=300dpi", "-o", "pdftops-renderer=pdftocairo",
            "-o", "ColorModel=Gray", "-o", "job-sheets=none",
            filepath
        ]
        t_start = time.time()
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if res.returncode != 0:
            send_pushplus_notice("❌ 任务提交拒绝", f"{res.stderr.strip()}")
            return False

        match = re.search(r"request id is ([^\s]+)", res.stdout)
        job_id = match.group(1) if match else res.stdout.strip().split()[0]

        is_printed, reason = wait_for_job_real_print(printer_name, job_id, timeout=90)
        cost = round(time.time() - t_start, 1)

        if is_printed:
            send_pushplus_notice("🖨️ 试卷/文档已出纸", f"打印机：<b>{printer_name}</b><br>文件名：<b>{filename}</b><br>耗时：<b>{cost} 秒</b>")
            return True
        else:
            send_pushplus_notice("⚠️ 未出纸报警", f"文件名：<b>{filename}</b><br>原因：<b>{reason}</b>")
            return False
    except Exception as e:
        print(f" [Print System Error] {e}", flush=True)
        return False
    finally:
        if os.path.exists(filepath):
            try: os.remove(filepath)
            except Exception: pass
        gc.collect()


# ==================== 4. 邮件守护主循环 ====================
def fetch_and_print():
    if not EMAIL_USER or not EMAIL_PASS:
        return
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993, timeout=12)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK" or not messages[0]:
            mail.logout()
            return

        for num in messages[0].split():
            status, data = mail.fetch(num, "(RFC822)")
            if status != "OK": continue
            msg = email.message_from_bytes(data[0][1])
            subject = decode_mime_words(msg.get("Subject", "无主题"))
            print(f" [New Mail] 收到新邮件: {subject}", flush=True)

            image_tasks = []
            doc_tasks = []

            for part in msg.walk():
                if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                    continue
                filename = decode_mime_words(part.get_filename() or "")
                ext = os.path.splitext(filename)[1].lower()
                filepath = os.path.join(TEMP_DIR, filename)

                with open(filepath, "wb") as f:
                    f.write(part.get_payload(decode=True))

                if ext in [".jpg", ".jpeg", ".png", ".bmp", ".heic"]:
                    image_tasks.append(filepath)
                elif ext in [".pdf", ".txt"]:
                    doc_tasks.append((filepath, filename))
                elif ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]:
                    if os.path.exists("/usr/bin/libreoffice"):
                        conv_pdf = convert_office_to_pdf(filepath)
                        if conv_pdf:
                            doc_tasks.append((conv_pdf, filename))
                        else:
                            send_pushplus_notice("❌ 转换失败", f"文档 <b>{filename}</b> 转换排版失败。")
                    else:
                        send_pushplus_notice("ℹ️ 格式提醒", f"收到 <b>{filename}</b>。<br>当前设备运行轻量极速版，请将文档另存为 <b>PDF</b> 或直接发送照片即可自动打印！")
                        if os.path.exists(filepath): os.remove(filepath)

            if image_tasks:
                if len(image_tasks) == 1:
                    auto_process_image(image_tasks[0])
                    print_file(image_tasks[0], os.path.basename(image_tasks[0]))
                else:
                    comb = os.path.join(TEMP_DIR, f"合并试卷_{int(time.time())}.pdf")
                    if images_to_single_pdf(image_tasks, comb):
                        print_file(comb, os.path.basename(comb))
                    for p in image_tasks:
                        if os.path.exists(p):
                            try: os.remove(p)
                            except Exception: pass

            for fpath, fname in doc_tasks:
                print_file(fpath, fname)

            mail.store(num, "+FLAGS", "\\Seen")

        mail.close()
        mail.logout()
    except Exception as e:
        print(f" [Loop Error] {e}", flush=True)

def main():
    mode = "Full 全功能版 (OpenCV + Office)" if HAVE_OPENCV else "Slim 轻量极速版 (Pillow 算法)"
    print("==================================================", flush=True)
    print(f" 🚀 [Cloud Print Daemon] 邮件打印监控就绪: [{mode}]", flush=True)
    print(f" 监听邮箱: {EMAIL_USER}", flush=True)
    print("==================================================", flush=True)
    while True:
        try: fetch_and_print()
        except Exception: pass
        time.sleep(8)

if __name__ == "__main__":
    main()
