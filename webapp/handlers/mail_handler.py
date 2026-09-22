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
    "accounts": [],  # 存储多邮箱列表: [{"id": "...", "name": "QQ", "pop_server": "pop.qq.com", "pop_port": 995, "email_user": "...", "email_pass": "...", "whitelist": "", "auto_enhance": True}]
    "printer_name": "",
    "check_interval": 30
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                # 兼容旧单账号配置自动升级为多账号
                if "email_user" in cfg and cfg.get("email_user") and not cfg.get("accounts"):
                    cfg["accounts"] = [{
                        "id": str(int(time.time())),
                        "name": "默认邮箱",
                        "pop_server": cfg.get("pop_server", "pop.qq.com"),
                        "pop_port": cfg.get("pop_port", 995),
                        "email_user": cfg.get("email_user", ""),
                        "email_pass": cfg.get("email_pass", ""),
                        "whitelist": cfg.get("whitelist", ""),
                        "auto_enhance": cfg.get("auto_enhance", True),
                        "active": True
                    }]
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

def send_pushplus(token, title, content):
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

class MultiMailWorker(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True
        self.statuses = {}

    def run(self):
        while self.running:
            cfg = load_config()
            if not cfg.get("enabled"):
                self.statuses = {"global": "云打印守护总开关已关闭"}
                time.sleep(10)
                continue

            accounts = cfg.get("accounts", [])
            if not accounts:
                self.statuses = {"global": "暂未配置任何邮箱账号"}
                time.sleep(10)
                continue

            target_printer = cfg.get("printer_name", "")
            push_token = cfg.get("pushplus_token", "")

            for acc in accounts:
                acc_user = acc.get("email_user", "")
                if not acc_user or not acc.get("email_pass") or not acc.get("active", True):
                    continue

                try:
                    self.check_single_account(acc, target_printer, push_token)
                    self.statuses[acc_user] = f"监听正常 (最后同步: {time.strftime('%H:%M:%S')})"
                except Exception as e:
                    self.statuses[acc_user] = f"收取异常: {str(e)}"

            time.sleep(int(cfg.get("check_interval", 30)))

    def check_single_account(self, acc, target_printer, push_token):
        user = acc["email_user"]
        server = poplib.POP3_SSL(acc.get("pop_server", "pop.qq.com"), int(acc.get("pop_port", 995)), timeout=20)
        server.user(user)
        server.pass_(acc["email_pass"])

        num_messages = len(server.list()[1])
        if num_messages == 0:
            server.quit()
            return

        whitelist = [x.strip().lower() for x in acc.get("whitelist", "").split(",") if x.strip()]

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
                        if acc.get("auto_enhance", True) and ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]:
                            enh_path = os.path.join(TASK_DIR, f"enh_{filename}.png")
                            if enhance_homework_image(save_path, enh_path):
                                final_file = enh_path

                        lp_cmd = ["lp"]
                        if target_printer: lp_cmd.extend(["-d", target_printer])
                        lp_cmd.extend(["-o", "fit-to-page", final_file])

                        res = subprocess.run(lp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        if res.returncode == 0:
                            printed_files.append(filename)

                        for p in [save_path, os.path.join(TASK_DIR, f"enh_{filename}.png")]:
                            if os.path.exists(p):
                                try: os.remove(p)
                                except Exception: pass

            if printed_files:
                # 发送微信推送通知，清晰展示接收邮箱
                if push_token:
                    file_list_html = "".join([f"<li><b>{f}</b></li>" for f in printed_files])
                    push_content = f"""
                    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 12px; border-left: 4px solid #0066cc;">
                        <h3 style="color: #0066cc; margin: 0 0 10px 0;">🖨️ 打印机任务完成通知</h3>
                        <p style="margin: 4px 0;"><b>接收邮箱：</b><span style="color: #2b6cb0;">{user}</span></p>
                        <p style="margin: 4px 0;"><b>发件人：</b>{sender_email}</p>
                        <p style="margin: 4px 0;"><b>主题：</b>{subject}</p>
                        <p style="margin: 4px 0;"><b>打印文件列表：</b></p>
                        <ul style="margin: 5px 0;">{file_list_html}</ul>
                        <p style="color: #888; font-size: 12px; margin-top: 10px;">打印时间：{time.strftime('%Y-%m-%d %H:%M:%S')}</p>
                    </div>
                    """
                    send_pushplus(push_token, f"🖨️ [{user}] 打印完成：{subject[:10]}", push_content)

                server.dele(i)

        server.quit()

worker = MultiMailWorker()
worker.start()

class MailConfigHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        cfg = load_config()
        safe_cfg = json.loads(json.dumps(cfg))
        
        # 密码掩码保护
        for acc in safe_cfg.get("accounts", []):
            if acc.get("email_pass"):
                acc["email_pass"] = "******"
        if safe_cfg.get("pushplus_token"):
            safe_cfg["pushplus_token"] = safe_cfg["pushplus_token"][:4] + "********" if len(safe_cfg["pushplus_token"]) > 4 else "******"

        self.write(json.dumps({"config": safe_cfg, "statuses": worker.statuses}))

    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            cfg = load_config()

            if "enabled" in data: cfg["enabled"] = data["enabled"]
            if "printer_name" in data: cfg["printer_name"] = data["printer_name"]
            
            # 更新 PushPlus Token
            if data.get("pushplus_token") and "******" not in data["pushplus_token"]:
                cfg["pushplus_token"] = data["pushplus_token"].strip()

            # 更新邮箱列表
            if "accounts" in data:
                old_pass_map = {a.get("id"): a.get("email_pass") for a in cfg.get("accounts", [])}
                new_accounts = []
                for acc in data["accounts"]:
                    acc_id = acc.get("id")
                    # 如果未修改密码则保持原密码
                    if acc.get("email_pass") == "******":
                        acc["email_pass"] = old_pass_map.get(acc_id, "")
                    new_accounts.append(acc)
                cfg["accounts"] = new_accounts

            save_config(cfg)
            self.write(json.dumps({"success": True, "msg": "邮箱配置已保存，系统正在无缝热加载生效！"}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))
