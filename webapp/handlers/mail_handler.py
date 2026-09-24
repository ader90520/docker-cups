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

def detect_skew_angle(gray_img):
    try:
        w, h = gray_img.size
        scale = 320.0 / max(w, h)
        small = gray_img.resize((int(w * scale), int(h * scale)), Image.Resampling.NEAREST)
        arr = np.array(small, dtype=np.float32)

        grad = np.abs(arr[2:, :] - arr[:-2, :])
        grad[grad < 30] = 0.0

        best_angle = 0.0
        max_var = 0.0
        for angle in np.arange(-4.0, 4.5, 0.5):
            rot = Image.fromarray(grad).rotate(angle, resample=Image.Resampling.NEAREST)
            proj = np.sum(np.array(rot), axis=1)
            var = np.var(proj)
            if var > max_var:
                max_var = var
                best_angle = angle
        return best_angle
    except Exception:
        return 0.0

def process_camscanner_a4(input_path, output_path):
    try:
        with Image.open(input_path) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            if img.width > img.height:
                img = img.rotate(270, expand=True)

            gray_deskew = img.convert("L")
            angle = detect_skew_angle(gray_deskew)
            if abs(angle) > 0.15:
                img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(255, 255, 255))

            w, h = img.size
            cx, cy = int(w * 0.035), int(h * 0.035)
            img = img.crop((cx, cy, w - cx, h - cy))

            if max(img.size) > 2400:
                img.thumbnail((2400, 2400), Image.Resampling.BILINEAR)

            rgb = img.convert("RGB")
            arr = np.array(rgb, dtype=np.float32)

            bg = rgb.filter(ImageFilter.GaussianBlur(radius=50))
            bg_arr = np.array(bg, dtype=np.float32) + 1e-4

            divided = (arr / bg_arr) * 255.0

            r, g, b = divided[:, :, 0], divided[:, :, 1], divided[:, :, 2]
            max_c = np.maximum(np.maximum(r, g), b)
            min_c = np.minimum(np.minimum(r, g), b)
            chroma = max_c - min_c

            is_red = (r > (g + 12.0)) & (r > (b + 12.0)) & (chroma > 15.0)
            lum = 0.299 * r + 0.587 * g + 0.114 * b

            effective_lum = np.where(is_red, lum * 0.50, lum)
            effective_lum = np.where(~is_red & (chroma > 15.0), effective_lum * 0.75, effective_lum)

            lum_pil = Image.fromarray(effective_lum.astype(np.uint8))
            min_filtered = lum_pil.filter(ImageFilter.MinFilter(size=3))
            min_arr = np.array(min_filtered, dtype=np.float32)

            line_detail = np.maximum(0.0, effective_lum - min_arr)
            enhanced_lum = effective_lum - line_detail * 0.55

            boosted = np.clip((enhanced_lum - 45.0) * (255.0 / (220.0 - 45.0)), 0, 255)
            boosted = (boosted / 255.0) ** 1.25 * 255.0

            boosted[boosted > 216] = 255
            boosted[boosted < 125] = boosted[boosted < 125] * 0.40

            clean_img = Image.fromarray(boosted.astype(np.uint8))
            sharp_img = clean_img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=180, threshold=2))

            a4_w, a4_h = 2480, 3508
            canvas = Image.new("L", (a4_w, a4_h), 255)
            margin = 50
            target_w, target_h = a4_w - margin * 2, a4_h - margin * 2

            ratio = min(target_w / sharp_img.width, target_h / sharp_img.height)
            new_w, new_h = int(sharp_img.width * ratio), int(sharp_img.height * ratio)

            resized = sharp_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas.paste(resized, ((a4_w - new_w) // 2, (a4_h - new_h) // 2))

            canvas.save(output_path, format="JPEG", quality=95)
            return True
    except Exception as e:
        print(f"[MailCamScanner] 异常: {e}")
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
    def __init__(self, check_interval=12):
        super().__init__()
        self.interval = check_interval
        self.daemon = True
        self.is_running = True

    def run(self):
        print(f"[MailWorker] ✔ 云邮件实时监听守护线程已上线 (轮询周期: {self.interval}s)")
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

        print(f"[MailWorker] 正在跟踪物理出纸，JobID: {job_id} ...")
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
                    f"<b>故障原因：</b>{' | '.join(alerts)}<br><b>待打印文件：</b>{fname}<br><b>打印机：</b>{printer or '默认'}<br>请及时加纸或排除卡纸。"
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
                print(f"[MailWorker] ✔ 任务 {job_id} 物理出纸确认完毕，微信通知已成功推送！")
                return

            time.sleep(2)

        PushPlusNotifier.send(push_token, "⏱️ 云打印超时未出纸", f"<b>文件：</b>{fname}<br>排队超 3 分钟未完成，请检查打印机状态。")

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

        if printer:
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            subprocess.run(["cupsenable", printer], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["cupsaccept", printer], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        sec_keyword = cfg.get("keyword", "").strip()
        whitelist = [s.strip().lower() for s in cfg.get("whitelist", "").split(",") if s.strip()]

        mail = None
        try:
            mail = imaplib.IMAP4_SSL(server, port, timeout=12)
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
                from_addr = self.decode_field(msg.get("From", ""))
                subject = self.decode_field(msg.get("Subject", ""))

                print(f"[MailWorker] 正在检查邮件 -> 来自: [{from_addr}] | 主题: [{subject}]")

                real_sender = from_addr.lower()
                if "<" in real_sender and ">" in real_sender:
                    real_sender = real_sender.split("<")[1].split(">")[0].strip()

                if whitelist and not any(w in real_sender for w in whitelist):
                    print(f"[MailWorker] ❌ 发件人 [{real_sender}] 不在白名单中，跳过打印！")
                    mail.store(num, "+FLAGS", "\\Seen")
                    continue

                if sec_keyword and (sec_keyword not in subject):
                    print(f"[MailWorker] ❌ 主题 [{subject}] 未包含预设暗号 [{sec_keyword}]，跳过打印！")
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
                        token = uuid.uuid4().hex[:8]
                        raw_save_path = os.path.join(MAIL_TASK_DIR, f"{token}_{fname}")
                        with open(raw_save_path, "wb") as f_out:
                            f_out.write(part.get_payload(decode=True))

                        ready_file = raw_save_path
                        print(f"[MailWorker] 提取到附件: {fname} (大小: {os.path.getsize(raw_save_path)} 字节)")

                        if ext in [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"] and need_enhance:
                            conv_path = os.path.join(MAIL_TASK_DIR, f"cam_{token}.jpg")
                            print(f"[MailWorker] 正在执行全能王 v5 细节保全线稿锐化算法...")
                            if process_camscanner_a4(raw_save_path, conv_path):
                                ready_file = conv_path
                                print(f"[MailWorker] ✔ 图像处理完成，准备交由 CUPS 打印")

                        cmd = ["lp"]
                        if printer:
                            cmd.extend(["-d", printer])
                        cmd.extend(["-o", "media=A4", "-o", "fit-to-page", ready_file])

                        env = os.environ.copy()
                        env["CUPS_SERVER"] = "/run/cups/cups.sock"
                        print(f"[MailWorker] 🚀 正在派发命令至打印机 [{printer or '默认'}]...")
                        res_lp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

                        if res_lp.returncode == 0:
                            job_line = res_lp.stdout.strip()
                            print(f"[MailWorker] ✔ CUPS 任务接收成功: {job_line}")
                            job_id = job_line.split(" ")[-1] if "request id is" in job_line else ""
                            printed_count += 1
                            self.track_job_until_output(job_id, printer, fname, push_token, from_addr)
                        else:
                            err_msg = res_lp.stderr.strip()
                            print(f"[MailWorker] ❌ CUPS 拒绝接收: {err_msg}")
                            PushPlusNotifier.send(push_token, "❌ 邮件打印被拒绝", f"文件：{fname}<br>错误：{err_msg}")

                if printed_count == 0:
                    print(f"[MailWorker] ⚠️ 该邮件内未发现有效的图片或 PDF 附件！")

                mail.store(num, "+FLAGS", "\\Seen")

        except Exception as e:
            print(f"[MailWorker] 处理邮件循环异常: {e}")
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
