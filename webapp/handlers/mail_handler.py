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
    "enabled": True,
    "pushplus_token": "",
    "accounts": [],
    "printer_name": "",
    "check_interval": 30
}

def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg.update(saved)
        except Exception:
            pass
    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)

def decode_str(s):
    if not s:
        return ""
    try:
        decoded_list = decode_header(s)
        res = []
        for value, charset in decoded_list:
            if isinstance(value, bytes):
                res.append(value.decode(charset or 'utf-8', errors='ignore'))
            else:
                res.append(str(value))
        return "".join(res)
    except Exception:
        return str(s)

def send_pushplus(token, title, content):
    if not token or not token.strip():
        return
    try:
        requests.post("https://www.pushplus.plus/send", json={
            "token": token.strip(),
            "title": title[:30],
            "content": content,
            "template": "html"
        }, timeout=8)
    except Exception as e:
        print(f"[PushPlus] 推送失败: {e}")

class MultiMailWorker(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True
        self.statuses = {}

    def run(self):
        while self.running:
            try:
                cfg = load_config()
                if not cfg.get("enabled"):
                    self.statuses = {"global": "云打印已暂停"}
                    time.sleep(10)
                    continue

                accounts = cfg.get("accounts", [])
                if not accounts:
                    self.statuses = {"global": "暂未配置任何邮箱"}
                    time.sleep(10)
                    continue

                target_printer = cfg.get("printer_name", "")
                push_token = cfg.get("pushplus_token", "")

                for acc in accounts:
                    acc_user = acc.get("email_user", "").strip()
                    if not acc_user or not acc.get("email_pass") or not acc.get("active", True):
                        continue

                    try:
                        self.check_single_account(acc, target_printer, push_token)
                        self.statuses[acc_user] = f"正常运行 (最后巡检: {time.strftime('%H:%M:%S')})"
                    except Exception as err:
                        self.statuses[acc_user] = f"连接受阻: {str(err)[:40]}"

                interval = int(cfg.get("check_interval", 30))
                time.sleep(max(10, interval))
            except Exception as e:
                time.sleep(10)

    def check_single_account(self, acc, target_printer, push_token):
        user = acc["email_user"].strip()
        server_host = acc.get("pop_server", "pop.qq.com").strip()
        server_port = int(acc.get("pop_port", 995))

        server = poplib.POP3_SSL(server_host, server_port, timeout=15)
        try:
            server.user(user)
            server.pass_(acc["email_pass"].strip())
            
            num_messages = len(server.list()[1])
            if num_messages == 0:
                return

            whitelist_raw = acc.get("whitelist", "")
            whitelist = [x.strip().lower() for x in whitelist_raw.split(",") if x.strip()]

            # 仅处理最近 5 封未删邮件
            for i in range(num_messages, max(0, num_messages - 5), -1):
                try:
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
                                save_path = os.path.join(TASK_DIR, f"task_{int(time.time())}_{filename}")
                                with open(save_path, "wb") as f:
                                    f.write(part.get_payload(decode=True))

                                final_file = save_path
                                # 图像作业自动增强处理
                                if acc.get("auto_enhance", True) and ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]:
                                    enh_path = os.path.join(TASK_DIR, f"enh_{filename}.png")
                                    if enhance_homework_image(save_path, enh_path):
                                        final_file = enh_path

                                # 提交 CUPS 打印
                                lp_cmd = ["lp"]
                                if target_printer:
                                    lp_cmd.extend(["-d", target_printer])
                                lp_cmd.extend(["-o", "fit-to-page", final_file])

                                res = subprocess.run(lp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                                if res.returncode == 0:
                                    printed_files.append(filename)

                                for p in [save_path, os.path.join(TASK_DIR, f"enh_{filename}.png")]:
                                    if os.path.exists(p):
                                        try: os.remove(p)
                                        except Exception: pass

                    if printed_files:
                        if push_token:
                            file_html = "".join([f"<li><b>{f}</b></li>" for f in printed_files])
                            body = f"""
                            <div style="padding:10px; border-left:4px solid #0066cc; font-family:sans-serif;">
                                <h3 style="color:#0066cc; margin:0 0 8px 0;">🖨️ 打印成功通知</h3>
                                <p style="margin:4px 0;"><b>接收邮箱：</b><span style="color:#2b6cb0;">{user}</span></p>
                                <p style="margin:4px 0;"><b>发件人：</b>{sender_email}</p>
                                <p style="margin:4px 0;"><b>主题：</b>{subject}</p>
                                <p style="margin:4px 0;"><b>已打印文件：</b></p>
                                <ul>{file_html}</ul>
                                <span style="font-size:12px; color:#888;">完成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}</span>
                            </div>
                            """
                            send_pushplus(push_token, f"🖨️ [{user}] 已打印附件", body)
                        # 成功打印后清除该邮件
                        server.dele(i)
                except Exception:
                    continue
        finally:
            try:
                server.quit()
            except Exception:
                pass

worker = MultiMailWorker()
worker.start()

class MailConfigHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        cfg = load_config()
        safe_cfg = json.loads(json.dumps(cfg))
        
        for acc in safe_cfg.get("accounts", []):
            if acc.get("email_pass"):
                acc["email_pass"] = "******"
        if safe_cfg.get("pushplus_token"):
            token = safe_cfg["pushplus_token"]
            safe_cfg["pushplus_token"] = token[:4] + "********" if len(token) > 4 else "******"

        self.write(json.dumps({"config": safe_cfg, "statuses": worker.statuses}))

    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            cfg = load_config()

            if "enabled" in data: cfg["enabled"] = bool(data["enabled"])
            if "printer_name" in data: cfg["printer_name"] = str(data["printer_name"]).strip()
            
            new_token = data.get("pushplus_token", "").strip()
            if new_token and "******" not in new_token:
                cfg["pushplus_token"] = new_token

            if "accounts" in data:
                old_pass_map = {a.get("id"): a.get("email_pass") for a in cfg.get("accounts", [])}
                new_accounts = []
                for acc in data["accounts"]:
                    acc_id = acc.get("id")
                    if acc.get("email_pass") == "******":
                        acc["email_pass"] = old_pass_map.get(acc_id, "")
                    new_accounts.append(acc)
                cfg["accounts"] = new_accounts

            save_config(cfg)
            self.write(json.dumps({"success": True, "msg": "配置已保存并即时生效！"}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))
