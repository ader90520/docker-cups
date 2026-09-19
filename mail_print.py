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
import cv2
import numpy as np
from PIL import Image, ImageEnhance

# ==================== 1. 全局配置与环境变量 ====================
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


# ==================== 2. 图像透视拉平、裁黑边与白底锐化算法 ====================
def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def four_point_transform(image, pts):
    rect = order_points(pts)
    (tl, tr, br, bl) = rect

    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB), 100)

    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB), 100)

    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]
    ], dtype="float32")

    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (maxWidth, maxHeight))

def auto_scan_and_whiten(image_path):
    """
    智能图像处理逻辑：
    1. 自动寻找纸张轮廓并进行透视拉直（纠正拍摄倾斜）
    2. 物理切除四周桌面黑边与阴影
    3. 光照归一化除法：将灰暗底色漂成纯白，字迹加黑锐化
    """
    try:
        orig = cv2.imread(image_path)
        if orig is None:
            with Image.open(image_path) as pil_im:
                orig = cv2.cvtColor(np.array(pil_im.convert("RGB")), cv2.COLOR_RGB2BGR)

        h, w = orig.shape[:2]

        scale_ratio = 800.0 / max(h, w)
        small_w = int(w * scale_ratio)
        small_h = int(h * scale_ratio)
        small = cv2.resize(orig, (small_w, small_h), interpolation=cv2.INTER_AREA)

        gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray_small, (5, 5), 0)
        edged = cv2.Canny(blurred, 50, 180)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

        doc_contour = None
        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.contourArea(c) > (small_w * small_h * 0.20):
                doc_contour = approx
                break

        if doc_contour is not None:
            pts = doc_contour.reshape(4, 2) * (1.0 / scale_ratio)
            warped = four_point_transform(orig, pts)
            print(f" [Auto-Scan] 成功识别文档轮廓并完成透视摆正: {os.path.basename(image_path)}", flush=True)
        else:
            margin_y = int(h * 0.02)
            margin_x = int(w * 0.02)
            warped = orig[margin_y:h-margin_y, margin_x:w-margin_x]
            print(f" [Auto-Scan] 特写拍摄，执行安全边框裁切: {os.path.basename(image_path)}", flush=True)

        gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        bg = cv2.GaussianBlur(gray, (51, 51), 0)
        normalized = cv2.divide(gray, bg, scale=255)

        clean = np.where(normalized > 195, 255, normalized).astype(np.uint8)
        clean = cv2.normalize(clean, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX)

        cv2.imwrite(image_path, clean)
        print(f" [Auto-Scan] 背景纯白化与字迹锐化完成: {os.path.basename(image_path)}", flush=True)
        return True
    except Exception as e:
        print(f" [Auto-Scan Warning] 图像处理异常，跳过高级滤镜: {e}", flush=True)
        return False

def images_to_single_pdf(image_paths, output_pdf_path):
    try:
        pil_images = []
        for img_p in image_paths:
            auto_scan_and_whiten(img_p)
            with Image.open(img_p) as im:
                pil_images.append(im.convert("RGB"))

        if not pil_images:
            return False

        first_image = pil_images[0]
        other_images = pil_images[1:] if len(pil_images) > 1 else []
        first_image.save(output_pdf_path, save_all=True, append_images=other_images, resolution=300.0)
        print(f" [Multi-Images] 成功合并 {len(image_paths)} 张图片为 PDF: {os.path.basename(output_pdf_path)}", flush=True)
        return True
    except Exception as e:
        print(f" [Multi-Images Error] 合并异常: {e}", flush=True)
        return False

def convert_office_to_pdf(doc_path):
    """大内存环境专属：通过 LibreOffice 无头转换 Office 文件为标准 PDF"""
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
            print(f" [Office Success] 成功将 {os.path.basename(doc_path)} 转为 PDF", flush=True)
            return target_pdf
        else:
            print(f" [Office Warning] 转换输出未生成: {res.stderr}", flush=True)
    except Exception as e:
        print(f" [Office Error] LibreOffice 调用异常: {e}", flush=True)
    return None


# ==================== 3. 微信通知与头部解析 ====================
def send_pushplus_notice(title, content):
    if not PUSHPLUS_TOKEN:
        return
    try:
        headers = {"Content-Type": "application/json"}
        payload = {
            "token": PUSHPLUS_TOKEN.strip(),
            "title": title,
            "content": content,
            "template": "html"
        }
        requests.post(NOTIFY_URL, json=payload, headers=headers, timeout=8)
    except Exception as e:
        print(f" [PushPlus Error] 推送异常: {e}", flush=True)

def decode_mime_words(header_str):
    if not header_str:
        return ""
    fragments = decode_header(header_str)
    res = []
    for frag, charset in fragments:
        if isinstance(frag, bytes):
            try:
                res.append(frag.decode(charset or "utf-8", errors="ignore"))
            except Exception:
                res.append(frag.decode("utf-8", errors="ignore"))
        else:
            res.append(str(frag))
    return "".join(res)


# ==================== 4. 打印任务管理与物理出纸侦测 ====================
def get_active_printers():
    default_printer = None
    all_printers = []
    try:
        env = dict(os.environ, LC_ALL="C")
        res_d = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, text=True, env=env, timeout=5)
        if "destination: " in res_d.stdout:
            default_printer = res_d.stdout.split("destination: ")[-1].strip()

        res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, text=True, env=env, timeout=5)
        for line in res_p.stdout.splitlines():
            if line.startswith("printer "):
                all_printers.append(line.split()[1].strip())
    except Exception:
        pass

    if DEFAULT_PRINTER_ENV and DEFAULT_PRINTER_ENV in all_printers:
        target = DEFAULT_PRINTER_ENV
    elif default_printer:
        target = default_printer
    elif all_printers:
        target = all_printers[0]
    else:
        target = None

    return target, all_printers

def diagnose_printer_hardware(printer_name):
    try:
        env = dict(os.environ, LC_ALL="C")
        res = subprocess.run(["lpstat", "-p", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
        out = res.stdout.lower()

        if any(w in out for w in ["out of paper", "media-empty", "paper empty", "input tray empty"]):
            return "打印机【缺纸】，请在纸槽加入 A4 纸！"
        elif any(w in out for w in ["jam", "paper-jam"]):
            return "打印机【卡纸】，请清除卡纸！"
        elif any(w in out for w in ["offline", "not connected", "unable to locate"]):
            return "打印机【脱机】，请检查 USB 数据线与电源！"
        elif any(w in out for w in ["door open", "cover open"]):
            return "打印机【机盖未闭合】！"
        elif "paused" in out or "disabled" in out:
            return "打印机已被系统停用或暂停。"
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
        send_pushplus_notice("❌ 打印失败", "未检测到打印机，请先在 631 后台添加。")
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
            send_pushplus_notice("❌ 打印拒绝", f"任务被拒绝：{res.stderr.strip()}")
            return False

        match = re.search(r"request id is ([^\s]+)", res.stdout)
        job_id = match.group(1) if match else res.stdout.strip().split()[0]

        is_printed, reason = wait_for_job_real_print(printer_name, job_id, timeout=90)
        cost_time = round(time.time() - t_start, 1)

        if is_printed:
            send_pushplus_notice(
                "🖨️ 试卷/文档已出纸",
                f"打印机：<b>{printer_name}</b><br>文件名：<b>{filename}</b><br>耗时：<b>{cost_time} 秒</b>"
            )
            return True
        else:
            send_pushplus_notice(
                "⚠️ 未出纸报警",
                f"文件名：<b>{filename}</b><br>原因：<b>{reason}</b>"
            )
            return False
    except Exception as e:
        print(f" [Print System Error] {e}", flush=True)
        return False
    finally:
        if os.path.exists(filepath):
            try: os.remove(filepath)
            except Exception: pass
        gc.collect()


# ==================== 5. 邮件循环监听 ====================
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
            if status != "OK":
                continue

            msg = email.message_from_bytes(data[0][1])
            subject = decode_mime_words(msg.get("Subject", "无主题"))
            print(f" [New Mail] 收到新邮件: [{subject}]", flush=True)

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
                elif ext in [".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]:
                    doc_tasks.append((filepath, filename, ext))

            # 图片任务自动拉直摆正、切除黑边、漂白并合并打印
            if image_tasks:
                if len(image_tasks) == 1:
                    auto_scan_and_whiten(image_tasks[0])
                    print_file(image_tasks[0], os.path.basename(image_tasks[0]))
                else:
                    comb_pdf = os.path.join(TEMP_DIR, f"合并试卷_{int(time.time())}.pdf")
                    if images_to_single_pdf(image_tasks, comb_pdf):
                        print_file(comb_pdf, os.path.basename(comb_pdf))
                    for p in image_tasks:
                        if os.path.exists(p):
                            try: os.remove(p)
                            except Exception: pass

            # 文档任务：PDF 直接打，Office 自动调用 LibreOffice 转 PDF 打
            for fpath, fname, ext in doc_tasks:
                if ext in [".pdf", ".txt"]:
                    print_file(fpath, fname)
                elif ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]:
                    conv_pdf = convert_office_to_pdf(fpath)
                    if conv_pdf:
                        print_file(conv_pdf, fname)
                    else:
                        send_pushplus_notice("❌ 打印失败", f"文档 <b>{fname}</b> 转换排版失败。")

            mail.store(num, "+FLAGS", "\\Seen")

        mail.close()
        mail.logout()
    except Exception as e:
        print(f" [Mail Error] {e}", flush=True)

def main():
    print("==================================================", flush=True)
    print(" 🚀 [Cloud Print Full] 全功能大内存增强版守护已启动", flush=True)
    print(" 具备特性: Office自动转PDF | 拍照四角透视纠偏 | 切除黑边 | 漂白锐化", flush=True)
    print("==================================================", flush=True)
    while True:
        try:
            fetch_and_print()
        except Exception:
            pass
        time.sleep(8)

if __name__ == "__main__":
    main()
