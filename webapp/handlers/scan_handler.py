#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import glob
import time
import fcntl
import subprocess
from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import process_image_for_print

SCANS_DIR = "/scans"
os.makedirs(SCANS_DIR, exist_ok=True)

USBDEVFS_RESET = ord('U') << (4*2) | 20
USBDEVFS_DISCONNECT = ord('U') << (4*2) | 22

def run_cmd(cmd, timeout=15):
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def force_release_usblp_lock():
    """
    终极解绑：
    HP M126a 复合机的核心冲突是 Linux 内核 usblp 驱动霸占了扫描端点。
    通过卸载 usblp 模块并复位 USB，彻底消除 Error during device I/O！
    """
    try:
        # 1. 暂停可能死锁的 CUPS 打印队列
        subprocess.run(["cupsdisable", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 2. 卸载抢占端口的内核打印机驱动 usblp
        subprocess.run(["rmmod", "usblp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 3. 硬件级 USB 端口复位
        for dev_path in glob.glob("/dev/bus/usb/*/*"):
            try:
                fd = os.open(dev_path, os.O_WRONLY)
                try:
                    fcntl.ioctl(fd, USBDEVFS_RESET, 0)
                except Exception:
                    pass
                finally:
                    os.close(fd)
            except Exception:
                pass

        if os.path.exists("/dev/bus/usb"):
            subprocess.run(["chmod", "-R", "666", "/dev/bus/usb"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
    except Exception as e:
        print(f"[ScanHandler] USB解绑异常: {e}", flush=True)

def restore_cups_environment():
    """扫描完成之后恢复环境"""
    try:
        # 重新按需加载 usblp 供常规打印使用
        subprocess.run(["modprobe", "usblp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 唤醒打印队列
        subprocess.run(["cupsenable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

class ScanProbeHandler(BaseHandler):
    def get(self):
        devices = []
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

        if not devices:
            devices.append({
                "id": "hpaio:/usb/HP_LaserJet_Pro_MFP_M126a?serial=CNBKK873D4",
                "name": "HP LaserJet Pro MFP M126a (自动捕获通道)"
            })

        self.write_json(True, "扫描设备探测完成", data={"devices": devices})

class ScanHandler(BaseHandler):
    def post(self):
        device = self.get_argument("device", "").strip()
        resolution = self.get_argument("resolution", "150").strip()
        mode = self.get_argument("mode", "Color").strip()

        if device and (device.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:/=?&]+$', device)):
            device = ""

        # 执行终极解绑，踢掉 usblp 独占锁
        force_release_usblp_lock()

        timestamp = int(time.time())
        raw_pnm = os.path.join(SCANS_DIR, f"scan_raw_{timestamp}.pnm")
        final_jpg = os.path.join(SCANS_DIR, f"scan_{timestamp}.jpg")

        cmd = ["scanimage", "--resolution", str(resolution), "--mode", str(mode)]
        if device:
            cmd.extend(["-d", device])
        cmd.extend(["--format=pnm", f"--output-path={raw_pnm}"])

        print(f"[ScanHandler] 执行硬件扫描: {' '.join(cmd)}", flush=True)
        ok, stdout, stderr = run_cmd(cmd, timeout=50)

        # 恢复环境
        restore_cups_environment()

        if not ok or not os.path.exists(raw_pnm):
            err_msg = stderr.strip() if stderr else stdout.strip()
            if "i/o" in err_msg.lower() or "device i/o" in err_msg.lower():
                self.write_json(False, "扫描仪硬件通信受阻 (Device I/O)。请检查 USB 插头是否插紧，或先在首页点击【恢复打印】清空阻塞任务！")
            elif "busy" in err_msg.lower():
                self.write_json(False, "扫描仪硬件繁忙，请稍候 3 秒后再试。")
            else:
                self.write_json(False, f"扫描硬件拒绝: {err_msg or '设备握手超时'}")
            return

        enhanced_jpg = os.path.join(SCANS_DIR, f"scan_opt_{timestamp}.jpg")
        if process_image_for_print(raw_pnm, enhanced_jpg):
            out_filename = f"scan_opt_{timestamp}.jpg"
        else:
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
