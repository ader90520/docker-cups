cat << 'EOF' > /tmp/mail_handler.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import email
import imaplib
import uuid
import urllib.request
import threading
import subprocess
import numpy as np
from PIL import Image, ImageOps
from email.header import decode_header
from handlers.base_handler import BaseHandler

MAIL_TASK_DIR = "/tmp/mail_print_tasks"
os.makedirs(MAIL_TASK_DIR, exist_ok=True)

class PushPlusNotifier:
    @staticmethod
    def send(token, title, content):
        if not token:
            return
        try:
            url = "http://www.pushplus.plus/send"
            data = json.dumps({
                "token": token.strip(),
                "title": title,
                "content": content,
                "template": "html"
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8)
        except Exception as e:
            print(f"[PushPlus] 推送失败: {e}")

class ImageEnhancer:
    @staticmethod
    def deskew_and_clean(img_path):
        try:
            with Image.open(img_path) as src:
                img = ImageOps.exif_transpose(src).convert("L")
                if max(img.size) > 2000:
                    img.thumbnail((2000, 2000), Image.Resampling.BILINEAR)
                arr = np.array(img, dtype=np.uint8)

                farr = arr.astype(np.float32)
                clean_arr = np.clip((farr - 55.0) * (255.0 / (195.0 - 55.0)), 0, 255).astype(np.uint8)

                out = os.path.join(MAIL_TASK_DIR, f"mail_clean_{uuid.uuid4().hex[:8]}.jpg")
                Image.fromarray(clean_arr).save(out, format="JPEG", quality=85)
                return out
        except Exception:
            return img_path

class MultiMailWorker(threading.Thread):
    def __init__(self, check_interval=20):
        super().__init__()
        self.interval = check_interval
        self.daemon = True
        self.is_running = True

    def run(self):
        while self.is_running:
            try:
                self.process_mail()
            except Exception:
                pass
            time.sleep(self.interval)

    def wait_for_job_completed(self, job_id, timeout=120):
        """轮询监控 CUPS 打印队列，直到该任务真正出纸完成"""
        if not job_id:
            return True
        start_time = time.time()
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"

        while time.time() - start_time < timeout:
            res = subprocess.run(["lpstat", "-W", "completed"], stdout=subprocess.PIPE, text=True, env=env)
            # 如果在已完成列表中发现了 job_id，证明出纸成功
            if job_id in res.stdout:
                return True
            # 如果当前活跃任务列表中也没有它了，也说明出纸完成
            res_active = subprocess.run(["lpstat", "-o"], stdout=subprocess.PIPE, text=True, env=env)
            if job_id not in res_active.stdout:
                return True
            time.sleep(2.5)
        return False

    def process_mail(self):
        cfg_file = "/opt/cups_data/mail_config.json"
        if not os.path.exists(cfg_file):
            return

        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        if not cfg.get("enable"):
            return

        server = cfg.get("server")
        port = int(cfg.get("port", 993))
        user = cfg.get("user")
        pwd = cfg.get("password")
        printer = cfg.get("default_printer", "")
        push_token = cfg.get("pushplus_token", "").strip()

        sec_keyword = cfg.get("keyword", "").strip()
        whitelist = [s.strip().lower() for s in cfg.get("whitelist", "").split(",") if s.strip()]

        mail = imaplib.IMAP4_SSL(server, port)
        mail.login(user, pwd)
        mail.select("INBOX")

        status, data = mail.search(None, "UNSEEN")
        if status != "OK" or not data[0]:
            mail.logout()
            return

        for num in data[0].split():
            res, msg_data = mail.fetch(num, "(RFC822)")
            if res != "OK":
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            from_addr = self._decode_header(msg.get("From", "")).lower()
            if whitelist and not any(w in from_addr for w in whitelist):
                continue

            subject = self._decode_header(msg.get("Subject", ""))
            if sec_keyword and sec_keyword not in subject:
                continue

            need_enhance = any(k in subject for k in ["去底", "去黑", "纠偏", "试卷", "清晰"]) and ("原图" not in subject)

            for part in msg.walk():
                if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                    continue

                fname = part.get_filename()
                if not fname:
                    continue

                fname = self._decode_header(fname)
                ext = os.path.splitext(fname)[-1].lower()

                if ext in [".jpg", ".jpeg", ".png", ".pdf"]:
                    file_path = os.path.join(MAIL_TASK_DIR, f"{uuid.uuid4().hex[:8]}_{fname}")
                    with open(file_path, "wb") as f_out:
                        f_out.write(part.get_payload(decode=True))

                    ready_file = file_path
                    if ext in [".jpg", ".jpeg", ".png"] and need_enhance:
                        ready_file = ImageEnhancer.deskew_and_clean(file_path)

                    cmd = ["lp"]
                    if printer:
                        cmd.extend(["-d", printer])
                    cmd.extend(["-o", "fit-to-page", ready_file])

                    env = os.environ.copy()
                    env["CUPS_SERVER"] = "/run/cups/cups.sock"
                    res_lp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                    
                    if res_lp.returncode == 0:
                        # 提取 Job ID，如 "HP_LaserJet-25"
                        job_line = res_lp.stdout.strip()
                        job_id = job_line.split(" ")[-1] if "request id is" in job_line else ""

                        # 等待打印机真正出纸完成
                        is_finished = self.wait_for_job_completed(job_id, timeout=180)
                        if is_finished:
                            PushPlusNotifier.send(
                                push_token,
                                "🖨️ 云邮件打印已成功出纸！",
                                f"<b>任务状态：</b>已物理出纸完成<br><b>文件名称：</b>{fname}<br><b>打印机：</b>{printer or '默认'}<br><b>发件人：</b>{from_addr}"
                            )
                        else:
                            PushPlusNotifier.send(
                                push_token,
                                "⚠️ 打印任务超时或可能缺纸卡纸",
                                f"任务已发送但长时间未检测到出纸：{fname}，请检查打印机状态。"
                            )

        mail.logout()

    def _decode_header(self, text):
        if not text:
            return ""
        decoded, encoding = decode_header(text)[0]
        if isinstance(decoded, bytes):
            return decoded.decode(encoding or "utf-8", errors="ignore")
        return str(decoded)

class MailConfigHandler(BaseHandler):
    def get(self):
        cfg_file = "/opt/cups_data/mail_config.json"
        if os.path.exists(cfg_file):
            with open(cfg_file, "r", encoding="utf-8") as f:
                self.write_json(True, data=json.load(f))
        else:
            self.write_json(True, data={"enable": False})

    def post(self):
        try:
            body = json.loads(self.request.body.decode("utf-8"))
            cfg_file = "/opt/cups_data/mail_config.json"
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
            self.write_json(True, "云邮箱及 PushPlus 策略已持久化保存")
        except Exception as e:
            self.write_json(False, f"保存失败: {str(e)}")

mail_thread = MultiMailWorker()
mail_thread.start()
EOF

docker cp /tmp/mail_handler.py cups:/opt/webapp/handlers/mail_handler.py
