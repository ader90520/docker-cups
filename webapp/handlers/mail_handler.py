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
    if os.path.exists(CONFIG_FILE) and os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception as e:
            print(f"[MailConfig] 读取配置异常: {e}", flush=True)
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
        # 如果挂载点被宿主机误创建为目录，强行纠正
        if os.path.exists(CONFIG_FILE) and os.path.isdir(CONFIG_FILE):
            os.system(f"rm -rf {CONFIG_FILE}")

        cfg_dir = os.path.dirname(CONFIG_FILE)
        if cfg_dir and not os.path.exists(cfg_dir):
            os.makedirs(cfg_dir, exist_ok=True)

        json_str = json.dumps(cfg, ensure_ascii=False, indent=2)

        # 覆写文件
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(json_str)
            f.flush()

        try:
            os.chmod(CONFIG_FILE, 0o666)
        except Exception:
            pass

        mail_wake_event.set()
        return True, ""
    except Exception as e:
        err = str(e)
        print(f"[MailConfig] 写入配置失败: {err}", flush=True)
        return False, err

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
    print("[MailWorker] 云邮件守护线程运行中...", flush=True)
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

                        raw_from = decode_str(msg.get("From", ""))
                        _, clean_from_addr = parseaddr(raw_from)
                        clean_from_addr = clean_from_addr.lower().strip()

                        whitelist = cfg.get("whitelist", "").strip()
                        if whitelist:
                            allowed_list = [w.strip().lower() for w in whitelist.split(",") if w.strip()]
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
                                    save_path = os.path.join(UPLOAD_DIR, f"mail_{int(time.time())}_{filename}")
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
            body_data = {}
            if self.request.body:
                try:
                    body_data = json.loads(self.request.body.decode("utf-8"))
                except Exception:
                    pass

            def fetch(key, default=""):
                if key in body_data:
                    return body_data[key]
                return self.get_argument(key, default)

            raw_enable = fetch("enable", "false")
            if isinstance(raw_enable, bool):
                enable = raw_enable
            else:
                enable = str(raw_enable).lower() in ["true", "1", "on"]

            server = str(fetch("server", "imap.qq.com")).strip()
            
            try:
                port = int(fetch("port", 993))
            except Exception:
                port = 993

            user = str(fetch("user", "")).strip()
            password = str(fetch("password", "")).strip()
            keyword = str(fetch("keyword", "")).strip()
            whitelist = str(fetch("whitelist", "")).strip()
            pushplus_token = str(fetch("pushplus_token", "")).strip()
            default_printer = str(fetch("default_printer", "")).strip()

            old_cfg = load_mail_config()
            if password == "******" or not password:
                password = old_cfg.get("password", "")

            if not default_printer:
                default_printer = old_cfg.get("default_printer", "")

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

            ok, err = save_mail_config(new_cfg)
            if ok:
                self.write_json(True, "云邮箱及微信通知设置已成功保存并立即生效！")
            else:
                self.write_json(False, f"写入配置失败: {err}")
        except Exception as e:
            self.write_json(False, f"保存异常: {str(e)}")
