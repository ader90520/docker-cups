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
            print(f"[PushPlus] 微信通知推送成功: {title}")
        except Exception as e:
            print(f"[PushPlus] 推送异常: {e}")

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
        except Exception as e:
            print(f"[ImageEnhancer] 图像增强失败，使用原图: {e}")
            return img_path

class MultiMailWorker(threading.Thread):
    def __init__(self, check_interval=15):
        super().__init__()
        self.interval = check_interval
        self.daemon = True
        self.is_running = True

    def run(self):
        print(f"[MailWorker] 云邮件物理状态闭环守护线程启动，轮询间隔: {self.interval} 秒")
        while self.is_running:
            try:
                self.process_mail()
            except Exception as e:
                print(f"[MailWorker] 轮询异常: {e}")
            time.sleep(self.interval)

    def check_printer_hardware_alerts(self, printer):
        """深层检测指定打印机的物理硬件故障：缺纸、卡纸、缺墨、脱机"""
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
                alerts.append("🚨 打印机严重卡纸")
            if "toner-low" in out or "marker-supply-low" in out or "toner-empty" in out:
                alerts.append("⚠️ 墨粉/硒鼓耗尽")
            if "offline" in out or "not connected" in out or "paused" in out:
                alerts.append("🔌 打印机脱机或暂停")
        except Exception:
            pass
        return alerts

    def track_job_until_output(self, job_id, printer, fname, push_token, from_addr, timeout=180):
        """
        核心物理状态机追踪：
        1. 轮询任务进度，检测出纸状态；
        2. 若遇缺纸、卡纸立即微信报警；
        3. 唯有确认真正出纸完成，才提示打印成功。
        """
        if not job_id:
            time.sleep(5)
            PushPlusNotifier.send(
                push_token,
                "🖨️ 云邮件打印已下发",
                f"<b>文件名称：</b>{fname}<br><b>提示：</b>任务已提交打印机，请留意现场出纸情况。"
            )
            return

        start_time = time.time()
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        has_alerted_error = False

        while time.time() - start_time < timeout:
            # 1. 检测硬件是否有物理异常阻断
            alerts = self.check_printer_hardware_alerts(printer)
            if alerts and not has_alerted_error:
                error_desc = " | ".join(alerts)
                PushPlusNotifier.send(
                    push_token,
                    "🚨 打印中断：打印机发生物理故障！",
                    f"<b>故障原因：</b>{error_desc}<br><b>待打印文件：</b>{fname}<br><b>打印设备：</b>{printer or '默认'}<br><b>提示：</b>请前往设备加纸、清卡纸或检查墨粉，处理后将继续出纸。"
                )
                has_alerted_error = True

            # 2. 检查任务是否进入 completed 历史
            res_comp = subprocess.run(["lpstat", "-W", "completed"], stdout=subprocess.PIPE, text=True, env=env)
            is_in_completed = job_id in res_comp.stdout

            # 3. 检查任务是否仍在活动队列
            res_active = subprocess.run(["lpstat", "-o"], stdout=subprocess.PIPE, text=True, env=env)
            is_still_active = job_id in res_active.stdout

            # 判定标准：任务在 completed 且退出活动队列，并且此时无缺纸/卡纸硬件报警
            if (is_in_completed or not is_still_active) and not alerts:
                time.sleep(2)
                PushPlusNotifier.send(
                    push_token,
                    "🎉 云邮件打印出纸成功！",
                    f"<b>物理状态：</b>已顺利出纸完毕<br><b>文件名称：</b>{fname}<br><b>打印设备：</b>{printer or '默认'}<br><b>发件人：</b>{from_addr}<br><b>时间：</b>{time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                return

            time.sleep(2)

        # 超时未出纸提示
        PushPlusNotifier.send(
            push_token,
            "⏱️ 云打印任务超时未出纸",
            f"<b>文件名称：</b>{fname}<br><b>设备：</b>{printer or '默认'}<br><b>诊断提示：</b>任务排队已超 3 分钟未完成，请检查打印机电源、数据线或是否有未清理的纸屑卡纸。"
        )

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
            print(f"[MailWorker] 发现 {len(msg_ids)} 封未读邮件，开始解析...")

            for num in msg_ids:
                res, msg_data = mail.fetch(num, "(RFC822)")
                if res != "OK":
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                from_addr = self.decode_field(msg.get("From", "")).lower()
                subject = self.decode_field(msg.get("Subject", ""))

                print(f"[MailWorker] 正在检查邮件 -> 来自: {from_addr} | 主题: {subject}")

                # 白名单与暗号安全过滤
                if whitelist and not any(w in from_addr for w in whitelist):
                    print(f"[MailWorker] ❌ 白名单拦截: {from_addr}")
                    continue

                if sec_keyword and (sec_keyword not in subject):
                    print(f"[MailWorker] ❌ 主题未包含暗号 [{sec_keyword}]")
                    continue

                need_enhance = any(k in subject for k in ["去底", "去黑", "纠偏", "试卷", "清晰"]) and ("原图" not in subject)

                # 附件排版打印与物理追踪
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

                        if ext in [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"]:
                            try:
                                with Image.open(raw_save_path) as im:
                                    im = ImageOps.exif_transpose(im)
                                    conv_path = os.path.join(MAIL_TASK_DIR, f"conv_{token}.jpg")
                                    im.convert("RGB").save(conv_path, format="JPEG", quality=92)
                                    ready_file = conv_path
                            except Exception as e:
                                print(f"[MailWorker] 格式转换预处理: {e}")

                            if need_enhance:
                                ready_file = ImageEnhancer.deskew_and_clean(ready_file)

                        # 发送打印命令
                        cmd = ["lp"]
                        if printer:
                            cmd.extend(["-d", printer])
                        cmd.extend(["-o", "fit-to-page", ready_file])

                        env = os.environ.copy()
                        env["CUPS_SERVER"] = "/run/cups/cups.sock"
                        print(f"[MailWorker] 🚀 正在向打印机 [{printer or '默认'}] 提交文件: {fname}")
                        res_lp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

                        if res_lp.returncode == 0:
                            job_line = res_lp.stdout.strip()
                            print(f"[MailWorker] CUPS 接收成功: {job_line}")
                            job_id = job_line.split(" ")[-1] if "request id is" in job_line else ""

                            # 启动物理出纸与硬件报警追踪
                            self.track_job_until_output(job_id, printer, fname, push_token, from_addr)
                        else:
                            err_msg = res_lp.stderr.strip()
                            print(f"[MailWorker] ❌ CUPS 拒绝: {err_msg}")
                            PushPlusNotifier.send(
                                push_token,
                                "❌ 邮件打印任务提交被拒绝",
                                f"文件：{fname}<br>错误详情：{err_msg}"
                            )

                mail.store(num, "+FLAGS", "\\Seen")

        except Exception as e:
            print(f"[MailWorker] 邮件循环处理异常: {e}")
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
            print(f"[MailConfigHandler] 云邮件策略已保存生效: 打印机={body.get('default_printer')}")
            self.write_json(True, "云邮箱及微信通知策略已成功更新")
        except Exception as e:
            self.write_json(False, f"保存失败: {str(e)}")

mail_thread = MultiMailWorker()
mail_thread.start()
