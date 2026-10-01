#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import threading
import subprocess
from handlers.base_handler import BaseHandler

SCANS_DIR = "/scans"
os.makedirs(SCANS_DIR, exist_ok=True)

# 全局硬件扫描互斥锁，彻底杜绝多端并发导致的 Device I/O 锁死
SCAN_LOCK = threading.Lock()

def run_cmd(cmd, env=None, timeout=12):
    if env is None:
        env = os.environ.copy()
        env["LANG"] = "C"
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=timeout)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def enumerate_all_scanners():
    """通用动态多机型嗅探：兼容 M125nw/M126a、M1005、佳能、爱普生等 SANE 设备"""
    devices = []
    seen = set()

    ok, out, _ = run_cmd(["scanimage", "-L"], timeout=8)
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
        ok_ls, out_ls = run_cmd(["lsusb"])
        if ok_ls and out_ls:
            for line in out_ls.splitlines():
                if any(v in line.lower() for v in ["hewlett", "hp", "epson", "canon", "brother"]):
                    parts = line.split()
                    if len(parts) >= 6:
                        bus = parts[1]
                        dev = parts[3].replace(":", "")
                        dev_uri = f"libusb:{bus}:{dev}"
                        desc = " ".join(parts[6:])
                        if dev_uri not in seen:
                            devices.append({"id": dev_uri, "name": f"{desc} (通用USB)"})
                            seen.add(dev_uri)

    return devices

class ScanProbeHandler(BaseHandler):
    def get(self):
        devices = enumerate_all_scanners()
        self.write_json(True, "扫描设备列表已更新", data={"devices": devices})

class ScanHandler(BaseHandler):
    def post(self):
        # 1. 严格参数过滤与白名单防注入
        device = self.get_argument("device", "").strip()
        resolution = self.get_argument("resolution", "150").strip()
        mode = self.get_argument("mode", "Color").strip()

        # 分辨率白名单校验
        if resolution not in ["75", "100", "150", "200", "300", "600"]:
            resolution = "150"
        
        # 模式白名单校验
        if mode not in ["Color", "Gray", "Lineart"]:
            mode = "Color"

        # 设备名称防注入校验（禁止任何以横杠开头的参数注入）
        if device.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:/=?&]+$', device):
            device = ""

        # 2. 获取硬件并发排他锁
        if not SCAN_LOCK.acquire(blocking=False):
            self.write_json(False, "扫描仪当前正在执行任务，请等待上一个任务完成后再试！")
            return

        try:
            available_devices = enumerate_all_scanners()
            if not available_devices:
                self.write_json(False, "未检测到可用扫描仪，请确认设备已开机并插紧 USB 数据线！")
                return

            valid_ids = [d["id"] for d in available_devices]
            if not device or device not in valid_ids:
                device = available_devices[0]["id"]

            timestamp = int(time.time())
            final_jpg = os.path.join(SCANS_DIR, f"scan_{timestamp}.jpg")
            temp_pnm = os.path.join(SCANS_DIR, f"temp_{timestamp}.pnm")

            env = os.environ.copy()
            env["LANG"] = "C"

            # 方案 A: 纯流式管道极速 JPEG 直出
            cmd_jpeg = [
                "scanimage",
                "-d", device,
                f"--resolution={resolution}",
                f"--mode={mode}",
                "--format=jpeg"
            ]

            print(f"[ScanHandler] 执行硬件扫描作业: {' '.join(cmd_jpeg)}", flush=True)

            is_success = False
            try:
                with open(final_jpg, "wb") as f_out:
                    p = subprocess.Popen(cmd_jpeg, stdout=f_out, stderr=subprocess.PIPE, env=env)
                    _, stderr_data = p.communicate(timeout=60)

                if p.returncode == 0 and os.path.exists(final_jpg) and os.path.getsize(final_jpg) > 0:
                    is_success = True
            except Exception:
                is_success = False

            # 方案 B: 通用 PNM 回退方案（兼容老款设备）
            if not is_success:
                if os.path.exists(final_jpg):
                    try: os.remove(final_jpg)
                    except Exception: pass

                cmd_pnm = [
                    "scanimage",
                    "-d", device,
                    f"--resolution={resolution}",
                    f"--mode={mode}",
                    "--format=pnm"
                ]
                try:
                    with open(temp_pnm, "wb") as f_out:
                        p = subprocess.Popen(cmd_pnm, stdout=f_out, stderr=subprocess.PIPE, env=env)
                        _, stderr_data = p.communicate(timeout=60)

                    if p.returncode == 0 and os.path.exists(temp_pnm) and os.path.getsize(temp_pnm) > 0:
                        try:
                            subprocess.run(["convert", temp_pnm, "-quality", "95", final_jpg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                        except Exception:
                            pass

                        if not os.path.exists(final_jpg):
                            from PIL import Image
                            with Image.open(temp_pnm) as img:
                                img.save(final_jpg, format="JPEG", quality=95)

                        is_success = (os.path.exists(final_jpg) and os.path.getsize(final_jpg) > 0)
                    else:
                        err_msg = stderr_data.decode("utf-8", errors="ignore").strip()
                        self.write_json(False, f"扫描硬件通信失败: {err_msg or '设备握手未响应'}")
                        return
                except Exception as e:
                    self.write_json(False, f"扫描执行异常: {str(e)}")
                    return
                finally:
                    if os.path.exists(temp_pnm):
                        try: os.remove(temp_pnm)
                        except Exception: pass

            if not is_success or not os.path.exists(final_jpg) or os.path.getsize(final_jpg) == 0:
                self.write_json(False, "未能从扫描仪获取到图像数据，请检查 USB 连通状态。")
                return

            out_filename = f"scan_{timestamp}.jpg"
            self.write_json(True, "扫描作业已完成！呈现原件真实细节", data={
                "url": f"/scans/{out_filename}",
                "preview_url": f"/api/scan/preview?file={out_filename}",
                "download_url": f"/api/scan/download?file={out_filename}",
                "filename": out_filename
            })
        finally:
            SCAN_LOCK.release()

def validate_safe_file_path(filename):
    """防路径遍历安全校验工具"""
    if not filename:
        return None
    # 强制取基础文件名，且仅允许字母、数字、下划线、中划线和标准扩展名
    safe_name = os.path.basename(filename)
    if not re.match(r'^[a-zA-Z0-9_\-]+\.(jpg|jpeg|png|pnm|pdf)$', safe_name, re.I):
        return None
    full_path = os.path.abspath(os.path.join(SCANS_DIR, safe_name))
    # 强制校验是否在允许的扫描目录范围内
    if not full_path.startswith(os.path.abspath(SCANS_DIR) + os.sep):
        return None
    if not os.path.exists(full_path):
        return None
    return full_path

class PreviewScanHandler(BaseHandler):
    def get(self):
        file_path = validate_safe_file_path(self.get_argument("file", "").strip())
        if not file_path:
            self.set_status(404)
            self.write("预览文件不存在或请求非法")
            return

        self.set_header("Content-Type", "image/jpeg")
        self.set_header("Cache-Control", "no-cache")
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.write(chunk)
        self.finish()

class DownloadScanHandler(BaseHandler):
    def get(self):
        filename = self.get_argument("file", "").strip()
        file_path = validate_safe_file_path(filename)
        if not file_path:
            self.set_status(404)
            self.write("文件不存在或请求非法")
            return

        self.set_header("Content-Type", "application/octet-stream")
        self.set_header("Content-Disposition", f"attachment; filename={os.path.basename(file_path)}")
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.write(chunk)
        self.finish()
