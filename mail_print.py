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
from PIL import Image, ImageOps, ImageFilter

HAVE_OPENCV = False
try:
    import cv2
    import numpy as np
    HAVE_OPENCV = True
except ImportError:
    HAVE_OPENCV = False

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.qq.com")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")
NOTIFY_URL = os.getenv("NOTIFY_URL", "https://www.pushplus.plus/send")
DEFAULT_PRINTER_ENV = os.getenv("DEFAULT_PRINTER", "")

TEMP_DIR = "/tmp/mail_print_tasks"
SCAN_DIR = os.getenv("SCAN_DIR", "/scans")
for d in (TEMP_DIR, SCAN_DIR):
    os.makedirs(d, exist_ok=True)

def get_clean_env():
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env

# ==================== 0. 消息通知模块 (PushPlus 等) ====================
def send_notify(title, content):
    """打印完成/异常自动推送通知"""
    if not PUSHPLUS_TOKEN:
        return
    try:
        data = {
            "token": PUSHPLUS_TOKEN,
            "title": title,
            "content": content,
            "template": "html"
        }
        requests.post(NOTIFY_URL, json=data, timeout=8)
    except Exception as e:
        print(f" [Notify Error] {e}", flush=True)

# ==================== 1. 扫描级图像增强核心 ====================

def auto_scan_and_whiten_pillow(image_path):
    """海纳思小内存专属降级方案 (纯 Pillow 极速去灰底与对比度拉伸)"""
    try:
        with Image.open(image_path) as im:
            im = ImageOps.exif_transpose(im).convert("L")
            # 适度切除四周 2% 暗边
            w, h = im.size
            im = im.crop((int(w * 0.02), int(h * 0.02), int(w * 0.98), int(h * 0.98)))
            # 自动对比度增强与灰底漂白
            im = ImageOps.autocontrast(im, cutoff=2)
            # 阶调映射白底
            table = [0 if i < 60 else (255 if i > 210 else int((i - 60) * 1.7)) for i in range(256)]
            out = im.point(table, "L").filter(ImageFilter.SHARPEN)
            out.save(image_path, "JPEG", quality=95)
        return True
    except Exception as e:
        print(f" [PIL Whiten Error] {e}", flush=True)
        return False

def auto_scan_and_whiten_cv(image_path):
    """N1 / x86 大内存旗舰方案 (OpenCV 霍夫变换倾角拉平 + 71x71 背景除法消灭黑底)"""
    try:
        with Image.open(image_path) as pil_raw:
            orig = cv2.cvtColor(np.array(ImageOps.exif_transpose(pil_raw).convert("RGB")), cv2.COLOR_RGB2BGR)

        h, w = orig.shape[:2]

        # 1. 霍夫变换文字基线倾角检测与快速自动拉平 (Deskew)
        scale = 600.0 / max(h, w)
        sw, sh = int(w * scale), int(h * scale)
        small = cv2.resize(orig, (sw, sh), interpolation=cv2.INTER_AREA)
        gray_s = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        thresh_s = cv2.adaptiveThreshold(gray_s, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 21, 10)
        dilated = cv2.dilate(thresh_s, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3)), iterations=1)
        
        lines = cv2.HoughLinesP(dilated, 1, np.pi / 180, threshold=80, minLineLength=int(sw * 0.25), maxLineGap=15)
        angle = 0.0
        if lines is not None:
            angles = [np.degrees(np.arctan2(y2 - y1, x2 - x1)) for line in lines for x1, y1, x2, y2 in line if -15 < np.degrees(np.arctan2(y2 - y1, x2 - x1)) < 15]
            if angles:
                angle = float(np.median(angles))

        warped = orig
        if abs(angle) > 0.3:
            M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
            warped = cv2.warpAffine(orig, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))

        # 2. 内切 2% 边框消除边缘暗影与扫描器黑边
        wh, ww = warped.shape[:2]
        my, mx = int(wh * 0.02), int(ww * 0.02)
        warped = warped[my:wh-my, mx:ww-mx]

        # 3. 提取彩色细线掩模（保留彩色题号与批改笔迹）
        b, g, r = cv2.split(warped)
        color_mask = ((cv2.absdiff(r, g) // 2 + cv2.absdiff(r, b) // 2 + cv2.absdiff(g, b) // 2) > 12)

        # 4. 71x71 大核背景除法归一化（消灭环境阴影与背面透光算式）
        gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        bg = cv2.GaussianBlur(gray, (71, 71), 0)
        norm = cv2.divide(gray, bg, scale=255.0)

        # 5. 快速矢量阶调映射：背景彻底纯白，正文字迹坚实纯黑
        res = np.empty_like(norm)
        res[norm >= 205] = 255
        res[norm <= 155] = 0
        mid = (norm > 155) & (norm < 205)
        res[mid] = ((norm[mid] - 155) * 5.1).astype(np.uint8)

        # 彩色线条强力压黑
        res[color_mask & (norm < 230)] = 0

        # 6. 单次拉普拉斯文本专用高反差边缘锐化
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        out = cv2.filter2D(res, -1, kernel)
        out[res == 255] = 255
        out[out < 50] = 0

        cv2.imwrite(image_path, out, [cv2.IMWRITE_JPEG_QUALITY, 98])
        return True
    except Exception as e:
        print(f" [CV Error] {e}", flush=True)
        return False

def auto_process_image(path):
    """自适应调用算法库"""
    if HAVE_OPENCV:
        return auto_scan_and_whiten_cv(path)
    return auto_scan_and_whiten_pillow(path)

def images_to_single_pdf(image_paths, output_pdf_path):
    try:
        pil_images = []
        for img_p in image_paths:
            auto_process_image(img_p)
            with Image.open(img_p) as im:
                pil_images.append(im.convert("RGB"))
        if not pil_images:
            return False
        pil_images[0].save(output_pdf_path, save_all=True, append_images=pil_images[1:], resolution=300.0)
        return True
    except Exception:
        return False

def convert_office_to_pdf(doc_path):
    return None

# ==================== 2. 邮件接收与出纸监控 ====================

def decode_mime(s):
    if not s:
        return ""
    res = []
    for frag, charset in decode_header(s):
        if isinstance(frag, bytes):
            try:
                res.append(frag.decode(charset or "utf-8", errors="ignore"))
            except Exception:
                res.append(frag.decode("utf-8", errors="ignore"))
        else:
            res.append(str(frag))
    return "".join(res)

def get_target_printer():
    all_p, def_p = [], None
    env = get_clean_env()
    try:
        dp = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, env=env, timeout=3)
        m_def = re.search(r"destination:\s*(\S+)", dp.stdout, re.IGNORECASE)
        if m_def:
            def_p = m_def.group(1).strip()
        ps = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, env=env, timeout=3)
        for l in ps.stdout.splitlines():
            m_p = re.match(r"^printer\s+([^\s:]+)", l.strip(), re.IGNORECASE)
            if m_p:
                all_p.append(m_p.group(1).strip())
    except Exception:
        pass
    return DEFAULT_PRINTER_ENV if DEFAULT_PRINTER_ENV in all_p else (def_p or (all_p[0] if all_p else None))

def print_file(filepath, filename):
    p = get_target_printer()
    if not p:
        send_notify("🖨️ 打印失败通知", f"任务【{filename}】打印失败：系统未检测到任何可用打印机！")
        return False
    try:
        cmd = [
            "lp", "-d", p,
            "-o", "media=A4", "-o", "fit-to-page",
            "-o", "Resolution=600dpi", "-o", "pdftops-renderer=gs",
            "-o", "ColorModel=Gray", filepath
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, env=get_clean_env(), timeout=35)
        if res.returncode == 0:
            send_notify("🖨️ 打印完成提醒", f"邮件任务已成功送达打印机！<br><b>文件名:</b> {filename}<br><b>目标设备:</b> {p}<br><b>完成时间:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}")
            return True
        else:
            err_msg = res.stderr.strip() or res.stdout.strip()
            send_notify("⚠️ 打印出纸异常", f"任务【{filename}】出纸失败！<br>报错信息: {err_msg}")
            return False
    except Exception as e:
        send_notify("⚠️ 打印任务异常", f"任务【{filename}】执行异常: {str(e)}")
        return False
    finally:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
        gc.collect()

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
            subject = decode_mime(msg.get("Subject", "无主题邮件"))
            imgs, docs = [], []
            for part in msg.walk():
                if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                    continue
                fn = decode_mime(part.get_filename() or "")
                if not fn:
                    continue
                ext = os.path.splitext(fn)[1].lower()
                fp = os.path.join(TEMP_DIR, fn)
                with open(fp, "wb") as f:
                    f.write(part.get_payload(decode=True))
                if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
                    imgs.append(fp)
                elif ext in [".pdf", ".txt"]:
                    docs.append((fp, fn))
            
            # 处理图片（单张去阴影拉平打印，多张自动合并排版为合卷 PDF）
            if imgs:
                if len(imgs) == 1:
                    auto_process_image(imgs[0])
                    print_file(imgs[0], os.path.basename(imgs[0]))
                else:
                    comb = os.path.join(TEMP_DIR, f"合卷_{int(time.time())}.pdf")
                    if images_to_single_pdf(imgs, comb):
                        print_file(comb, f"[{subject}] 合卷共{len(imgs)}页.pdf")
                    else:
                        for ifp in imgs:
                            auto_process_image(ifp)
                            print_file(ifp, os.path.basename(ifp))
            
            # 处理 PDF / 文档直通
            for dfp, dfn in docs:
                print_file(dfp, dfn)
                
            mail.store(num, "+FLAGS", "\\Seen")
        mail.close()
        mail.logout()
    except Exception as e:
        print(f" [Fetch Mail Error] {e}", flush=True)

def main():
    while True:
        try:
            fetch_and_print()
        except Exception:
            pass
        time.sleep(8)

if __name__ == "__main__":
    main()
