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
            res = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            output = res.stdout.strip()
            devices = []
            
            # 格式类似: device `hpljm1005:libusb:001:004' is a Hewlett-Packard LaserJet M1005 flatbed scanner
            for line in output.splitlines():
                if "device `" in line:
                    dev_id = line.split("`")[1].split("'")[0]
                    desc = line.split("is a")[-1].strip() if "is a" in line else dev_id
                    devices.append({"id": dev_id, "name": desc})

            self.write_json(True, "扫描仪检测成功", data={"devices": devices, "raw": output})
        except Exception as e:
            self.write_json(False, f"探测扫描仪异常: {str(e)}")

    def post(self):
        """执行硬件扫描任务并生成图片"""
        try:
            device = self.get_argument("device", "").strip()
            resolution = self.get_argument("resolution", "200").strip()
            mode = self.get_argument("mode", "Color").strip()  # Color, Gray, Lineart
            copy_print = self.get_argument("copy_print", "0").strip() # 1 = 扫描后自动复印
            printer = self.get_argument("printer", "").strip()

            timestamp = int(time.time())
            filename = f"scan_{timestamp}.jpg"
            filepath = os.path.join(SCAN_DIR, filename)

            # 构建 scanimage 命令
            cmd = ["scanimage"]
            if device:
                cmd.extend(["-d", device])
            cmd.extend([
                "--format=jpeg",
                f"--output-file={filepath}",
                "--resolution", resolution,
                "--mode", mode
            ])

            print(f"[ScanHandler] 执行扫描指令: {' '.join(cmd)}")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90)

            if res.returncode != 0 or not os.path.exists(filepath):
                err = res.stderr.strip() or "扫描仪未就绪或未检测到介质"
                self.write_json(False, f"扫描失败: {err}")
                return

            print(f"[ScanHandler] ✔ 扫描完成，文件已保存至: {filepath}")

            # 如果用户开启了一键复印，直接送往 CUPS 打印机
            copy_job = ""
            if copy_print == "1":
                lp_res = self.execute_lp(printer, "1", filepath)
                if lp_res.returncode == 0:
                    copy_job = lp_res.stdout.strip()
                    print(f"[ScanHandler] ✔ 自动复印作业已派发: {copy_job}")

            self.write_json(True, "扫描完成", filename=filename, url=f"/download/scan/{filename}", copy_job=copy_job)
        except subprocess.TimeoutExpired:
            self.write_json(False, "扫描仪响应超时，请检查 USB 连接或机器供电！")
        except Exception as e:
            self.write_json(False, f"扫描执行异常: {str(e)}")

class DownloadScanHandler(BaseHandler):
    def get(self, filename):
        """提供扫描件直接下载与网页内嵌预览"""
        filepath = os.path.join(SCAN_DIR, filename)
        if not os.path.exists(filepath):
            self.set_status(404)
            self.write("文件不存在")
            return
        
        self.set_header("Content-Type", "image/jpeg")
        with open(filepath, "rb") as f:
            self.write(f.read())
