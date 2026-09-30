#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import subprocess
from handlers.base_handler import BaseHandler

SCANS_DIR = "/scans"
os.makedirs(SCANS_DIR, exist_ok=True)

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
        ok_ls, out_ls, _ = run_cmd(["lsusb"])
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
        device = self.get_argument("device", "").strip()
        resolution = self.get_argument("resolution", "150").strip()
        mode = self.get_argument("mode", "Color").strip()

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

        # 方案 A: 极简纯管道输出 (Scanservjs 同款无锁调用)
        cmd_jpeg = [
            "scanimage",
            "-d", device,
            f"--resolution={resolution}",
            f"--mode={mode}",
            "--format=jpeg"
        ]

        print(f"[ScanHandler] 执行硬件扫描 (设备: {device}): {' '.join(cmd_jpeg)}", flush=True)

        is_success = False
        try:
            with open(final_jpg, "wb") as f_out:
                p = subprocess.Popen(cmd_jpeg, stdout=f_out, stderr=subprocess.PIPE, env=env)
                _, stderr_data = p.communicate(timeout=60)

            if p.returncode == 0 and os.path.exists(final_jpg) and os.path.getsize(final_jpg) > 0:
                is_success = True
        except Exception:
            is_success = False

        # 方案 B: 针对不支持 jpeg 直出的老旧一体机 (如 M1005)，自动回退通用 PNM 无损转码
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

class PreviewScanHandler(BaseHandler):
    def get(self):
        filename = os.path.basename(self.get_argument("file", "").strip())
        file_path = os.path.join(SCANS_DIR, filename)

        if not os.path.exists(file_path):
            self.set_status(404)
            self.write("预览文件不存在")
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
        filename = os.path.basename(self.get_argument("file", "").strip())
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
