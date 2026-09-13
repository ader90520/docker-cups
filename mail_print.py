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

# 容错引入 PIL：即使 slim 镜像未安装也不会崩溃
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.qq.com")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
NOTIFY_URL = os.getenv("NOTIFY_URL", "http://www.pushplus.plus/send")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")

# 触发关键词
TRIGGER_KEYWORDS = [
    "打", "print", "作业", "试卷", "练习", "复习", "打卡",
    "语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "科学",
    "一年级", "二年级", "三年级", "四年级", "五年级", "六年级",
    "初一", "初二", "初三", "七年级", "八年级", "九年级", "高一", "高二", "高三"
]

TEMP_DIR = "/tmp/mail_print_tasks"
os.makedirs(TEMP_DIR, exist_ok=True)

def get_system_memory_mb():
    """直接探测宿主系统的总可用内存 (MB)"""
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if 'MemTotal' in line:
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 1024

def get_default_printer():
    """动态获取 CUPS 默认打印机或第一台在线打印机，杜绝写死设备名"""
    try:
        # 1. 尝试获取系统默认打印机
        res = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if "destination: " in res.stdout:
            printer = res.stdout.split("destination: ")[-1].strip()
            if printer:
                return printer

        # 2. 如果未设默认，获取当前注册的第一台打印机
        res = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in res.stdout.splitlines():
            if line.startswith("printer "):
                return line.split()[1].strip()
    except Exception as e:
        print(f" [Printer Detect Warning] 打印机探测异常: {e}")
    return None

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
    """文件名清洗：防止特殊字符与路径穿越"""
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
    """大图降维压缩，防止小盒子内存溢出崩溃"""
    if not HAS_PIL:
        return
    try:
        ext = os.path.splitext(filepath)[1].lower()
        if ext in [".jpg", ".jpeg", ".png"]:
            mem = get_system_memory_mb()
            max_limit = 1800 if mem <= 1200 else 3800
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
                    print(f" [Optimizer] 图片自适应降维 (可用内存 {mem}M): {w}x{h} -> {new_size[0]}x{new_size[1]}")
    except Exception as e:
        print(f" [Optimizer Warning] 图片优化跳过: {e}")

def print_file(filepath, filename):
    printer_name = get_default_printer()
    if not printer_name:
        err_msg = "未检测到已添加的 CUPS 打印机，请先进入 Web 后台 (http://设备IP:631) 添加打印机。"
        print(f" [Print Error] {err_msg}")
        send_pushplus_notice("❌ 打印失败提醒", f"打印 <b>{filename}</b> 失败：<br>{err_msg}")
        return False

    try:
        optimize_image_for_print(filepath)
        # 动态绑定当前系统探测到的打印机
        cmd = ["lp", "-c", "-d", printer_name, "-o", "fit-to-page", filepath]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            print(f" [Print Success] 任务提交成功 -> 目标设备: [{printer_name}] 文件: {filename}")
            send_pushplus_notice("🖨️ 打印机出纸提醒", f"已向打印机 <b>{printer_name}</b> 提交任务：<br><b>{filename}</b><br>正在出纸，请在设备旁等待。")
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

                        # 图片过滤小于 40KB 签名图标；PDF 保留
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
    print(" [Cloud Print Daemon] 邮件云打印服务运行中...")
    printer = get_default_printer()
    print(f" 当前默认出纸设备: [{printer or '暂无，请在Web后台绑定'}]")
    print(f" 运行硬件状态: 总内存 {get_system_memory_mb()} MB")
    print("==========================================")
    while True:
        process_email()
        time.sleep(10)

if __name__ == "__main__":
    main()
