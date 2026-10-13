#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import json
import time
import uuid
import shutil
import subprocess
import threading
import email
from email.header import decode_header
from email.utils import parseaddr
import imaplib
import urllib.request
import urllib.parse
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import process_image_for_print, clean_old_tmp_files

DATA_DIR = "/opt/webapp/data"
CONFIG_FILE = os.path.join(DATA_DIR, "mail_config.json")
mail_wake_event = threading.Event()

def ensure_config_dir():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as e:
        print(f"[MailConfig] 目录初始化异常: {e}", flush=True)

def load_mail_config():
    ensure_config_dir()
    if os.path.exists(CONFIG_FILE) and os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception as e:
            print(f"[MailConfig] 读取配置异常: {e}", flush=True)
    return {
        "enable": False,
        "server": "imap.qq.com",
        "port": 993,
        "user": "",
        "password": "",
        "keyword": "",
        "whitelist": "",
        "pushplus_token": "",
        "default_printer": ""
    }

def save_mail_config(cfg):
    try:
        ensure_config_dir()
        if os.path.exists(CONFIG_FILE) and os.path.isdir(CONFIG_FILE):
            shutil.rmtree(CONFIG_FILE, ignore_errors=True)

        json_str = json.dumps(cfg, ensure_ascii=False, indent=2)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(json_str)
            f.flush()

        try:
            os.chmod(CONFIG_FILE, 0o666)
        except Exception:
            pass

        mail_wake_event.set()
        return True, ""
    except Exception as e:
        err = str(e)
        print(f"[MailConfig] 保存配置失败: {err}", flush=True)
        return False, err

def push_wechat_notice(token, title, content):
    if not token:
        return
    try:
        url = "http://www.pushplus.plus/send"
        data = json.dumps({
            "token": token,
            "title": title,
            "content": content,
            "template": "html"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print(f"[PushPlus] 微信通知推送异常: {e}", flush=True)

def get_target_printer(preferred_printer=""):
    env = os.environ.copy()
    env["CUPS_SERVER"] = "/run/cups/cups.sock"
    env["LANG"] = "C"

    if preferred_printer:
        check = subprocess.run(["lpstat", "-p", preferred_printer], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        if check.returncode == 0:
            return preferred_printer

    try:
        res = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, timeout=3)
        for line in res.stdout.splitlines():
            if ":" in line or "：" in line:
                dest = line.replace("：", ":").split(":")[-1].strip()
                if dest and "no system default" not in dest.lower():
                    return dest
    except Exception:
        pass

    try:
        res_a = subprocess.run(["lpstat", "-a"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, timeout=3)
        lines = res_a.stdout.splitlines()
        for l in lines:
            parts = l.split()
            if parts and parts[0]:
                return parts[0].strip()
    except Exception:
        pass

    return ""

def decode_header_safely(header_value):
    if not header_value:
        return ""
    try:
        decoded_fragments = decode_header(header_value)
        text_parts = []
        for fragment, encoding in decoded_fragments:
            if isinstance(fragment, bytes):
                encoding = encoding or "utf-8"
                try:
                    text_parts.append(fragment.decode(encoding, errors="replace"))
                except LookupError:
                    text_parts.append(fragment.decode("utf-8", errors="replace"))
            else:
                text_parts.append(str(fragment))
        return "".join(text_parts).strip()
    except Exception:
        return str(header_value).strip()

def extract_safe_filename(part):
    filename = part.get_filename()
    if not filename:
        for param in part.get_params(header="content-disposition", failobj=[]):
            if isinstance(param, tuple) and len(param) == 2:
                k, v = param
                if k.lower() in ["filename*", "name*"]:
                    try:
                        filename = urllib.parse.unquote(v.split("''")[-1])
                    except Exception:
                        filename = v
                elif k.lower() in ["filename", "name"]:
                    filename = v
    if not filename:
        return ""

    decoded = decode_header_safely(filename)
    clean_name = os.path.basename(decoded).replace("/", "").replace("\\", "")
    return re.sub(r'[\r\n\t]', '', clean_name)

def async_print_attachment_task(file_path, printer_name="", skip_filter=False, token=""):
    try:
        printer = get_target_printer(printer_name)
        filename = os.path.basename(file_path)

        if not printer:
            print(f"[MailWorker] 错误：未找到可用打印队列: {filename}", flush=True)
            push_wechat_notice(token, "⚠️ 云打印失败", f"未发现物理打印机队列，附件 <b>{filename}</b> 未能出纸。")
            return

        target_file = file_path
        ext = os.path.splitext(file_path)[-1].lower()

        if not skip_filter and ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic"]:
            enhanced_path = file_path + f"_{time.time_ns()}_enhanced.jpg"
            try:
                if process_image_for_print(file_path, enhanced_path, media="A4"):
                    target_file = enhanced_path
            except Exception as e:
                print(f"[MailWorker] 增强处理受阻，回退原图: {e}", flush=True)

        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        cmd = [
            "lp",
            "-d", printer,
            "-n", "1",
            "-o", "media=A4",
            "-o", "PageSize=A4",
            "-o", "fit-to-page",
            "-o", "scaling=100",
            "-o", "position=center",
            "-o", "ColorModel=K",
            "-o", "print-color-mode=monochrome"
        ]
        cmd.append(target_file)

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=40)
        clean_old_tmp_files(UPLOAD_DIR)

        if res.returncode == 0:
            job_info = res.stdout.strip()
            print(f"[MailWorker] ✔ 附件成功出纸: {target_file} ({job_info})", flush=True)
            push_wechat_notice(token, "🖨️ 邮件附件打印成功", f"文件 <b>{filename}</b> 已成功送达打印机【{printer}】！<br>任务信息: {job_info}")
        else:
            err = res.stderr.strip()
            print(f"[MailWorker] ❌ CUPS 拒绝打印: {err}", flush=True)
            push_wechat_notice(token, "⚠️ CUPS 拒绝打印", f"文件 <b>{filename}</b> 出纸失败。<br>原因: {err}")

    except subprocess.TimeoutExpired:
        print("[MailWorker] ❌ 打印管道超时，可能出现卡纸或端口锁死", flush=True)
        push_wechat_notice(token, "⚠️ 打印超时告警", f"文件 <b>{os.path.basename(file_path)}</b> 打印超时，请检查硬件通信。")
    except Exception as e:
        print(f"[MailWorker] ❌ 邮件异步出纸进程异常: {e}", flush=True)

def mail_polling_worker():
    print(">>> [MailWorker] 云邮箱常驻守护线程已就绪...", flush=True)
    conn = None
    last_user = ""
    last_pwd = ""

    while True:
        try:
            cfg = load_mail_config()
            if cfg.get("enable", False) and cfg.get("user") and cfg.get("password"):
                cur_user = cfg["user"]
                cur_pwd = cfg["password"]
                server_host = cfg.get("server", "imap.qq.com")
                server_port = int(cfg.get("port", 993))

                if conn is None or cur_user != last_user or cur_pwd != last_pwd:
                    if conn:
                        try:
                            conn.logout()
                        except Exception:
                            pass
                    try:
                        conn = imaplib.IMAP4_SSL(server_host, server_port, timeout=15)
                        conn.login(cur_user, cur_pwd)
                        last_user = cur_user
                        last_pwd = cur_pwd
                        print(f"[MailWorker] IMAP 连接认证成功: {cur_user}@{server_host}", flush=True)
                    except Exception as e:
                        print(f"[MailWorker] 邮箱登录认证受阻，5秒后重试: {e}", flush=True)
                        conn = None
                        time.sleep(5)
                        continue

                try:
                    status, _ = conn.select("INBOX")
                    if status != "OK":
                        raise IOError("Select INBOX failed")
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                    time.sleep(3)
                    continue

                typ, data = conn.search(None, "UNSEEN")
                if typ == "OK" and data[0]:
                    msg_ids = data[0].split()
                    for num in msg_ids:
                        try:
                            typ_m, msg_data = conn.fetch(num, "(RFC822)")
                            if typ_m != "OK" or not msg_data or not msg_data[0]:
                                continue

                            raw_email = msg_data[0][1]
                            msg = email.message_from_bytes(raw_email)

                            raw_from = decode_header_safely(msg.get("From", ""))
                            _, clean_from_addr = parseaddr(raw_from)
                            clean_from_addr = clean_from_addr.lower().strip()

                            whitelist = cfg.get("whitelist", "").strip()
                            if whitelist:
                                allowed_list = [w.strip().lower() for w in whitelist.split(",") if w.strip()]
                                if allowed_list and not any(clean_from_addr == w or clean_from_addr.endswith("@" + w) or clean_from_addr.endswith(w) for w in allowed_list):
                                    print(f"[MailWorker] 拦截未授权发件人: {clean_from_addr}", flush=True)
                                    conn.store(num, "+FLAGS", "\\Seen")
                                    continue

                            subject = decode_header_safely(msg.get("Subject", ""))
                            keyword = cfg.get("keyword", "").strip()
                            if keyword and (keyword.lower() not in subject.lower()):
                                print(f"[MailWorker] 拦截未命中暗号邮件: {subject}", flush=True)
                                conn.store(num, "+FLAGS", "\\Seen")
                                continue

                            skip_filter = ("原图" in subject or "直打" in subject)
                            found_attachments = 0

                            for part in msg.walk():
                                if part.get_content_maintype() == "multipart":
                                    continue

                                clean_fname = extract_safe_filename(part)
                                if not clean_fname:
                                    continue

                                ext = os.path.splitext(clean_fname)[-1].lower()
                                if ext in [".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                                    os.makedirs(UPLOAD_DIR, exist_ok=True)
                                    safe_name = f"mail_{int(time.time())}_{uuid.uuid4().hex[:6]}_{clean_fname}"
                                    save_path = os.path.join(UPLOAD_DIR, safe_name)

                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        with open(save_path, "wb") as f:
                                            f.write(payload)

                                        found_attachments += 1
                                        t = threading.Thread(
                                            target=async_print_attachment_task,
                                            args=(save_path, cfg.get("default_printer", ""), skip_filter, cfg.get("pushplus_token", ""))
                                        )
                                        t.daemon = True
                                        t.start()

                            if found_attachments > 0:
                                print(f"[MailWorker] 邮件 [{subject}] 解析完毕，分发了 {found_attachments} 个出纸附件", flush=True)

                            conn.store(num, "+FLAGS", "\\Seen")

                        except Exception as item_err:
                            print(f"[MailWorker] 单件处理受阻: {item_err}", flush=True)
                            conn.store(num, "+FLAGS", "\\Seen")

            else:
                if conn:
                    try:
                        conn.logout()
                    except Exception:
                        pass
                    conn = None

        except Exception as e:
            conn = None
            time.sleep(5)

        mail_wake_event.wait(timeout=3)
        mail_wake_event.clear()

mail_thread = threading.Thread(target=mail_polling_worker, daemon=True)
mail_thread.start()

class MailConfigHandler(BaseHandler):
    def get(self):
        cfg = load_mail_config()
        safe_cfg = cfg.copy()
        if safe_cfg.get("password"):
            safe_cfg["password"] = "******"
        self.write_json(True, "", data=safe_cfg)

    def post(self):
        try:
            body_data = {}
            if self.request.body:
                try:
                    body_data = json.loads(self.request.body.decode("utf-8"))
                except Exception:
                    pass

            def fetch(key, default=""):
                if key in body_data:
                    return body_data[key]
                return self.get_argument(key, default)

            raw_enable = fetch("enable", "false")
            if isinstance(raw_enable, bool):
                enable = raw_enable
            else:
                enable = str(raw_enable).lower() in ["true", "1", "on"]

            server = str(fetch("server", "imap.qq.com")).strip()
            try:
                port = int(fetch("port", 993))
            except Exception:
                port = 993

            user = str(fetch("user", "")).strip()
            password = str(fetch("password", "")).strip()
            keyword = str(fetch("keyword", "")).strip()
            whitelist = str(fetch("whitelist", "")).strip()
            pushplus_token = str(fetch("pushplus_token", "")).strip()
            default_printer = str(fetch("default_printer", "")).strip()

            old_cfg = load_mail_config()
            if password == "******" or not password:
                password = old_cfg.get("password", "")

            if not default_printer:
                default_printer = old_cfg.get("default_printer", "")

            new_cfg = {
                "enable": enable,
                "server": server,
                "port": port,
                "user": user,
                "password": password,
                "keyword": keyword,
                "whitelist": whitelist,
                "pushplus_token": pushplus_token,
                "default_printer": default_printer
            }

            ok, err = save_mail_config(new_cfg)
            if ok:
                self.write_json(True, "云邮箱设置已保存并立即生效！")
            else:
                self.write_json(False, f"保存失败: {err}")
        except Exception as e:
            self.write_json(False, f"配置写入异常: {str(e)}")
