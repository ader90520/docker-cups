#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import subprocess
from PIL import Image
from handlers.base_handler import BaseHandler

SCAN_DIR = "/scans"
os.makedirs(SCAN_DIR, exist_ok=True)

class ScanHandler(BaseHandler):
    def get(self):
        """探测可用的扫描仪设备"""
        try:
            subprocess.run(["chmod", "-R", "666", "/dev/bus/usb"], stderr=subprocess.DEVNULL)
            env = os.environ.copy()
            res = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8, env=env)
            output = res.stdout.strip()
            devices = []

            for line in output.splitlines():
                if "device `" in line:
                    dev_id = line.split("`")[1].split("'")[0]
                    desc = line.split("is a")[-1].strip() if "is a" in line else dev_id
                    friendly_name = desc.replace("all-in-one", "").replace("Hewlett-Packard", "HP").strip()
                    devices.append({"id": dev_id, "name": friendly_name})

            self.write_json(True, "扫描仪检测成功", data={"devices": devices, "raw": output})
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
            tmp_pnm = os.path.join(SCAN_DIR, f"temp_{timestamp}.pnm")
            target_jpg = os.path.join(SCAN_DIR, f"scan_{timestamp}.jpg")

            cmd = ["scanimage", "--resolution", str(resolution), "--mode", mode]
            if device:
                cmd.extend(["-d", device])

            print(f"[ScanHandler] 执行原始扫描管道: {' '.join(cmd)}")
            env = os.environ.copy()
            with open(tmp_pnm, "wb") as f_out:
                res = subprocess.run(cmd, stdout=f_out, stderr=subprocess.PIPE, timeout=90, env=env)

            if res.returncode != 0 or not os.path.exists(tmp_pnm) or os.path.getsize(tmp_pnm) == 0:
                err = res.stderr.decode("utf-8", errors="ignore").strip() or "扫描仪未返回数据"
                if os.path.exists(tmp_pnm):
                    os.remove(tmp_pnm)
                self.write_json(False, f"扫描失败: {err}")
                return

            with Image.open(tmp_pnm) as img:
                img.convert("RGB").save(target_jpg, format="JPEG", quality=92)

            if os.path.exists(tmp_pnm):
                os.remove(tmp_pnm)

            print(f"[ScanHandler] ✔ 扫描完成: {target_jpg}")

            copy_job = ""
            if copy_print == "1":
                lp_env = os.environ.copy()
                lp_env["CUPS_SERVER"] = "/run/cups/cups.sock"
                lp_cmd = ["lp"]
                if printer:
                    lp_cmd.extend(["-d", printer])
                lp_cmd.extend(["-o", "media=A4", "-o", "fit-to-page", target_jpg])
                lp_res = subprocess.run(lp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=lp_env)
                if lp_res.returncode == 0:
                    copy_job = lp_res.stdout.strip()
                    print(f"[ScanHandler] ✔ 自动复印作业下发成功: {copy_job}")

            self.write_json(True, "扫描完成", filename=f"scan_{timestamp}.jpg", url=f"/download/scan/scan_{timestamp}.jpg", copy_job=copy_job)
        except subprocess.TimeoutExpired:
            self.write_json(False, "扫描仪响应超时，请确认盖板合上且未卡纸！")
        except Exception as e:
            self.write_json(False, f"扫描执行异常: {str(e)}")

class DownloadScanHandler(BaseHandler):
    def get(self, filename):
        filepath = os.path.join(SCAN_DIR, filename)
        if not os.path.exists(filepath):
            self.set_status(404)
            self.write("文件不存在")
            return
        self.set_header("Content-Type", "image/jpeg")
        with open(filepath, "rb") as f:
            self.write(f.read())
