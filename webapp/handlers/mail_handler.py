#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import email
import imaplib
import uuid
import threading
import subprocess
import numpy as np
from PIL import Image
from email.header import decode_header
from handlers.base_handler import BaseHandler

MAIL_TASK_DIR = "/tmp/mail_print_tasks"
os.makedirs(MAIL_TASK_DIR, exist_ok=True)

class ImageEnhancer:
    """针对海思机顶盒的轻量级纯 NumPy/PIL 图像增强引擎"""
    @staticmethod
    def deskew_and_clean(img_path):
        try:
            with Image.open(img_path) as src:
                img = src.convert("L")
                if max(img.size) > 2000:
                    img.thumbnail((2000, 2000), Image.Resampling.BILINEAR)
                arr = np.array(img, dtype=np.uint8)

                # 快速水平投影方差法算倾斜角度
                angle = ImageEnhancer._detect_skew_angle(arr)
                if abs(angle) >= 0.5:
                    img = img.rotate(angle, expand=True, fillcolor=255)
                    arr = np.array(img, dtype=np.uint8)

                # 动态阴影消除与背景漂白
                farr = arr.astype(np.float32)
                clean_arr = np.clip((farr - 55.0) * (255.0 / (195.0 - 55.0)), 0, 255).astype(np.uint8)

                out_path = os.path.join(MAIL_TASK_DIR, f"mail_clean_{uuid.uuid4().hex[:8]}.jpg")
                Image.fromarray(clean_arr).save(out_path, format="JPEG", quality=85)
                return out_path
        except Exception as e:
            print(f"[MailEnhance] 增强处理跳过: {e}")
            return img_path

    @staticmethod
    def _detect_skew_angle(arr):
        try:
            small = arr[::4, ::4]
            bin_arr = (small < 180).astype(np.uint8)
            best_angle = 0.0
            max_variance = -1.0
            for ang in range(-12, 13, 2):
                rotated = Image.fromarray(bin_arr).rotate(ang, resample=Image.Resampling.NEAREST, fillcolor=0)
                proj = np.sum(np.array(rotated), axis=1)
                variance = np.var(proj)
                if variance > max_variance:
                    max_variance = variance
                    best_angle = ang
            return best_angle
        except Exception:
            return 0.0

class MultiMailWorker(threading.Thread):
    def __init__(self, check_interval=30):
        super().__init__()
        self.interval = check_interval
        self.daemon = True
        self.is_running = True

    def run(self):
        print(">>> [MailWorker] 邮件自动抓取打印守护线程已就绪...")
        while self.is_running:
            try:
                self.process_mail_tasks()
            except Exception:
                pass
            time.sleep(self.interval)

    def process_mail_tasks(self):
        config_path = "/opt/cups_data/mail_config.json"
        if not os.path.exists(config_path):
            return

        import json
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        if not cfg.get("enable"):
            return

        server = cfg.get("server")
        port = int(cfg.get("port", 993))
        user = cfg.get("user")
        pwd = cfg.get("password")
        printer = cfg.get("default_printer", "")

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

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            subject = self._decode_header(msg["Subject"])
            need_enhance = any(k in subject for k in ["拍照", "试卷", "去底", "纠偏", "清晰"])

            for part in msg.walk():
                if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                    continue

                filename = part.get_filename()
                if not filename:
                    continue

                filename = self._decode_header(filename)
                ext = os.path.splitext(filename)[-1].lower()

                if ext in [".jpg", ".jpeg", ".png", ".pdf"]:
                    file_path = os.path.join(MAIL_TASK_DIR, f"{uuid.uuid4().hex[:8]}_{filename}")
                    with open(file_path, "wb") as f_out:
                        f_out.write(part.get_payload(decode=True))

                    ready_file = file_path
                    if ext in [".jpg", ".jpeg", ".png"] and need_enhance:
                        ready_file = ImageEnhancer.deskew_and_clean(file_path)

                    cmd = ["lp"]
                    if printer:
                        cmd.extend(["-d", printer])
                    cmd.extend(["-o", "fit-to-page", ready_file])
                    subprocess.run(cmd, timeout=30)

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
        config_path = "/opt/cups_data/mail_config.json"
        if os.path.exists(config_path):
            import json
            with open(config_path, "r", encoding="utf-8") as f:
                self.write_json(True, data=json.load(f))
        else:
            self.write_json(True, data={"enable": False})

    def post(self):
        try:
            import json
            body = json.loads(self.request.body.decode("utf-8"))
            config_path = "/opt/cups_data/mail_config.json"
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
            self.write_json(True, "邮件配置已持久化保存")
        except Exception as e:
            self.write_json(False, f"保存失败: {str(e)}")

# 启动邮件轮询常驻线程
mail_thread = MultiMailWorker()
mail_thread.start()
