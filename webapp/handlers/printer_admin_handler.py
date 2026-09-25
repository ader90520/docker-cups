#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import subprocess
from handlers.base_handler import BaseHandler

PPD_DIR = "/etc/cups/ppd"
os.makedirs(PPD_DIR, exist_ok=True)

class PrinterAdminHandler(BaseHandler):
    def get(self):
        action = self.get_argument("action", "")
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        if action == "discovered_devices":
            # 扫描物理连接的 USB / 局域网设备
            res = subprocess.run(["lpinfo", "-v"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            devices = []
            for line in res.stdout.splitlines():
                if any(k in line for k in ["direct usb://", "hp:/usb/", "hpfax:/usb/", "socket://", "ipp://"]):
                    parts = line.split(" ", 1)
                    if len(parts) == 2:
                        uri = parts[1].strip()
                        raw_name = uri.split("/")[-1].split("?")[0]
                        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_name)
                        devices.append({"uri": uri, "name": uri, "auto_name": clean_name or "Printer"})
            self.write_json(True, data=devices)
            return

        elif action == "drivers":
            q = self.get_argument("q", "").strip().lower()
            cmd = ["lpinfo", "-m"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, env=env)
            drivers = []
            for line in res.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split(" ", 1)
                drv_id = parts[0]
                desc = parts[1] if len(parts) > 1 else drv_id
                if not q or q in line.lower():
                    drivers.append({"id": drv_id, "name": desc})
                    if len(drivers) >= 60:
                        break
            self.write_json(True, data=drivers)
            return

        self.write_json(False, "未知操作")

    def post(self):
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        # 1. 支持直接上传自定义 PPD 驱动文件
        ppd_file = self.request.files.get("ppd_file")
        uri = self.get_argument("uri", "").strip()
        driver = self.get_argument("driver", "").strip()
        name = self.get_argument("name", "").strip()

        if not uri:
            self.write_json(False, "未选择打印机端口设备")
            return

        # 如果没有传入名称，自动基于端口提取
        if not name:
            raw_name = uri.split("/")[-1].split("?")[0]
            name = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_name) or "Printer"

        cmd = ["lpadmin", "-p", name, "-v", uri, "-E"]

        # 处理用户上传的本地 PPD 驱动
        if ppd_file and len(ppd_file) > 0:
            uploaded = ppd_file[0]
            ppd_path = os.path.join(PPD_DIR, f"{name}.ppd")
            with open(ppd_path, "wb") as f:
                f.write(uploaded["body"])
            cmd.extend(["-P", ppd_path])
        elif driver and driver != "raw":
            cmd.extend(["-m", driver])
        else:
            cmd.extend(["-m", "raw"])

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        if res.returncode == 0:
            # 设为默认打印机并启用队列
            subprocess.run(["lpadmin", "-d", name], env=env)
            subprocess.run(["cupsenable", name], env=env)
            subprocess.run(["cupsaccept", name], env=env)
            self.write_json(True, f"打印机【{name}】添加并启用成功！", printer=name)
        else:
            self.write_json(False, f"添加失败: {res.stderr.strip()}")
