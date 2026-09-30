#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import subprocess
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import process_image_for_print

SCANS_DIR = "/scans"
os.makedirs(SCANS_DIR, exist_ok=True)

def run_cmd(cmd, timeout=15):
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def release_usb_printer_lock():
    """解除因打印机缺纸/报错导致的 USB 端口死锁，将总线交还给扫描仪"""
    try:
        # 暂停可能正在死等进纸的 CUPS 打印队列
        subprocess.run(["cupsdisable", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 确保权限全开
        if os.path.exists("/dev/bus/usb"):
            subprocess.run(["chmod", "-R", "666", "/dev/bus/usb"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
    except Exception:
        pass

def restore_cups_queue():
    """扫描完毕后唤醒打印队列"""
    try:
        subprocess.run(["cupsenable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

class ScanProbeHandler(BaseHandler):
    def get(self):
        """探测可用扫描仪（自动适配通用 SANE 与 HP 复合机）"""
        devices = []
        # 1. 尝试 scanimage 探测
        ok, out, _ = run_cmd(["scanimage", "-L"], timeout=8)
        if ok and out:
            for line in out.splitlines():
                if "is a" in line:
                    match = re.search(r"`([^']+)'\s+is\s+a\s+(.+)", line)
                    if match:
                        devices.append({
                            "id": match.group(1).strip(),
                            "name": match.group(2).strip()
                        })

        # 2. 如果没抓到，尝试通过 hp-probe 探测复合设备
        if not devices:
            ok_hp, out_hp, _ = run_cmd(["hp-probe", "-busb"], timeout=8)
            if ok_hp and out_hp:
                for line in out_hp.splitlines():
                    if "hp:/" in line or "hpaio:/" in line:
                        parts = line.strip().split()
                        uri = parts[0]
                        devices.append({
                            "id": uri.replace("hp:/", "hpaio:/"),
                            "name": "HP LaserJet Pro MFP 复合扫描仪"
                        })

        # 3. 兜底默认通道
        if not devices:
            devices.append({
                "id": "hpaio:/usb/HP_LaserJet_Pro_MFP_M126a?serial=default",
                "name": "HP LaserJet Pro MFP M126a (自动推荐)"
            })

        self.write_json(True, "扫描设备探测完成", data={"devices": devices})

class ScanHandler(BaseHandler):
    def post(self):
        device = self.get_argument("device", "").strip()
        resolution = self.get_argument("resolution", "150").strip()
        mode = self.get_argument("mode", "Color").strip()

        # 安全防注入
        if device and (device.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:/=?&]+$', device)):
            device = ""

        # 预先解除可能的打印端点锁
        release_usb_printer_lock()

        timestamp = int(time.time())
        raw_pnm = os.path.join(SCANS_DIR, f"scan_raw_{timestamp}.pnm")
        final_jpg = os.path.join(SCANS_DIR, f"scan_{timestamp}.jpg")

        cmd = ["scanimage", "--resolution", str(resolution), "--mode", str(mode)]
        if device:
            cmd.extend(["-d", device])
        cmd.extend(["--format=pnm", f"--output-path={raw_pnm}"])

        print(f"[ScanHandler] 执行硬件扫描命令: {' '.join(cmd)}", flush=True)
        ok, stdout, stderr = run_cmd(cmd, timeout=45)

        # 恢复打印队列
        restore_cups_queue()

        if not ok or not os.path.exists(raw_pnm):
            err_msg = stderr.strip() if stderr else stdout.strip()
            if "busy" in err_msg.lower() or "device busy" in err_msg.lower():
                self.write_json(False, "扫描仪硬件正忙（通常因打印机正处于 E1 缺纸或卡纸阻塞状态），请先补充纸张或点击【复位USB通信】！")
            elif "no devices" in err_msg.lower():
                self.write_json(False, "未找到扫描仪硬件，请确认 USB 连接良好且赋予了 --privileged 权限。")
            else:
                self.write_json(False, f"扫描硬件拒绝: {err_msg or '通信超时'}")
            return

        # 自动执行图像漂白与去噪
        enhanced_jpg = os.path.join(SCANS_DIR, f"scan_opt_{timestamp}.jpg")
        if process_image_for_print(raw_pnm, enhanced_jpg):
            out_filename = f"scan_opt_{timestamp}.jpg"
        else:
            # 降级直接转码为 JPG
            subprocess.run(["convert", raw_pnm, final_jpg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            out_filename = f"scan_{timestamp}.jpg"

        try:
            if os.path.exists(raw_pnm):
                os.remove(raw_pnm)
        except Exception:
            pass

        self.write_json(True, "扫描并去灰底成功！", data={
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
