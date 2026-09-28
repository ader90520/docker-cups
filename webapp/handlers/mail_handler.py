#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import subprocess
import threading
import email
from email.header import decode_header
import imaplib
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import process_image_for_print

CONFIG_FILE = "/opt/webapp/mail_config.json"

def load_mail_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "enabled": False,
        "imap_server": "imap.qq.com",
        "imap_port": 993,
        "email_user": "",
        "email_pwd": "",
        "check_interval": 30
    }

def save_mail_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def get_default_printer():
    try:
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        res = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        for line in res.stdout.splitlines():
            if "：" in line:
                return line.split("：")[-1].strip()
            elif ":" in line:
                return line.split(":")[-1].strip()
        res_a = subprocess.run(["lpstat", "-a"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        lines = res_a.stdout.splitlines()
        if lines:
            return lines[0].split()[0].strip()
    except Exception:
        pass
    return ""

def decode_str(s):
    value, charset = decode_header(s)[0]
    if isinstance(value, bytes):
        return value.decode(charset or "utf-8", errors="ignore")
    return value

def print_attachment_file(file_path):
    printer = get_default_printer()
    if not printer:
        print("[MailWorker] 未发现可用打印机，跳过打印")
        return False

    target_file = file_path
    ext = os.path.splitext(file_path)[-1].lower()

    # 接入图像增强引擎
    if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
        enhanced_path = file_path + "_enhanced.jpg"
        try:
            if process_image_for_print(file_path, enhanced_path):
                target_file = enhanced_path
        except Exception as e:
            print(f"[MailWorker] 图像增强失败，使用原图打印: {e}")

    env = os.environ.copy()
    env["CUPS_SERVER"] = "/run/cups/cups.sock"
    env["LANG"] = "C"

    cmd = [
        "lp",
        "-d", printer,
        "-n", "1",
        "-o", "media=A4",
        "-o", "PageSize=A4",
        "-o", "natural-scaling=90",
        "-o", "position=center",
        target_file
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    if res.returncode == 0:
        print(f"[MailWorker] 邮件附件成功派发打印: {target_file}")
        return True
    else:
        print(f"[MailWorker] CUPS拒绝邮件打印任务: {res.stderr.strip()}")
        return False

def mail_polling_worker():
    print("[MailWorker] 云邮件打印轮询守护线程已启动...", flush=True)
    while True:
        try:
            cfg = load_mail_config()
            if not cfg.get("enabled", False) or not cfg.get("email_user") or not cfg.get("email_pwd"):
                time.sleep(10)
                continue

            conn = imaplib.IMAP4_SSL(cfg.get("imap_server", "imap.qq.com"), int(cfg.get("imap_port", 993)), timeout=20)
            conn.login(cfg["email_user"], cfg["email_pwd"])
            conn.select("INBOX")

            typ, data = conn.search(None, "UNSEEN")
            if typ == "OK":
                for num in data[0].split():
                    typ_m, msg_data = conn.fetch(num, "(RFC822)")
                    if typ_m != "OK":
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    for part in msg.walk():
                        if part.get_content_maintype() == "multipart":
                            continue
                        filename = part.get_filename()
                        if filename:
                            filename = decode_str(filename)
                            ext = os.path.splitext(filename)[-1].lower()
                            if ext in [".jpg", ".jpeg", ".png", ".pdf", ".bmp", ".webp"]:
                                save_path = os.path.join(UPLOAD_DIR, f"mail_{int(time.time())}_{filename}")
                                with open(save_path, "wb") as f:
                                    f.write(part.get_payload(decode=True))
                                print_attachment_file(save_path)
                    conn.store(num, "+FLAGS", "\\Seen")

            conn.close()
            conn.logout()
        except Exception as e:
            pass

        interval = int(cfg.get("check_interval", 30)) if "cfg" in locals() else 30
        time.sleep(max(10, interval))

# 自动在后台启动邮件轮询线程
mail_thread = threading.Thread(target=mail_polling_worker, daemon=True)
mail_thread.start()

class MailConfigHandler(BaseHandler):
    def get(self):
        cfg = load_mail_config()
        safe_cfg = cfg.copy()
        if safe_cfg.get("email_pwd"):
            safe_cfg["email_pwd"] = "******"
        self.write_json(True, "", data=safe_cfg)

    def post(self):
        try:
            enabled = self.get_argument("enabled", "false").lower() == "true"
            imap_server = self.get_argument("imap_server", "imap.qq.com").strip()
            imap_port = int(self.get_argument("imap_port", "993"))
            email_user = self.get_argument("email_user", "").strip()
            email_pwd = self.get_argument("email_pwd", "").strip()
            check_interval = int(self.get_argument("check_interval", "30"))

            old_cfg = load_mail_config()
            if email_pwd == "******" or not email_pwd:
                email_pwd = old_cfg.get("email_pwd", "")

            new_cfg = {
                "enabled": enabled,
                "imap_server": imap_server,
                "imap_port": imap_port,
                "email_user": email_user,
                "email_pwd": email_pwd,
                "check_interval": check_interval
            }
            if save_mail_config(new_cfg):
                self.write_json(True, "邮件配置已保存并生效")
            else:
                self.write_json(False, "配置文件写入失败")
        except Exception as e:
            self.write_json(False, f"保存配置异常: {str(e)}")
