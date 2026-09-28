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
from email.utils import parseaddr
import imaplib
import urllib.request
import urllib.parse
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

def push_wechat_notice(token, title, content):
    if not token:
        return
    try:
        url = "http://www.pushplus.plus/send"
        data = json.dumps({
            "token": token,
            "title": title,
            "content": content,
            "template": "html"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print(f"[PushPlus] 微信通知推送异常: {e}", flush=True)

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

def print_attachment_file(file_path, printer_name="", skip_filter=False, token=""):
    printer = get_target_printer(printer_name)
    if not printer:
        print("[MailWorker] 未发现可用打印机，跳过打印", flush=True)
        return False

    target_file = file_path
    ext = os.path.splitext(file_path)[-1].lower()

    if not skip_filter and ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
        # 使用唯一命名杜绝覆盖冲突
        enhanced_path = file_path + f"_{time.time_ns()}_enhanced.jpg"
        try:
            if process_image_for_print(file_path, enhanced_path):
                target_file = enhanced_path
        except Exception as e:
            print(f"[MailWorker] 图像增强失败，使用原图: {e}", flush=True)

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
        "-o", "natural-scaling=95",
        "-o", "position=center",
        target_file
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    filename = os.path.basename(file_path)
    if res.returncode == 0:
        print(f"[MailWorker] 邮件附件成功送达打印: {target_file}", flush=True)
        push_wechat_notice(token, "🖨️ 打印出纸成功", f"文件 <b>{filename}</b> 已成功送达打印机！")
        return True
    else:
        err = res.stderr.strip()
        print(f"[MailWorker] CUPS拒绝邮件打印任务: {err}", flush=True)
        push_wechat_notice(token, "⚠️ 打印任务异常告警", f"文件 <b>{filename}</b> 打印失败: {err}")
        return False

def mail_polling_worker():
    print("[MailWorker] 云邮件打印守护线程启动...", flush=True)
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

                        # 严格 RFC 邮件地址解析，杜绝昵称伪造注入
                        raw_from = decode_str(msg.get("From", ""))
                        _, clean_from_addr = parseaddr(raw_from)
                        clean_from_addr = clean_from_addr.lower().strip()

                        whitelist = cfg.get("whitelist", "").strip()
                        if whitelist:
                            allowed_list = [w.strip().lower() for w in whitelist.split(",") if w.strip()]
                            # 严格全匹配或域名后缀匹配
                            if allowed_list and not any(clean_from_addr == w or clean_from_addr.endswith("@" + w) for w in allowed_list):
                                print(f"[MailWorker] 发件人 {clean_from_addr} 不在白名单允许范围内，跳过处理")
                                continue

                        subject = decode_str(msg.get("Subject", ""))
                        keyword = cfg.get("keyword", "").strip()
                        if keyword and keyword not in subject:
                            print(f"[MailWorker] 邮件主题 {subject} 未命中暗号 {keyword}，跳过处理")
                            continue

                        skip_filter = "原图" in subject

                        for part in msg.walk():
                            if part.get_content_maintype() == "multipart":
                                continue
                            filename = part.get_filename()
                            if filename:
                                filename = decode_str(filename)
                                ext = os.path.splitext(filename)[-1].lower()
                                if ext in [".jpg", ".jpeg", ".png", ".pdf", ".bmp", ".webp"]:
                                    save_path = os.path.join(UPLOAD_DIR, f"mail_{int(time.time())}_{uuid.uuid4().hex[:6]}_{filename}")
                                    with open(save_path, "wb") as f:
                                        f.write(part.get_payload(decode=True))
                                    print_attachment_file(save_path, cfg.get("default_printer", ""), skip_filter, cfg.get("pushplus_token", ""))
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
