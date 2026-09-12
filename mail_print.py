#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import email
import imaplib
import subprocess
import requests
import urllib.parse
from email.header import decode_header
from email.utils import collapse_rfc2231_value
from PIL import Image

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.qq.com")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
NOTIFY_URL = os.getenv("NOTIFY_URL", "http://www.pushplus.plus/send")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")

TRIGGER_KEYWORDS = [
    "打", "print", "作业", "试卷", "练习", "复习", "打卡",
    "语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "科学",
    "一年级", "二年级", "三年级", "四年级", "五年级", "六年级",
    "初一", "初二", "初三", "七年级", "八年级", "九年级", "高一", "高二", "高三"
]

TEMP_DIR = "/tmp/mail_print_tasks"
os.makedirs(TEMP_DIR, exist_ok=True)

def get_hardware_profile():
    if os.path.exists("/tmp/cups_profile"):
        try:
            with open("/tmp/cups_profile", "r") as f:
                return f.read().strip()
        except Exception:
            pass
    return "HIGH_PERF"

def send_pushplus_notice(title, content):
    if not PUSHPLUS_TOKEN:
        return
    try:
        data = {"token": PUSHPLUS_TOKEN, "title": title, "content": content, "template": "html"}
        requests.post(NOTIFY_URL, json=data, timeout=10)
    except Exception as e:
        print(f" [PushPlus Error] 推送失败: {e}")

def decode_str(header_text):
    if not header_text:
        return ""
    try:
        decoded_fragments = decode_header(header_text)
        text_result = []
        for fragment, charset in decoded_fragments:
            if isinstance(fragment, bytes):
                text_result.append(fragment.decode(charset or "utf-8", errors="ignore"))
            else:
                text_result.append(str(fragment))
        return "".join(text_result)
    except Exception:
        return str(header_text)

def clean_filename(filename):
    """纯净健壮的文件名清洗器：杜绝 empty separator 报错"""
    if not filename:
        return f"doc_{int(time.time())}.pdf"
    try:
        if isinstance(filename, tuple):
            filename = collapse_rfc2231_value(filename)
        filename = decode_str(str(filename))
        if "%" in filename:
            filename = urllib.parse.unquote(filename)
        clean_name = filename.strip().replace("/", "_").replace("\\", "_")
        return clean_name if clean_name else f"doc_{int(time.time())}.pdf"
    except Exception as e:
        print(f" [Filename Parse Warning] 解析警告: {e}")
        return f"doc_{int(time.time())}.pdf"

def optimize_image_for_print(filepath):
    try:
        ext = os.path.splitext(filepath)[1].lower()
        if ext in [".jpg", ".jpeg", ".png"]:
            profile = get_hardware_profile()
            max_limit = 1800 if profile == "LOW_MEM" else 4000
            with Image.open(filepath) as img:
                w, h = img.size
                max_edge = max(w, h)
                if max_edge > max_limit:
                    scale = max_limit / float(max_edge)
                    new_size = (int(w * scale), int(h * scale))
                    resized_img = img.resize(new_size, Image.Resampling.LANCZOS)
                    if resized_img.mode != "RGB":
                        resized_img = resized_img.convert("RGB")
                    resized_img.save(filepath, "JPEG", quality=85)
                    print(f" [Optimizer] 图片自适应降维 ({profile}): {w}x{h} -> {new_size[0]}x{new_size[1]}")
    except Exception as e:
        print(f" [Optimizer Warning] 图片优化跳过: {e}")

def print_file(filepath, filename):
    try:
        optimize_image_for_print(filepath)
        cmd = ["lp", "-c", "-d", "HP_LaserJet_Pro_MFP_M126a", "-o", "fit-to-page", filepath]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            print(f" [Print Success] 任务提交成功: {filename} ({res.stdout.strip()})")
            send_pushplus_notice("🖨️ 打印机出纸提醒", f"已成功提交打印文件：<br><b>{filename}</b><br>正在出纸，请在打印机旁等待。")
            return True
        else:
            print(f" [Print Error] 打印指令执行失败: {res.stderr.strip()}")
            send_pushplus_notice("❌ 打印失败提醒", f"打印 <b>{filename}</b> 失败：<br>{res.stderr.strip()}")
            return False
    except Exception as e:
        print(f" [System Error] 提交异常: {e}")
        return False
    finally:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass

def process_email():
    if not EMAIL_USER or not EMAIL_PASS:
        return
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK" or not messages[0]:
            return

        for num in messages[0].split():
            status, data = mail.fetch(num, "(RFC822)")
            if status != "OK":
                continue

            msg = email.message_from_bytes(data[0][1])
            subject = decode_str(msg.get("Subject", ""))
            sender = decode_str(msg.get("From", ""))
            print(f" [New Mail] 收到邮件: 《{subject}》 来自: {sender}")

            attachments_to_print = []
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue

                raw_filename = part.get_filename()
                content_type = part.get_content_type().lower()

                if not raw_filename and "pdf" in content_type:
                    raw_filename = f"document_{int(time.time())}.pdf"

                if raw_filename:
                    filename = clean_filename(raw_filename)
                    ext = os.path.splitext(filename)[1].lower()

                    if ext in [".pdf", ".jpg", ".jpeg", ".png", ".txt"] or "pdf" in content_type:
                        payload = part.get_payload(decode=True)
                        if not payload:
                            continue

                        # 图片过滤小于 40KB 签名图标；PDF 一律保留
                        if ext in [".jpg", ".jpeg", ".png"] and len(payload) < 40 * 1024:
                            print(f" [Filter] 过滤小图标/签名: {filename}")
                            continue

                        save_path = os.path.join(TEMP_DIR, f"{int(time.time())}_{filename}")
                        with open(save_path, "wb") as f:
                            f.write(payload)
                        attachments_to_print.append((save_path, filename))
                        print(f" [Found Attachment] 成功捕获待打印文件: {filename} (大小: {len(payload)} 字节)")

            should_print = any(k in subject for k in TRIGGER_KEYWORDS) or any(
                any(k in fname for k in TRIGGER_KEYWORDS) for _, fname in attachments_to_print
            )

            if should_print and attachments_to_print:
                for fpath, fname in attachments_to_print:
                    print_file(fpath, fname)
            elif attachments_to_print:
                print(f" [Ignore] 邮件未包含触发关键词，跳过打印: 《{subject}》")
                for fpath, _ in attachments_to_print:
                    if os.path.exists(fpath):
                        os.remove(fpath)

            mail.store(num, "+FLAGS", "\\Deleted")
        mail.expunge()

    except Exception as e:
        print(f" [Mail Loop Error] 邮件处理异常: {e}")
    finally:
        if mail:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass

def main():
    print("==========================================")
    print(" [Cloud Print Daemon] 云打印守护进程已就绪...")
    print(f" 当前硬件匹配策略: {get_hardware_profile()}")
    print("==========================================")
    while True:
        process_email()
        time.sleep(10)

if __name__ == "__main__":
    main()
