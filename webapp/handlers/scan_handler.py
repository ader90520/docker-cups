#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import uuid
import subprocess
import threading
from PIL import Image
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import process_image_for_print

SCAN_DIR = "/opt/webapp/static/scans"
os.makedirs(SCAN_DIR, exist_ok=True)

def detect_scan_devices():
    devices = []
    try:
        env = os.environ.copy()
        env["LANG"] = "C"
        res = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=6)
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("device `"):
                parts = line.split("`", 1)[1].split("'", 1)
                dev_id = parts[0].strip()
                dev_desc = parts[1].replace("is a", "").strip() if len(parts) > 1 else dev_id
                
                if dev_id.startswith("hpaio"):
                    devices.insert(0, {"id": dev_id, "name": f"HP 官方通道 ({dev_desc})"})
                else:
                    devices.append({"id": dev_id, "name": dev_desc})
    except Exception as e:
        print(f"[ScanHandler] 动态枚举异常: {e}", flush=True)

    # 兜底通道：当 scanimage -L 阻塞时，检测 USB 硬件 ID 自动匹配
    if not devices:
        try:
            lsusb_res = subprocess.run(["lsusb"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if "03f0:222a" in lsusb_res.stdout:
                fallback_uri = "hpaio:/usb/HP_LaserJet_Pro_MFP_M126a?serial=CNBKK873D4"
                print(f"[ScanHandler] 触发硬件兜底通道: {fallback_uri}", flush=True)
                devices.append({"id": fallback_uri, "name": "HP LaserJet Pro MFP M126a (直通模式)"})
        except Exception:
            pass

    return devices

def execute_scan(device_id, resolution=150, mode="Color", format_type="jpeg"):
    token = uuid.uuid4().hex[:8]
    output_filename = f"scan_{token}.jpg"
    raw_path = os.path.join(SCAN_DIR, f"raw_{token}.jpg")
    final_path = os.path.join(SCAN_DIR, output_filename)

    env = os.environ.copy()
    env["LANG"] = "C"

    # 严格限定 A4 边界 (210mm x 297mm)
    cmd = [
        "scanimage",
        "-d", device_id,
        "--resolution", str(resolution),
        "--mode", mode,
        "-l", "0",
        "-t", "0",
        "-x", "210",
        "-y", "297",
        "--format=jpeg"
    ]

    print(f"[ScanHandler] 下发扫描任务: {' '.join(cmd)}", flush=True)
    with open(raw_path, "wb") as f_out:
        res = subprocess.run(cmd, stdout=f_out, stderr=subprocess.PIPE, text=False, env=env, timeout=60)

    if res.returncode != 0:
        err_msg = res.stderr.decode("utf-8", errors="ignore").strip()
        print(f"[ScanHandler] 扫描硬件通信失败: {err_msg}", flush=True)
        if os.path.exists(raw_path):
            os.remove(raw_path)
        raise RuntimeError(err_msg or "硬件通信超时，请检查一体机 USB 连接")

    try:
        if not process_image_for_print(raw_path, final_path):
            os.rename(raw_path, final_path)
        else:
            if os.path.exists(raw_path):
                os.remove(raw_path)
    except Exception as e:
        print(f"[ScanHandler] 图像优化异常，使用原始扫描件: {e}", flush=True)
        os.rename(raw_path, final_path)

    return f"/static/scans/{output_filename}", output_filename

class ScanProbeHandler(BaseHandler):
    def get(self):
        devs = detect_scan_devices()
        self.write_json(True, "", data={"devices": devs})

class ScanHandler(BaseHandler):
    def post(self):
        try:
            device = self.get_argument("device", "").strip()
            resolution = int(self.get_argument("resolution", "150"))
            mode = self.get_argument("mode", "Color").strip()

            if not device:
                devs = detect_scan_devices()
                if not devs:
                    self.write_json(False, "未检测到可用的扫描仪硬件，请确认一体机已通电并连接 USB")
                    return
                device = devs[0]["id"]

            web_url, filename = execute_scan(device, resolution, mode)
            self.write_json(True, "扫描完成", data={"url": web_url, "filename": filename})
        except Exception as e:
            self.write_json(False, f"扫描中断: {str(e)}")

class DownloadScanHandler(BaseHandler):
    def get(self):
        filename = self.get_argument("file", "").strip()
        filepath = os.path.join(SCAN_DIR, filename)
        if os.path.exists(filepath):
            self.set_header("Content-Type", "application/octet-stream")
            self.set_header("Content-Disposition", f"attachment; filename={filename}")
            with open(filepath, "rb") as f:
                self.write(f.read())
        else:
            self.write_json(False, "文件不存在")
