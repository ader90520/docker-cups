#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import time
import uuid
import email
import imaplib
import threading
import subprocess
import urllib.request
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from handlers.base_handler import BaseHandler
from handlers.print_handler import process_image_for_print

CONFIG_FILE = "/opt/mail_config.json"
MAIL_TASK_DIR = "/tmp/mail_print_tasks"
os.makedirs(MAIL_TASK_DIR, exist_ok=True)

def load_config():
    default_cfg = {
        "enable": True,
        "server": "imap.qq.com",
        "port": 993,
        "user": "",
        "password": "",
        "keyword": "",
        "whitelist": "",
        "pushplus_token": "",
        "default_printer": "HP_LaserJet_Pro_MFP_M126a"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_cfg.update(data)
                return default_cfg
        except Exception:
            pass
    return default_cfg

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def pushplus_notify(token, title, content):
    if not token:
        return
    try:
        url = "http://www.pushplus.plus/send"
        data = json.dumps({"token": token, "title": title, "content": content}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[PushPlus] 推送异常: {e}", flush=True)

def decode_str(s):
    if not s:
        return ""
    try:
        decoded_list = email.header.decode_header(s)
        result = []
        for val, charset in decoded_list:
            if isinstance(val, bytes):
                result.append(val.decode(charset or "utf-8", errors="ignore"))
            else:
                result.append(str(val))
        return "".join(result)
    except Exception:
        return str(s)

def mail_worker_loop():
    print("[MailWorker] 邮件自动打印轮询后台守护线程启动...", flush=True)
    while True:
        try:
            cfg = load_config()
            if not cfg.get("enable") or not cfg.get("server") or not cfg.get("user") or not cfg.get("password"):
                time.sleep(6)
                continue

            mail = imaplib.IMAP4_SSL(cfg["server"], int(cfg.get("port", 993)), timeout=15)
            mail.login(cfg["user"], cfg["password"])
            mail.select("INBOX")

            status, msg_nums = mail.search(None, "UNSEEN")
            if status != "OK" or not msg_nums[0]:
                mail.logout()
                time.sleep(5)
                continue

            for num in msg_nums[0].split():
                status, data = mail.fetch(num, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = decode_str(msg.get("Subject", "")).strip()
                from_str = decode_str(msg.get("From", "")).strip()
                from_email = re.findall(r"[\w\.-]+@[\w\.-]+", from_str)
                sender = from_email[0] if from_email else from_str

                print(f"[MailWorker] 发现未读邮件 -> 发件人: {sender} | 标题: {subject}", flush=True)

                # 白名单校验
                whitelist = [w.strip().lower() for w in cfg.get("whitelist", "").split(",") if w.strip()]
                if whitelist and sender.lower() not in whitelist:
                    print(f"[MailWorker] 发件人 [{sender}] 不在白名单，跳过", flush=True)
                    mail.store(num, "+FLAGS", "\\Seen")
                    continue

                # 暗号过滤
                kw = cfg.get("keyword", "").strip()
                has_keyword = True
                if kw:
                    has_keyword = (kw.lower() in subject.lower())
                    if not has_keyword:
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                try:
                                    txt = part.get_payload(decode=True).decode(errors="ignore")
                                    if kw.lower() in txt.lower():
                                        has_keyword = True
                                        break
                                except Exception:
                                    pass

                if not has_keyword:
                    print(f"[MailWorker] 邮件不包含暗号 [{kw}]，跳过", flush=True)
                    mail.store(num, "+FLAGS", "\\Seen")
                    continue

                printer = cfg.get("default_printer") or "HP_LaserJet_Pro_MFP_M126a"
                is_raw_mode = ("原图" in subject)
                printed_count = 0

                env = os.environ.copy()
                env["CUPS_SERVER"] = "/run/cups/cups.sock"
                env["LANG"] = "C"

                for part in msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    filename = part.get_filename()
                    if filename:
                        filename = decode_str(filename)
                        ext = os.path.splitext(filename)[-1].lower()
                        if ext in [".jpg", ".jpeg", ".png", ".pdf", ".ofd", ".doc", ".docx"]:
                            task_token = uuid.uuid4().hex[:8]
                            save_path = os.path.join(MAIL_TASK_DIR, f"mail_{task_token}{ext}")
                            with open(save_path, "wb") as f:
                                f.write(part.get_payload(decode=True))

                            target_print = save_path
                            if not is_raw_mode and ext in [".jpg", ".jpeg", ".png"]:
                                cam_path = os.path.join(MAIL_TASK_DIR, f"cam_{task_token}.jpg")
                                if process_image_for_print(save_path, cam_path):
                                    target_print = cam_path

                            cmd = [
                                "lp", "-d", printer,
                                "-o", "media=A4",
                                "-o", "PageSize=A4",
                                "-o", "natural-scaling=90",
                                "-o", "position=center",
                                target_print
                            ]
                            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                            if res.returncode == 0:
                                printed_count += 1
                                print(f"[MailWorker] ✔ 附件 [{filename}] 打印成功: {res.stdout.strip()}", flush=True)
                            else:
                                print(f"[MailWorker] ✖ 附件打印被拒绝: {res.stderr.strip()}", flush=True)

                mail.store(num, "+FLAGS", "\\Seen")

                if printed_count > 0:
                    pushplus_notify(cfg.get("pushplus_token"), "🖨️ 云邮件自动打印完成", f"发件人: {sender}\n标题: {subject}\n打印附件数: {printed_count}")

            mail.logout()
        except Exception as e:
            print(f"[MailWorker] 轮询异常恢复: {e}", flush=True)
        time.sleep(5)

# 启动轮询守护线程
worker_thread = threading.Thread(target=mail_worker_loop, daemon=True)
worker_thread.start()

class MailConfigHandler(BaseHandler):
    def get(self):
        cfg = load_config()
        self.write_json(True, "获取配置成功", data=cfg)

    def post(self):
        try:
            data = json.loads(self.request.body.decode("utf-8"))
            if save_config(data):
                self.write_json(True, "云邮件策略配置保存成功！")
            else:
                self.write_json(False, "写入配置文件失败")
        except Exception as e:
            self.write_json(False, f"保存异常: {str(e)}")
