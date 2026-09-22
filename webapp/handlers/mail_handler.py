#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import poplib
import email
from email.header import decode_header
import smtplib
from email.mime.text import MIMEText
import threading
import subprocess
import requests
import tornado.web

from handlers.print_handler import enhance_homework_image

CONFIG_FILE = "/etc/cups/mail_print_config.json"
TASK_DIR = "/tmp/mail_print_tasks"
os.makedirs(TASK_DIR, exist_ok=True)

DEFAULT_CONFIG = {
    "enabled": False,
    "pop_server": "pop.qq.com",
    "pop_port": 995,
    "smtp_server": "smtp.qq.com",
    "smtp_port": 465,
    "email_user": "",
    "email_pass": "",
    "pushplus_token": "",
    "whitelist": "",
    "printer_name": "",
    "check_interval": 30,
    "auto_enhance": True
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                DEFAULT_CONFIG.update(cfg)
        except Exception:
            pass
    return DEFAULT_CONFIG

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)

def decode_str(s):
    value, charset = decode_header(s)[0]
    if charset:
        try: value = value.decode(charset)
        except Exception: value = str(value)
    return str(value)

def send_pushplus_notification(token, title, content):
    if not token: return
    try:
        requests.post("https://www.pushplus.plus/send", json={
            "token": token.strip(),
            "title": title,
            "content": content,
            "template": "html"
        }, timeout=10)
    except Exception as e:
        print(f"[PushPlus] 推送异常: {e}")

class MailWorker(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True
        self.last_status = "未启用"

    def run(self):
        while self.running:
            cfg = load_config()
            if not cfg.get("enabled") or not cfg.get("email_user") or not cfg.get("email_pass"):
                self.last_status = "云邮箱打印未开启或未配置邮箱凭据"
                time.sleep(10)
                continue

            try:
                self.last_status = "正在检查新邮件..."
                self.check_and_print(cfg)
                self.last_status = f"正常运行中 (最近检查: {time.strftime('%H:%M:%S')})"
            except Exception as e:
                self.last_status = f"邮箱连接异常: {str(e)}"

            time.sleep(int(cfg.get("check_interval", 30)))

    def check_and_print(self, cfg):
        server = poplib.POP3_SSL(cfg["pop_server"], int(cfg["pop_port"]), timeout=20)
        server.user(cfg["email_user"])
        server.pass_(cfg["email_pass"])

        num_messages = len(server.list()[1])
        if num_messages == 0:
            server.quit()
            return

        whitelist = [x.strip().lower() for x in cfg.get("whitelist", "").split(",") if x.strip()]
        target_printer = cfg.get("printer_name", "")

        for i in range(num_messages, max(0, num_messages - 5), -1):
            raw_email = b"\n".join(server.retr(i)[1])
            msg = email.message_from_bytes(raw_email)
            
            sender = decode_str(msg.get("From", ""))
            subject = decode_str(msg.get("Subject", "无主题"))
            sender_email = email.utils.parseaddr(sender)[1].lower()

            if whitelist and sender_email not in whitelist:
                continue

            printed_files = []
            for part in msg.walk():
                if part.get_content_disposition() == 'attachment':
                    filename = part.get_filename()
                    if filename:
                        filename = decode_str(filename)
                        ext = os.path.splitext(filename)[1].lower()
                        save_path = os.path.join(TASK_DIR, f"mail_{int(time.time())}_{filename}")
                        with open(save_path, "wb") as f:
                            f.write(part.get_payload(decode=True))

                        final_file = save_path
                        if cfg.get("auto_enhance") and ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]:
                            enhanced = os.path.join(TASK_DIR, f"enh_{filename}.png")
                            if enhance_homework_image(save_path, enhanced):
                                final_file = enhanced

                        lp_cmd = ["lp"]
                        if target_printer: lp_cmd.extend(["-d", target_printer])
                        lp_cmd.extend(["-o", "fit-to-page", final_file])
                        
                        ret = subprocess.run(lp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        if ret.returncode == 0:
                            printed_files.append(filename)

                        for p in [save_path, os.path.join(TASK_DIR, f"enh_{filename}.png")]:
                            if os.path.exists(p):
                                try: os.remove(p)
                                except Exception: pass

            if printed_files:
                if cfg.get("pushplus_token"):
                    file_list_html = "".join([f"<li><b>{f}</b></li>" for f in printed_files])
                    push_content = f"""
                    <div style="font-family: sans-serif; padding: 10px; border-left: 4px solid #0066cc;">
                        <h3 style="color: #0066cc;">🖨️ 打印机任务完成通知</h3>
                        <p><b>发件人：</b>{sender_email}</p>
                        <p><b>邮件主题：</b>{subject}</p>
                        <p><b>打印文件：</b></p>
                        <ul>{file_list_html}</ul>
                        <p style="color: #888; font-size: 12px;">时间：{time.strftime('%Y-%m-%d %H:%M:%S')}</p>
                    </div>
                    """
                    send_pushplus_notification(cfg["pushplus_token"], f"🖨️ 文档打印成功：{subject[:12]}", push_content)

                server.dele(i)

        server.quit()

worker = MailWorker()
worker.start()

class MailConfigHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        cfg = load_config()
        safe_cfg = dict(cfg)
        if safe_cfg.get("email_pass"): safe_cfg["email_pass"] = "******"
        if safe_cfg.get("pushplus_token"): safe_cfg["pushplus_token"] = safe_cfg["pushplus_token"][:4] + "********" if len(safe_cfg["pushplus_token"]) > 4 else "******"
        self.write(json.dumps({"config": safe_cfg, "status": worker.last_status}))

    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            cfg = load_config()
            for key in ["enabled", "pop_server", "pop_port", "smtp_server", "smtp_port", "email_user", "whitelist", "printer_name", "check_interval", "auto_enhance"]:
                if key in data: cfg[key] = data[key]
            if data.get("email_pass") and data["email_pass"] != "******":
                cfg["email_pass"] = data["email_pass"]
            if data.get("pushplus_token") and "******" not in data["pushplus_token"]:
                cfg["pushplus_token"] = data["pushplus_token"].strip()

            save_config(cfg)
            self.write(json.dumps({"success": True, "msg": "配置已保存并即时生效！"}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))
