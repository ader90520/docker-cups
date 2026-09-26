#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import uuid
import urllib.parse
import subprocess
from handlers.base_handler import BaseHandler

class PrinterAdminHandler(BaseHandler):
    def _clean_printer_name(self, raw_str):
        """清洗提取规范的 CUPS 打印机名称 (仅允许英文字母、数字、下划线和连字符)"""
        if not raw_str:
            return f"Printer_{uuid.uuid4().hex[:4]}"
        decoded = urllib.parse.unquote(raw_str)
        cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", decoded).strip("_")
        # 截断过长名称，CUPS 打印机名称长度建议在 32 字符以内
        return cleaned[:32] if cleaned else f"Printer_{uuid.uuid4().hex[:4]}"

    def get(self):
        action = self.get_argument("action", "")
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        if action == "discovered_devices":
            # 扫描物理连接的 USB / 网络打印设备
            res = subprocess.run(["lpinfo", "-v"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            devices = []
            for line in res.stdout.splitlines():
                if any(k in line for k in ["direct usb://", "hp:/usb/", "hpfax:/usb/", "socket://", "ipp://", "dnssd://"]):
                    parts = line.split(" ", 1)
                    if len(parts) == 2:
                        uri = parts[1].strip()
                        # 解析 URI 生成更友好的可读自动名称
                        raw_seg = uri.split("/")[-1].split("?")[0]
                        if not raw_seg and len(uri.split("/")) > 2:
                            raw_seg = uri.split("/")[-2]
                        auto_name = self._clean_printer_name(raw_seg)
                        devices.append({
                            "uri": uri,
                            "name": uri,
                            "auto_name": auto_name
                        })
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

        ppd_files = self.request.files.get("ppd_file")
        uri = self.get_argument("uri", "").strip()
        driver = self.get_argument("driver", "").strip()
        name = self.get_argument("name", "").strip()

        if not uri:
            self.write_json(False, "未选择打印机物理端口设备！")
            return

        # 前端未传或留空时自动计算打印机名称
        if not name:
            raw_seg = uri.split("/")[-1].split("?")[0]
            if not raw_seg and len(uri.split("/")) > 2:
                raw_seg = uri.split("/")[-2]
            name = self._clean_printer_name(raw_seg)

        cmd = ["lpadmin", "-p", name, "-v", uri, "-E"]

        tmp_ppd_path = None
        # 1. 优先使用用户上传的本地自定义 PPD 驱动文件
        if ppd_files and len(ppd_files) > 0:
            uploaded = ppd_files[0]
            token = uuid.uuid4().hex[:8]
            tmp_ppd_path = f"/tmp/{token}.ppd"
            with open(tmp_ppd_path, "wb") as f:
                f.write(uploaded["body"])
            os.chmod(tmp_ppd_path, 0o666)
            cmd.extend(["-P", tmp_ppd_path])
        # 2. 其次使用系统驱动库指定的驱动 ID
        elif driver and driver != "raw":
            cmd.extend(["-m", driver])
        # 3. 兜底透传驱动
        else:
            cmd.extend(["-m", "raw"])

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

        # 清理中转的临时上传文件
        if tmp_ppd_path and os.path.exists(tmp_ppd_path):
            try: os.remove(tmp_ppd_path)
            except: pass

        if res.returncode == 0:
            # 设为默认打印机，并激活接收/打印队列
            subprocess.run(["lpadmin", "-d", name], env=env)
            subprocess.run(["cupsenable", name], env=env)
            subprocess.run(["cupsaccept", name], env=env)
            self.write_json(True, f"打印机【{name}】配置成功并已设为默认！", printer=name)
        else:
            err_msg = res.stderr.strip() or "CUPS 执行拒绝"
            self.write_json(False, f"配置失败: {err_msg}")
