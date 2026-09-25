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

def fast_detect_skew(gray_img):
    """微采样水平倾斜角度估计（耗时 < 0.05s）"""
    try:
        w, h = gray_img.size
        scale = 160.0 / max(w, h)
        small = gray_img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.NEAREST)
        arr = np.array(small, dtype=np.float32)

        grad = np.abs(arr[2:, :] - arr[:-2, :])
        grad[grad < 35] = 0.0

        best_angle = 0.0
        max_var = 0.0
        for angle in [-2.0, 0.0, 2.0]:
            rot = Image.fromarray(grad).rotate(angle, resample=Image.Resampling.NEAREST)
            proj = np.sum(np.array(rot), axis=1)
            var = np.var(proj)
            if var > max_var:
                max_var = var
                best_angle = angle
        return best_angle
    except Exception:
        return 0.0

def process_camscanner_color_stream(input_path, output_path):
    """
    全能王真彩色保留去底引擎 (200 DPI RGB 流式输出):
    1. EXIF 方向修正与水平微纠偏
    2. 裁剪 3% 暗黑外边沿
    3. RGB 三通道分别做局部背景除法，纸张底色 100% 漂白为 (255, 255, 255)
    4. 完美保留红章、红线、彩图、蓝黑墨水笔迹原生色彩
    5. 居中排版至 200 DPI A4 画布，连续流畅吐纸
    """
    try:
        with Image.open(input_path) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            if img.width > img.height:
                img = img.rotate(270, expand=True)

            gray_small = img.convert("L")
            angle = fast_detect_skew(gray_small)
            if abs(angle) >= 1.0:
                img = img.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=(255, 255, 255))

            w, h = img.size
            cx, cy = int(w * 0.03), int(h * 0.03)
            img = img.crop((cx, cy, w - cx, h - cy))

            if max(img.size) > 1600:
                img.thumbnail((1600, 1600), Image.Resampling.BILINEAR)

            rgb = img.convert("RGB")
            channels = [np.array(c, dtype=np.float32) for c in rgb.split()]
            cleaned_channels = []

            for c_arr in channels:
                c_pil = Image.fromarray(np.clip(c_arr, 0, 255).astype(np.uint8))
                bg = c_pil.filter(ImageFilter.BoxBlur(radius=25))
                bg_arr = np.array(bg, dtype=np.float32) + 1.0

                divided = (c_arr / bg_arr) * 255.0

                out = np.zeros_like(divided)
                out[divided >= 195] = 255.0

                mask_ink = divided < 195
                ink_val = np.clip((divided[mask_ink] - 40.0) * (205.0 / (195.0 - 40.0)), 0, 255)
                ink_val = (ink_val / 205.0) ** 1.25 * 190.0
                out[mask_ink] = ink_val
                cleaned_channels.append(np.clip(out, 0, 255).astype(np.uint8))

            clean_rgb = Image.merge("RGB", [Image.fromarray(c) for c in cleaned_channels])
            sharp_rgb = clean_rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=2))

            a4_w, a4_h = 1654, 2338
            canvas = Image.new("RGB", (a4_w, a4_h), (255, 255, 255))
            margin = 35
            target_w, target_h = a4_w - margin * 2, a4_h - margin * 2

            ratio = min(target_w / sharp_rgb.width, target_h / sharp_rgb.height)
            new_w, new_h = int(sharp_rgb.width * ratio), int(sharp_rgb.height * ratio)

            resized = sharp_rgb.resize((new_w, new_h), Image.Resampling.BILINEAR)
            canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))

            canvas.save(output_path, format="JPEG", quality=90)
            return True
    except Exception as e:
        print(f"[CamScannerColor] 处理异常: {e}")
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
            with urllib.request.urlopen(req, timeout=6) as resp:
                print(f"[PushPlus] 推送返回: {resp.read().decode('utf-8')}")
        except Exception as e:
            print(f"[PushPlus] 推送异常: {e}")

class MultiMailWorker(threading.Thread):
    def __init__(self, check_interval=5):
        super().__init__()
        self.interval = check_interval
        self.daemon = True
        self.is_running = True

    def run(self):
        print(f"[MailWorker] ✔ 极速真彩色云邮件监听守护线程已上线 (轮询周期: {self.interval}s)")
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
                alerts.append("⚠️ 打印机缺纸")
            if "media-jam" in out or "jam" in out:
                alerts.append("🚨 打印机卡纸")
            if "toner-low" in out or "marker-supply-low" in out or "toner-empty" in out:
                alerts.append("⚠️ 墨粉将尽")
            if "offline" in out or "not connected" in out:
                alerts.append("🔌 打印机脱机")
        except Exception:
            pass
        return alerts

    def track_job_until_output(self, job_id, printer, fname, push_token, from_addr, timeout=180):
        if not job_id:
            time.sleep(2)
            PushPlusNotifier.send(push_token, "🖨️ 云邮件打印已下发", f"<b>文件：</b>{fname}<br>任务已送往打印机。")
            return

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
                    "🚨 打印中断：打印机硬件异常！",
                    f"<b>故障原因：</b>{' | '.join(alerts)}<br><b>文件：</b>{fname}<br><b>打印机：</b>{printer or '默认'}"
                )
                has_alerted_error = True

            res_comp = subprocess.run(["lpstat", "-W", "completed"], stdout=subprocess.PIPE, text=True, env=env)
            is_in_completed = job_id in res_comp.stdout

            res_active = subprocess.run(["lpstat", "-o"], stdout=subprocess.PIPE, text=True, env=env)
            is_still_active = job_id in res_active.stdout

            if (is_in_completed or not is_still_active) and not alerts:
                time.sleep(1)
                PushPlusNotifier.send(
                    push_token,
                    "🎉 云邮件打印出纸成功！",
                    f"<b>状态：</b>出纸完成<br><b>文件：</b>{fname}<br><b>设备：</b>{printer or '默认'}<br><b>时间：</b>{time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                print(f"[MailWorker] ✔ 任务 {job_id} 出纸完毕，已推送微信通知！")
                return

            time.sleep(2)

        PushPlusNotifier.send(push_token, "⏱️ 云打印超时", f"<b>文件：</b>{fname}<br>超 3 分钟未出纸，请检查打印机。")

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
            mail = imaplib.IMAP4_SSL(server, port, timeout=6)
            mail.login(user, pwd)
            mail.select("INBOX")
        except Exception:
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

            for num in msg_ids:
                res, msg_data = mail.fetch(num, "(RFC822)")
                if res != "OK":
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                from_addr = self.decode_field(msg.get("From", ""))
                subject = self.decode_field(msg.get("Subject", ""))

                real_sender = from_addr.lower()
                if "<" in real_sender and ">" in real_sender:
                    real_sender = real_sender.split("<")[1].split(">")[0].strip()

                if whitelist and not any(w in real_sender for w in whitelist):
                    mail.store(num, "+FLAGS", "\\Seen")
                    continue

                if sec_keyword and (sec_keyword not in subject):
                    mail.store(num, "+FLAGS", "\\Seen")
                    continue

                need_enhance = ("原图" not in subject)
                printed_count = 0

                for part in msg.walk():
                    if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                        continue

                    fname = part.get_filename()
                    if not fname:
                        continue

                    fname = self.decode_field(fname)
                    ext = os.path.splitext(fname)[-1].lower()

                    if ext in [".jpg", ".jpeg", ".png", ".pdf", ".bmp", ".tif", ".tiff"]:
                        t0 = time.time()
                        token = uuid.uuid4().hex[:8]
                        raw_save_path = os.path.join(MAIL_TASK_DIR, f"{token}_{fname}")
                        with open(raw_save_path, "wb") as f_out:
                            f_out.write(part.get_payload(decode=True))

                        ready_file = raw_save_path
                        print(f"[MailWorker] 收到附件: {fname}")

                        if ext in [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"] and need_enhance:
                            conv_path = os.path.join(MAIL_TASK_DIR, f"cam_{token}.jpg")
                            if process_camscanner_color_stream(raw_save_path, conv_path):
                                ready_file = conv_path
                                print(f"[MailWorker] ✔ 真彩色保留去底耗时: {time.time() - t0:.2f} 秒")

                        cmd = ["lp"]
                        if printer:
                            cmd.extend(["-d", printer])
                        cmd.extend(["-o", "media=A4", "-o", "fit-to-page", ready_file])

                        env = os.environ.copy()
                        env["CUPS_SERVER"] = "/run/cups/cups.sock"
                        print(f"[MailWorker] 🚀 提交真彩色打印任务...")
                        res_lp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

                        if res_lp.returncode == 0:
                            job_line = res_lp.stdout.strip()
                            print(f"[MailWorker] ✔ CUPS 任务成功: {job_line}")
                            job_id = job_line.split(" ")[-1] if "request id is" in job_line else ""
                            printed_count += 1
                            self.track_job_until_output(job_id, printer, fname, push_token, from_addr)
                        else:
                            err_msg = res_lp.stderr.strip()
                            print(f"[MailWorker] ❌ 打印拒绝: {err_msg}")
                            PushPlusNotifier.send(push_token, "❌ 打印被拒绝", f"文件：{fname}<br>错误：{err_msg}")

                if printed_count > 0:
                    mail.store(num, "+FLAGS", "\\Deleted")
                    mail.expunge()
                    print(f"[MailWorker] 🗑️ 已从收件箱彻底删除该邮件")
                else:
                    mail.store(num, "+FLAGS", "\\Seen")

        except Exception as e:
            print(f"[MailWorker] 邮件处理异常: {e}")
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
            print(f"[MailConfigHandler] 邮箱策略更新成功")
            self.write_json(True, "云邮箱及微信通知策略已保存生效")
        except Exception as e:
            self.write_json(False, f"保存失败: {str(e)}")

mail_thread = MultiMailWorker()
mail_thread.start()
