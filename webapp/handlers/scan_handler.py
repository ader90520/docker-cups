#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import subprocess
from handlers.base_handler import BaseHandler

SCANS_DIR = "/scans"
os.makedirs(SCANS_DIR, exist_ok=True)

def run_cmd(cmd, env=None, timeout=40):
    if env is None:
        env = os.environ.copy()
        env["SANE_USB_WORKAROUND"] = "1"
        env["LANG"] = "C"
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=timeout)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def enumerate_all_scanners():
    """动态感知所有在线扫描设备（兼容 M125nw/M126a、M1005 及任意通用 SANE 设备）"""
    devices = []
    seen = set()

    ok, out, _ = run_cmd(["scanimage", "-L"], timeout=6)
    if ok and out:
        for line in out.splitlines():
            line_s = line.strip()
            if "is a" in line_s:
                m = re.search(r"`([^']+)'\s+is\s+a\s+(.+)", line_s)
                if m:
                    dev_id = m.group(1).strip()
                    dev_name = m.group(2).strip()
                    if dev_id not in seen:
                        devices.append({"id": dev_id, "name": dev_name})
                        seen.add(dev_id)

    if not devices:
        ok_hp, out_hp, _ = run_cmd(["hp-probe", "-busb"], timeout=6)
        if ok_hp and out_hp:
            for line in out_hp.splitlines():
                line_s = line.strip()
                if "hp:/" in line_s or "hpaio:/" in line_s:
                    parts = line_s.split()
                    uri = parts[0].replace("hp:/", "hpaio:/")
                    name = "HP 多功能一体机 (HPLIP 通道)"
                    if len(parts) > 1:
                        name = " ".join(parts[1:])
                    if uri not in seen:
                        devices.append({"id": uri, "name": name})
                        seen.add(uri)

    ok_lsusb, out_lsusb, _ = run_cmd(["lsusb"])
    if ok_lsusb and "03f0:3b17" in out_lsusb:
        m1005_uri = "hpaio:/usb/HP_LaserJet_M1005?serial=auto"
        if not any("M1005" in d["id"] for d in devices):
            devices.insert(0, {"id": m1005_uri, "name": "HP LaserJet M1005 MFP"})

    return devices

class ScanProbeHandler(BaseHandler):
    def get(self):
        devices = enumerate_all_scanners()
        self.write_json(True, "扫描设备列表已更新", data={"devices": devices})

class ScanHandler(BaseHandler):
    def post(self):
        device = self.get_argument("device", "").strip()
        resolution = self.get_argument("resolution", "150").strip()
        mode = self.get_argument("mode", "Color").strip()

        available_devices = enumerate_all_scanners()
        if not available_devices:
            self.write_json(False, "未检测到任何可用扫描仪，请确认设备已开机并插紧 USB 数据线！")
            return

        valid_ids = [d["id"] for d in available_devices]
        if not device or device not in valid_ids:
            device = available_devices[0]["id"]

        subprocess.run(["cupsdisable", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        timestamp = int(time.time())
        raw_pnm = os.path.join(SCANS_DIR, f"scan_raw_{timestamp}.pnm")
        final_jpg = os.path.join(SCANS_DIR, f"scan_{timestamp}.jpg")

        env = os.environ.copy()
        env["SANE_USB_WORKAROUND"] = "1"
        env["LANG"] = "C"

        cmd = [
            "scanimage",
            "-d", device,
            f"--resolution={resolution}",
            f"--mode={mode}",
            "--format=pnm",
            f"--output-path={raw_pnm}"
        ]

        print(f"[ScanHandler] 执行硬件扫描 (设备: {device}): {' '.join(cmd)}", flush=True)
        ok, stdout, stderr = run_cmd(cmd, env=env, timeout=50)

        if not ok or not os.path.exists(raw_pnm):
            print(f"[ScanHandler] scanimage 通信受阻 ({stderr})，调用 hp-scan 专有通道回退...", flush=True)
            hp_dev = device.replace("hpaio:/", "hp:/")
            hp_cmd = [
                "hp-scan",
                f"-d{hp_dev}",
                "-m" + ("color" if mode == "Color" else "gray"),
                f"-r{resolution}",
                "-sfile",
                f"-o{final_jpg}"
            ]
            ok_hp, _, hp_err = run_cmd(hp_cmd, env=env, timeout=50)
            subprocess.run(["cupsenable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if not ok_hp or not os.path.exists(final_jpg):
                self.write_json(False, f"扫描硬件通信失败: {stderr or hp_err}。此机型需加载 HP 闭源插件，可在设备管理页面上传插件包。")
                return
        else:
            subprocess.run(["cupsenable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                subprocess.run(["convert", raw_pnm, "-quality", "95", final_jpg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if not os.path.exists(final_jpg):
                    from PIL import Image
                    with Image.open(raw_pnm) as img:
                        img.save(final_jpg, format="JPEG", quality=95)
            except Exception as e:
                print(f"[ScanHandler] 转码异常: {e}", flush=True)

            if os.path.exists(raw_pnm):
                try:
                    os.remove(raw_pnm)
                except Exception:
                    pass

        out_filename = f"scan_{timestamp}.jpg"
        self.write_json(True, "原始扫描完成！已呈现原件真实细节", data={
            "url": f"/scans/{out_filename}",
            "filename": out_filename
        })

class DownloadScanHandler(BaseHandler):
    def get(self):
        filename = self.get_argument("file", "").strip()
        filename = os.path.basename(filename)
        file_path = os.path.join(SCANS_DIR, filename)

        if not os.path.exists(file_path):
            self.set_status(404)
            self.write("文件不存在")
            return

        self.set_header("Content-Type", "application/octet-stream")
        self.set_header("Content-Disposition", f"attachment; filename={filename}")
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.write(chunk)
        self.finish()
