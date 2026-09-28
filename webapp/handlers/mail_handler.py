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
mail_wake_event = threading.Event()

def load_mail_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "enable": False,
        "server": "imap.qq.com",
        "port": 993,
        "user": "",
        "password": "",
        "keyword": "",
        "whitelist": "",
        "pushplus_token": "",
        "default_printer": ""
    }

def save_mail_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        mail_wake_event.set()
        return True
    except Exception as e:
        print(f"[MailConfig] 写入配置异常: {e}", flush=True)
        return False

def get_target_printer(preferred_printer=""):
    if preferred_printer:
        return preferred_printer
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
    try:
        value, charset = decode_header(s)[0]
        if isinstance(value, bytes):
            return value.decode(charset or "utf-8", errors="ignore")
        return value
    except Exception:
        return str(s)

def print_attachment_file(file_path, printer_name=""):
    printer = get_target_printer(printer_name)
    if not printer:
        print("[MailWorker] 未发现可用打印机，跳过打印", flush=True)
        return False

    target_file = file_path
    ext = os.path.splitext(file_path)[-1].lower()

    if ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
        enhanced_path = file_path + "_enhanced.jpg"
        try:
            if process_image_for_print(file_path, enhanced_path):
                target_file = enhanced_path
        except Exception as e:
            print(f"[MailWorker] 图像增强失败，使用原图打印: {e}", flush=True)

    env = os.environ.copy()
    env["CUPS_SERVER"] = "/run/cups/cups.sock"
    env["LANG"] = "C"

    cmd = [
        "lp",
        "-d", printer,
        "-n", "1",
        "-o", "media=A4",
        "-o", "PageSize=A4",
        "-o", "fit-to-page",
        "-o", "position=center",
        target_file
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    if res.returncode == 0:
        print(f"[MailWorker] 邮件附件成功派发打印: {target_file}", flush=True)
        return True
    else:
        print(f"[MailWorker] CUPS拒绝邮件打印任务: {res.stderr.strip()}", flush=True)
        return False

def mail_polling_worker():
    print("[MailWorker] 云邮件打印轮询守护线程已启动...", flush=True)
    while True:
        try:
            cfg = load_mail_config()
            if cfg.get("enable", False) and cfg.get("user") and cfg.get("password"):
                conn = imaplib.IMAP4_SSL(cfg.get("server", "imap.qq.com"), int(cfg.get("port", 993)), timeout=20)
                conn.login(cfg["user"], cfg["password"])
                conn.select("INBOX")

                typ, data = conn.search(None, "UNSEEN")
                if typ == "OK":
                    for num in data[0].split():
                        typ_m, msg_data = conn.fetch(num, "(RFC822)")
                        if typ_m != "OK":
                            continue
                        msg = email.message_from_bytes(msg_data[0][1])

                        from_addr = decode_str(msg.get("From", ""))
                        whitelist = cfg.get("whitelist", "").strip()
                        if whitelist:
                            allowed = [w.strip() for w in whitelist.split(",") if w.strip()]
                            if allowed and not any(w in from_addr for w in allowed):
                                print(f"[MailWorker] 发件人 {from_addr} 不在白名单中，跳过")
                                continue

                        subject = decode_str(msg.get("Subject", ""))
                        keyword = cfg.get("keyword", "").strip()
                        if keyword and keyword not in subject:
                            print(f"[MailWorker] 邮件主题 {subject} 未命中关键字 {keyword}，跳过")
                            continue

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
                                    print_attachment_file(save_path, cfg.get("default_printer", ""))
                        conn.store(num, "+FLAGS", "\\Seen")

                conn.close()
                conn.logout()
        except Exception:
            pass

        mail_wake_event.wait(timeout=5)
        mail_wake_event.clear()

mail_thread = threading.Thread(target=mail_polling_worker, daemon=True)
mail_thread.start()

class MailConfigHandler(BaseHandler):
    def get(self):
        cfg = load_mail_config()
        safe_cfg = cfg.copy()
        if safe_cfg.get("password"):
            safe_cfg["password"] = "******"
        self.write_json(True, "", data=safe_cfg)

    def post(self):
        try:
            data = {}
            if self.request.body:
                try:
                    data = json.loads(self.request.body.decode("utf-8"))
                except Exception:
                    pass

            def get_val(key, default=""):
                if key in data:
                    return data[key]
                return self.get_argument(key, default)

            raw_enable = get_val("enable", False)
            if isinstance(raw_enable, bool):
                enable = raw_enable
            else:
                enable = str(raw_enable).lower() in ["true", "1", "on"]

            server = str(get_val("server", "imap.qq.com")).strip()
            port = int(get_val("port", 993))
            user = str(get_val("user", "")).strip()
            password = str(get_val("password", "")).strip()
            keyword = str(get_val("keyword", "")).strip()
            whitelist = str(get_val("whitelist", "")).strip()
            pushplus_token = str(get_val("pushplus_token", "")).strip()
            default_printer = str(get_val("default_printer", "")).strip()

            old_cfg = load_mail_config()
            if password == "******" or not password:
                password = old_cfg.get("password", "")

            new_cfg = {
                "enable": enable,
                "server": server,
                "port": port,
                "user": user,
                "password": password,
                "keyword": keyword,
                "whitelist": whitelist,
                "pushplus_token": pushplus_token,
                "default_printer": default_printer
            }

            if save_mail_config(new_cfg):
                self.write_json(True, "云邮件及 PushPlus 微信通知设置已保存生效！")
            else:
                self.write_json(False, "配置文件写入失败")
        except Exception as e:
            self.write_json(False, f"保存配置异常: {str(e)}")
