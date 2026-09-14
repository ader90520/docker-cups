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
import gc
from email.header import decode_header
from email.utils import collapse_rfc2231_value

# 容错引入 PIL，避免 slim 精简镜像缺失依赖报错
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

# 匹配主流打印触发关键词
TRIGGER_KEYWORDS = [
    "打", "print", "作业", "试卷", "练习", "复习", "打卡",
    "语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "科学",
    "一年级", "二年级", "三年级", "四年级", "五年级", "六年级",
    "初一", "初二", "初三", "七年级", "八年级", "九年级", "高一", "高二", "高三"
]

TEMP_DIR = "/tmp/mail_print_tasks"
os.makedirs(TEMP_DIR, exist_ok=True)

def get_system_memory_mb():
    """实时读取系统总可用内存 (MB)"""
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if 'MemTotal' in line:
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 1024

def get_target_printer():
    """动态获取 CUPS 默认打印机或第一台可用打印机"""
    try:
        res = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if "destination: " in res.stdout:
            printer = res.stdout.split("destination: ")[-1].strip()
            if printer:
                return printer

        res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        for line in res_p.stdout.splitlines():
            if line.startswith("printer "):
                return line.split()[1].strip()
    except Exception as e:
        print(f" [Printer Detect Warning] 探测异常: {e}", flush=True)
    return "HP_LaserJet_Pro_MFP_M126a"

def send_pushplus_notice(title, content):
    """PushPlus 微信结果通知"""
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
        print(f" [Push Error] 推送失败: {e}", flush=True)

def decode_str(header_text):
    """邮件标题/发送人编码深度解码"""
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
    """文件名安全清洗，防止路径穿越与特殊符号导致命令行解析失败"""
    if not filename:
        return f"doc_{int(time.time())}.pdf"
    try:
        if isinstance(filename, tuple):
            filename = collapse_rfc2231_value(filename)
        filename = decode_str(str(filename))
        if "%" in filename:
            filename = urllib.parse.unquote(filename)
        clean_name = filename.strip().replace("/", "_").replace("\\", "_").replace(" ", "_")
        return clean_name if clean_name else f"doc_{int(time.time())}.pdf"
    except Exception as e:
        print(f" [Filename Parse Warning] 解析警告: {e}", flush=True)
        return f"doc_{int(time.time())}.pdf"

def optimize_image_for_print(filepath):
    """自适应降维大图，打印前强制释放内存，绝不让小盒子死机"""
    if not HAS_PIL:
        return
    try:
        ext = os.path.splitext(filepath)[1].lower()
        if ext in [".jpg", ".jpeg", ".png"]:
            mem = get_system_memory_mb()
            # 1G 内存（海纳思）设定 1800 像素阈值，2G（N1）设定 3600 像素
            max_limit = 1800 if mem <= 1200 else 3600
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
                    print(f" [Optimizer] 图片自适应压缩 (可用内存 {mem}M): {w}x{h} -> {new_size[0]}x{new_size[1]}", flush=True)
            gc.collect()
    except Exception as e:
        print(f" [Optimizer Warning] 图片优化跳过: {e}", flush=True)

def print_file(filepath, filename):
    """提交打印任务，包含真实结果校验与超时中断"""
    printer_name = get_target_printer()
    if not printer_name:
        err_msg = "未找到可用打印机，请先访问 Web 后台 (http://盒子IP:631) 添加并启用打印机。"
        print(f" [Print Error] {err_msg}", flush=True)
        send_pushplus_notice("❌ 打印失败提醒", f"文件 <b>{filename}</b> 提交失败：<br>{err_msg}")
        return False

    try:
        optimize_image_for_print(filepath)

        # 核心出纸指令：移除不稳定参数，加入 fit-to-page 与 A4 锁定
        cmd = [
            "lp",
            "-d", printer_name,
            "-o", "fit-to-page",
            "-o", "media=A4",
            filepath
        ]

        # 60 秒超时控制，彻底防止任务死锁阻塞后续任务
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        if res.returncode == 0:
            job_id = res.stdout.strip()
            print(f" [Print Success] 任务提交成功 -> 目标设备: [{printer_name}] 文件: {filename} (编号: {job_id})", flush=True)
            send_pushplus_notice(
                "🖨️ 打印机出纸提醒",
                f"已向打印机 <b>{printer_name}</b> 提交任务：<br>"
                f"文件名：<b>{filename}</b><br>"
                f"任务编号：{job_id}<br>"
                f"正在出纸，请在设备旁稍候。"
            )
            return True
        else:
            err_output = res.stderr.strip()
            print(f" [Print Error] lp 失败: {err_output}", flush=True)
            send_pushplus_notice("❌ 打印失败提醒", f"打印 <b>{filename}</b> 出错：<br>{err_output}")
            return False

    except subprocess.TimeoutExpired:
        print(f" [Print Error] 打印提交超时: {filename}", flush=True)
        send_pushplus_notice("❌ 打印超时提醒", f"向 <b>{printer_name}</b> 提交任务超时，请检查打印机状态。")
        return False
    except Exception as e:
        print(f" [System Error] 提交异常: {e}", flush=True)
        return False
    finally:
        # 无论成功失败，立即销毁临时文件，彻底保护 eMMC 磁盘不被撑爆
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
        gc.collect()

def process_email():
    """邮件轮询核心：提取附件、匹配关键词、成功后永久删除邮件"""
    if not EMAIL_USER or not EMAIL_PASS:
        return
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993, timeout=15)
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
            print(f" [New Mail] 收到邮件: 《{subject}》 来自: {sender}", flush=True)

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

                        # 过滤小于 40KB 的小头像/邮件签名图标
                        if ext in [".jpg", ".jpeg", ".png"] and len(payload) < 40 * 1024:
                            print(f" [Filter] 过滤小图标: {filename}", flush=True)
                            continue

                        save_path = os.path.join(TEMP_DIR, f"{int(time.time())}_{filename}")
                        with open(save_path, "wb") as f:
                            f.write(payload)
                        attachments_to_print.append((save_path, filename))
                        print(f" [Found Attachment] 提取待打印文件: {filename} (大小: {len(payload)} 字节)", flush=True)

            # 匹配邮件标题或附件名中是否含有触发词
            should_print = any(k in subject for k in TRIGGER_KEYWORDS) or any(
                any(k in fname for k in TRIGGER_KEYWORDS) for _, fname in attachments_to_print
            )

            if should_print and attachments_to_print:
                all_success = True
                for fpath, fname in attachments_to_print:
                    success = print_file(fpath, fname)
                    if not success:
                        all_success = False

                # 打印处理完成，标记删除并在服务端永久清除该邮件
                mail.store(num, "+FLAGS", "\\Deleted")
                print(f" [Mail Clean] 邮件已处理完毕并标记清除: 《{subject}》", flush=True)

            elif attachments_to_print:
                print(f" [Ignore] 未匹配到打印关键词，忽略: 《{subject}》", flush=True)
                for fpath, _ in attachments_to_print:
                    if os.path.exists(fpath):
                        try:
                            os.remove(fpath)
                        except Exception:
                            pass
                # 未触发打印的邮件同样标记删除，防止邮件箱无限堆积
                mail.store(num, "+FLAGS", "\\Deleted")

        # 真正执行物理清除已删除标记的邮件
        mail.expunge()

    except Exception as e:
        print(f" [Mail Loop Error] 邮件轮询异常: {e}", flush=True)
    finally:
        if mail:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass
        gc.collect()

def main():
    print("==========================================", flush=True)
    print(" [Cloud Print Daemon] 邮件云打印服务已启动", flush=True)
    target_printer = get_target_printer()
    print(f" 默认出纸设备: [{target_printer}]", flush=True)
    print(f" 系统可用内存: {get_system_memory_mb()} MB", flush=True)
    print("==========================================", flush=True)
    while True:
        process_email()
        time.sleep(10)

if __name__ == "__main__":
    main()
