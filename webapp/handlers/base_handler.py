#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import glob
import time
import subprocess
import tornado.web
from concurrent.futures import ThreadPoolExecutor

UPLOAD_DIR = "/tmp/cups_web_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 独立的轻量线程池（限制并发为 2，防止 ARM 4 核被耗尽）
THREAD_POOL = ThreadPoolExecutor(max_workers=2)

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
        resp = {"success": success, "msg": msg}
        if data is not None:
            resp["data"] = data
        resp.update(kwargs)
        self.write(resp)

    def clean_old_files(self, max_age_seconds=600):
        """非阻塞后台清理：清理 10 分钟前的缓存文件，彻底防止存储占满"""
        def _cleanup():
            try:
                now = time.time()
                for f in glob.glob(os.path.join(UPLOAD_DIR, "*")):
                    if os.path.isfile(f) and (now - os.path.getmtime(f) > max_age_seconds):
                        os.remove(f)
            except Exception:
                pass
        THREAD_POOL.submit(_cleanup)

    def execute_lp(self, printer, copies, file_path, extra_opts=None):
        """高效派发打印作业到 CUPS 管道"""
        cmd = ["lp"]
        if printer:
            cmd.extend(["-d", printer])
        cmd.extend(["-n", str(copies), "-o", "fit-to-page"])
        if extra_opts:
            for opt in extra_opts:
                cmd.extend(["-o", opt])
        cmd.append(file_path)

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        # 触发一次异步清理
        self.clean_old_files()
        return res
