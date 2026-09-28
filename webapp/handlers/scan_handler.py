#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import uuid
import subprocess
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import process_image_for_print

SCAN_DIR = "/opt/webapp/static/scans"
os.makedirs(SCAN_DIR, exist_ok=True)

def release_usb_lock():
    try:
        subprocess.run(["rmmod", "usblp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def detect_scan_devices():
    devices = []
    seen = set()
    try:
        env = os.environ.copy()
        env["LANG"] = "C"
        env["SANE_CONFIG_DIR"] = "/etc/sane.d"
        res = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=6)
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("device `"):
                parts = line.split("`", 1)[1].split("'", 1)
                dev_id = parts[0].strip()
                dev_desc = parts[1].replace("is a", "").strip() if len(parts) > 1 else dev_id

                if dev_id in seen:
                    continue
                seen.add(dev_id)

                if dev_id.startswith("airscan"):
                    devices.insert(0, {"id": dev_id, "name": f"🌐 局域网免驱 ({dev_desc})"})
                elif dev_id.startswith("hpaio"):
                    devices.append({"id": dev_id, "name": f"🔌 HP 硬件专有驱动 ({dev_desc})"})
                else:
                    devices.append({"id": dev_id, "name": f"📷 通用扫描仪 ({dev_desc})"})
    except Exception as e:
        print(f"[ScanHandler] 动态枚举异常: {e}", flush=True)

    if not devices:
        try:
            lsusb = subprocess.run(["lsusb"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout
            if "03f0:222a" in lsusb:
                devices.append({
                    "id": "hpaio:/usb/HP_LaserJet_Pro_MFP_M126a?serial=CNBKK873D4",
                    "name": "🔌 HP LaserJet Pro MFP M126a (直通模式)"
                })
        except Exception:
            pass

    return devices

def execute_scan(device_id, resolution=150, mode="Color", format_type="jpeg"):
    token = uuid.uuid4().hex[:8]
    output_filename = f"scan_{token}.jpg"
    raw_path = os.path.join(SCAN_DIR, f"raw_{token}.jpg")
    final_path = os.path.join(SCAN_DIR, output_filename)

    release_usb_lock()

    env = os.environ.copy()
    env["LANG"] = "C"
    env["SANE_CONFIG_DIR"] = "/etc/sane.d"

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

    print(f"[ScanHandler] 执行扫描: {' '.join(cmd)}", flush=True)
    with open(raw_path, "wb") as f_out:
        res = subprocess.run(cmd, stdout=f_out, stderr=subprocess.PIPE, text=False, env=env, timeout=90)

    # 遇到死锁进行二次延时重试
    if res.returncode != 0:
        err_msg = res.stderr.decode("utf-8", errors="ignore").strip()
        print(f"[ScanHandler] 首次扫描返回: {err_msg}", flush=True)
        if "device I/O" in err_msg or "busy" in err_msg.lower():
            time.sleep(1.2)
            release_usb_lock()
            with open(raw_path, "wb") as f_out:
                res = subprocess.run(cmd, stdout=f_out, stderr=subprocess.PIPE, text=False, env=env, timeout=90)

    if res.returncode != 0:
        err_msg = res.stderr.decode("utf-8", errors="ignore").strip()
        if os.path.exists(raw_path):
            os.remove(raw_path)
        if "device I/O" in err_msg:
            raise RuntimeError("扫描仪硬件正忙或处于死锁状态，请将一体机重启后重试！")
        raise RuntimeError(err_msg or "扫描通信超时")

    try:
        if not process_image_for_print(raw_path, final_path):
            os.rename(raw_path, final_path)
        else:
            if os.path.exists(raw_path):
                os.remove(raw_path)
    except Exception as e:
        print(f"[ScanHandler] 图像处理异常，返回原图: {e}", flush=True)
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
                    self.write_json(False, "未检测到可用扫描仪硬件，请确认一体机已通电并连接 USB")
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
