#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import glob
import time
import subprocess
import tornado.web
from concurrent.futures import ThreadPoolExecutor

UPLOAD_DIR = "/tmp/cups_web_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 限制工作线程池并发数为 2，防止 ARM 4 核设备由于密集调度引发高负载
THREAD_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="CupsWorker")

class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With")

    def options(self, *args, **kwargs):
        self.set_status(204)
        self.finish()

    def write_json(self, success=True, msg="", data=None, **kwargs):
        resp = {
            "success": bool(success),
            "msg": str(msg),
            "message": str(msg)
        }
        if data is not None:
            resp["data"] = data
        resp.update(kwargs)
        self.write(resp)

    def clean_old_files(self, max_age_seconds=300):
        def _cleanup():
            try:
                now = time.time()
                if not os.path.exists(UPLOAD_DIR):
                    os.makedirs(UPLOAD_DIR, exist_ok=True)
                    return
                for f in glob.glob(os.path.join(UPLOAD_DIR, "*")):
                    try:
                        if os.path.isfile(f) and (now - os.path.getmtime(f) > max_age_seconds):
                            os.remove(f)
                    except Exception:
                        pass
            except Exception:
                pass
        THREAD_POOL.submit(_cleanup)

    def execute_lp(self, printer, copies, file_path, extra_opts=None, timeout=20):
        clean_printer = str(printer).strip() if printer else ""
        if clean_printer and (clean_printer.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:+]+$', clean_printer)):
            return False, "非法打印机设备名称"

        try:
            copies_int = int(copies)
            if not (1 <= copies_int <= 99):
                copies_int = 1
        except (ValueError, TypeError):
            copies_int = 1

        if not file_path or not os.path.exists(file_path):
            return False, "待打印文件不存在"

        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        cmd = ["lp"]
        if clean_printer:
            cmd.extend(["-d", clean_printer])
        cmd.extend([
            "-n", str(copies_int),
            "-o", "fit-to-page",
            "-o", "scaling=100",
            "-o", "natural-scaling=100",
            "-o", "position=center"
        ])

        if extra_opts and isinstance(extra_opts, list):
            for opt in extra_opts:
                opt_str = str(opt).strip()
                if opt_str and not opt_str.startswith("-") and "=" in opt_str:
                    cmd.extend(["-o", opt_str])

        cmd.append(file_path)

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                timeout=timeout
            )
            self.clean_old_files()

            if res.returncode == 0:
                job_id = res.stdout.strip()
                return True, job_id
            else:
                return False, res.stderr.strip() or "CUPS 拒绝作业"
        except subprocess.TimeoutExpired:
            return False, "打印机通信超时"
        except Exception as e:
            return False, f"调度进程异常: {str(e)}"
