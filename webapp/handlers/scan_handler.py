#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import subprocess
from handlers.base_handler import BaseHandler

SCAN_DIR = "/scans"
os.makedirs(SCAN_DIR, exist_ok=True)

class ScanHandler(BaseHandler):
    def get(self):
        """探测可用的扫描仪设备"""
        try:
            env = os.environ.copy()
            env["LANG"] = "C"
            res = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8, env=env)
            output = res.stdout.strip()
            devices = []
            
            for line in output.splitlines():
                if "device `" in line:
                    dev_id = line.split("`")[1].split("'")[0]
                    desc = line.split("is a")[-1].strip() if "is a" in line else dev_id
                    devices.append({"id": dev_id, "name": desc})

            self.write_json(True, "扫描仪检测完成", data={"devices": devices, "raw": output})
        except Exception as e:
            self.write_json(False, f"探测扫描仪异常: {str(e)}")

    def post(self):
        """执行硬件扫描任务并生成图片"""
        try:
            device = self.get_argument("device", "").strip()
            resolution = self.get_argument("resolution", "200").strip()
            mode = self.get_argument("mode", "Color").strip()
            copy_print = self.get_argument("copy_print", "0").strip()
            printer = self.get_argument("printer", "").strip()

            timestamp = int(time.time())
            filename = f"scan_{timestamp}.jpg"
            filepath = os.path.join(SCAN_DIR, filename)

            cmd = ["scanimage"]
            if device:
                cmd.extend(["-d", device])
            cmd.extend([
                "--format=jpeg",
                f"--output-file={filepath}",
                "--resolution", resolution,
                "--mode", mode
            ])

            print(f"[ScanHandler] 执行扫描: {' '.join(cmd)}")
            env = os.environ.copy()
            env["LANG"] = "C"
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90, env=env)

            if res.returncode != 0 or not os.path.exists(filepath):
                err = res.stderr.strip() or "扫描仪响应异常"
                self.write_json(False, f"扫描失败: {err}")
                return

            print(f"[ScanHandler] ✔ 扫描完成: {filepath}")

            copy_job = ""
            if copy_print == "1":
                lp_env = os.environ.copy()
                lp_env["CUPS_SERVER"] = "/run/cups/cups.sock"
                lp_env["LANG"] = "C"
                lp_cmd = ["lp"]
                if printer:
                    lp_cmd.extend(["-d", printer])
                lp_cmd.extend(["-o", "media=A4", "-o", "fit-to-page", filepath])
                lp_res = subprocess.run(lp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=lp_env)
                if lp_res.returncode == 0:
                    copy_job = lp_res.stdout.strip()
                    print(f"[ScanHandler] ✔ 自动复印作业派发成功: {copy_job}")

            self.write_json(True, "扫描完成", filename=filename, url=f"/download/scan/{filename}", copy_job=copy_job)
        except subprocess.TimeoutExpired:
            self.write_json(False, "扫描仪响应超时，请检查连接或供电！")
        except Exception as e:
            self.write_json(False, f"扫描执行异常: {str(e)}")

class DownloadScanHandler(BaseHandler):
    def get(self, filename):
        """提供扫描件下载与预览"""
        filepath = os.path.join(SCAN_DIR, filename)
        if not os.path.exists(filepath):
            self.set_status(404)
            self.write("文件不存在")
            return
        
        self.set_header("Content-Type", "image/jpeg")
        with open(filepath, "rb") as f:
            self.write(f.read())
