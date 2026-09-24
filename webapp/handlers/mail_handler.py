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
from PIL import Image, ImageOps, ImageFilter
from email.header import decode_header
from handlers.base_handler import BaseHandler

MAIL_TASK_DIR = "/tmp/mail_print_tasks"
os.makedirs(MAIL_TASK_DIR, exist_ok=True)

def process_camscanner_a4(input_path, output_path):
    try:
        with Image.open(input_path) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            if img.width > img.height:
                img = img.rotate(270, expand=True)

            gray = img.convert("L")
            if max(gray.size) > 2200:
                gray.thumbnail((2200, 2200), Image.Resampling.BILINEAR)

            bg = gray.filter(ImageFilter.GaussianBlur(radius=25))
            orig_arr = np.array(gray, dtype=np.float32)
            bg_arr = np.array(bg, dtype=np.float32) + 1e-5

            divided = (orig_arr / bg_arr) * 255.0
            divided = np.clip((divided - 50.0) * (255.0 / (205.0 - 50.0)), 0, 255)
            clean_arr = divided.astype(np.uint8)
            clean_arr[clean_arr > 215] = 255

            whitened_img = Image.fromarray(clean_arr)
            a4_w, a4_h = 2480, 3508
            canvas = Image.new("L", (a4_w, a4_h), 255)

            margin = 80
            target_w, target_h = a4_w - margin * 2, a4_h - margin * 2
            ratio = min(target_w / whitened_img.width, target_h / whitened_img.height)
            new_w, new_h = int(whitened_img.width * ratio), int(whitened_img.height * ratio)

            resized = whitened_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))
            canvas.convert("RGB").save(output_path, format="JPEG", quality=92)
            return True
    except Exception as e:
        print(f"[MailCamScanner] 算法异常: {e}")
        return False

class PushPlusNotifier:
    @staticmethod
    def send(token, title, content):
        if not token:
            return
        try:
            url = "https://www.pushplus.plus/send"
            payload = json.dumps({
                "token": token.strip(),
                "title": title,
                "content": content,
                "template": "html"
            }).encode("utf-8")
            req = urllib.request.Request(
                url, 
                data=payload, 
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"[PushPlus] 推送返回: {resp.read().decode('utf-8')}")
        except Exception as e:
            print(f"[PushPlus] 推送异常: {e}")

class MultiMailWorker(threading.Thread):
    def __init__(self, check_interval=15):
        super().__init__()
        self.interval = check_interval
        self.daemon = True
        self.is_running = True

    def run(self):
        print(f"[MailWorker] ✔ 云邮件物理出纸守护线程已上线 (轮询: {self.interval}s)")
        while self.is_running:
            try:
                self.process_mail()
            except Exception as e:
                print(f"[MailWorker] 周期异常: {e}")
            time.sleep(self.interval)

    def check_printer_hardware_alerts(self, printer):
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        alerts = []
        try:
            cmd = ["lpstat", "-p", printer, "-l"] if printer else ["lpstat", "-p", "-l"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, timeout=2, env=env)
            out = res.stdout.lower()

            if "media-empty" in out or "out-of-paper" in out or "empty" in out:
                alerts.append("⚠️ 打印机缺纸 (纸盒已空)")
            if "media-jam" in out or "jam" in out:
                alerts.append("🚨 打印机卡纸")
            if "toner-low" in out or "marker-supply-low" in out or "toner-empty" in out:
                alerts.append("⚠️ 墨粉将尽")
            if "offline" in out or "not connected" in out or "paused" in out:
                alerts.append("🔌 打印机脱机或暂停")
        except Exception:
            pass
        return alerts

    def track_job_until_output(self, job_id, printer, fname, push_token, from_addr, timeout=180):
        if not job_id:
            time.sleep(4)
            PushPlusNotifier.send(push_token, "🖨️ 云邮件打印已下发", f"<b>文件：</b>{fname}<br>任务已送往打印机队列。")
            return

        print(f"[MailWorker] 跟踪出纸 JobID: {job_id} ...")
        start_time = time.time()
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        has_alerted_error = False

        while time.time() - start_time < timeout:
            alerts = self.check_printer_hardware_alerts(printer)
            if alerts and not has_alerted_error:
                PushPlusNotifier.send(
                    push_token,
                    "🚨 打印中断：打印机发生硬件故障！",
                    f"<b>故障原因：</b>{' | '.join(alerts)}<br><b>待打印文件：</b>{fname}<br><b>打印机：</b>{printer or '默认'}<br>请及时加纸或清卡纸。"
                )
                has_alerted_error = True

            res_comp = subprocess.run(["lpstat", "-W", "completed"], stdout=subprocess.PIPE, text=True, env=env)
            is_in_completed = job_id in res_comp.stdout

            res_active = subprocess.run(["lpstat", "-o"], stdout=subprocess.PIPE, text=True, env=env)
            is_still_active = job_id in res_active.stdout

            if (is_in_completed or not is_still_active) and not alerts:
                time.sleep(2)
                PushPlusNotifier.send(
                    push_token,
                    "🎉 云邮件打印出纸成功！",
                    f"<b>状态：</b>已物理出纸完成<br><b>文件：</b>{fname}<br><b>设备：</b>{printer or '默认'}<br><b>发件人：</b>{from_addr}<br><b>时间：</b>{time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                print(f"[MailWorker] ✔ 出纸完成，推送成功！")
                return

            time.sleep(2)

        PushPlusNotifier.send(push_token, "⏱️ 云打印超时未出纸", f"<b>文件：</b>{fname}<br>排队超 3 分钟未完成，请检查打印机。")

    def get_fallback_printer(self):
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"
        try:
            res = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, text=True, timeout=2, env=env)
            for line in res.stdout.splitlines():
                if "destination:" in line:
                    return line.split("destination:")[-1].strip()
            res_a = subprocess.run(["lpstat", "-a"], stdout=subprocess.PIPE, text=True, timeout=2, env=env)
            for line in res_a.stdout.splitlines():
                if line.strip():
                    return line.split()[0]
        except Exception:
            pass
        return ""

    def decode_field(self, header_val):
        if not header_val:
            return ""
        result = []
        try:
            for part, enc in decode_header(header_val):
                if isinstance(part, bytes):
                    result.append(part.decode(enc or "utf-8", errors="ignore"))
                else:
                    result.append(str(part))
        except Exception:
            return str(header_val)
        return "".join(result).strip()

    def process_mail(self):
        cfg_file = "/opt/cups_data/mail_config.json"
        if not os.path.exists(cfg_file):
            return

        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        if not cfg.get("enable"):
            return

        server = cfg.get("server", "").strip()
        port = int(cfg.get("port", 993))
        user = cfg.get("user", "").strip()
        pwd = cfg.get("password", "").strip()
        printer = cfg.get("default_printer", "").strip()
        push_token = cfg.get("pushplus_token", "").strip()

        if not server or not user or not pwd:
            return

        if not printer:
            printer = self.get_fallback_printer()

        sec_keyword = cfg.get("keyword", "").strip()
        whitelist = [s.strip().lower() for s in cfg.get("whitelist", "").split(",") if s.strip()]

        mail = None
        try:
            mail = imaplib.IMAP4_SSL(server, port, timeout=15)
            mail.login(user, pwd)
            mail.select("INBOX")
        except Exception as e:
            print(f"[MailWorker] 邮箱登录失败 ({user}): {e}")
            if mail:
                try: mail.logout()
                except: pass
            return

        try:
            status, data = mail.search(None, "UNSEEN")
            if status != "OK" or not data[0]:
                mail.logout()
                return

            msg_ids = data[0].split()
            print(f"[MailWorker] 📩 发现 {len(msg_ids)} 封未读邮件，开始解析...")

            for num in msg_ids:
                res, msg_data = mail.fetch(num, "(RFC822)")
                if res != "OK":
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                from_addr = self.decode_field(msg.get("From", "")).lower()
                subject = self.decode_field(msg.get("Subject", ""))

                print(f"[MailWorker] 解析邮件 -> 发件人: {from_addr} | 主题: {subject}")

                if whitelist and not any(w in from_addr for w in whitelist):
                    print(f"[MailWorker] ❌ 发件人不在白名单，跳过: {from_addr}")
                    continue

                if sec_keyword and (sec_keyword not in subject):
                    print(f"[MailWorker] ❌ 主题缺少暗号 [{sec_keyword}]，跳过")
                    continue

                need_enhance = ("原图" not in subject)

                for part in msg.walk():
                    if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                        continue

                    fname = part.get_filename()
                    if not fname:
                        continue

                    fname = self.decode_field(fname)
                    ext = os.path.splitext(fname)[-1].lower()

                    if ext in [".jpg", ".jpeg", ".png", ".pdf", ".bmp", ".tif", ".tiff"]:
                        token = uuid.uuid4().hex[:8]
                        raw_save_path = os.path.join(MAIL_TASK_DIR, f"{token}_{fname}")
                        with open(raw_save_path, "wb") as f_out:
                            f_out.write(part.get_payload(decode=True))

                        ready_file = raw_save_path

                        if ext in [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"] and need_enhance:
                            conv_path = os.path.join(MAIL_TASK_DIR, f"cam_{token}.jpg")
                            if process_camscanner_a4(raw_save_path, conv_path):
                                ready_file = conv_path

                        cmd = ["lp"]
                        if printer:
                            cmd.extend(["-d", printer])
                        cmd.extend(["-o", "fit-to-page", ready_file])

                        env = os.environ.copy()
                        env["CUPS_SERVER"] = "/run/cups/cups.sock"
                        print(f"[MailWorker] 🚀 正在派发打印: {fname} 到 [{printer or '默认'}]")
                        res_lp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

                        if res_lp.returncode == 0:
                            job_line = res_lp.stdout.strip()
                            print(f"[MailWorker] ✔ CUPS 任务接收成功: {job_line}")
                            job_id = job_line.split(" ")[-1] if "request id is" in job_line else ""
                            self.track_job_until_output(job_id, printer, fname, push_token, from_addr)
                        else:
                            err_msg = res_lp.stderr.strip()
                            print(f"[MailWorker] ❌ CUPS 拒绝: {err_msg}")
                            PushPlusNotifier.send(push_token, "❌ 邮件打印被拒绝", f"文件：{fname}<br>原因：{err_msg}")

                mail.store(num, "+FLAGS", "\\Seen")

        except Exception as e:
            print(f"[MailWorker] 处理异常: {e}")
        finally:
            try:
                mail.logout()
            except Exception:
                pass

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
            print(f"[MailConfigHandler] 邮箱策略更新成功: 启用={body.get('enable')}")
            self.write_json(True, "云邮箱及微信通知策略已保存生效")
        except Exception as e:
            self.write_json(False, f"保存失败: {str(e)}")

mail_thread = MultiMailWorker()
mail_thread.start()
