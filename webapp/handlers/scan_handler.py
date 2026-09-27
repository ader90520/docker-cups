#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import uuid
import subprocess
from PIL import Image
from handlers.base_handler import BaseHandler

SCAN_DIR = "/scans"
os.makedirs(SCAN_DIR, exist_ok=True)

class ScanHandler(BaseHandler):
    def get(self):
        """设备枚举与推荐排序（优先排布免驱与专用驱动）"""
        try:
            subprocess.run(["chmod", "-R", "666", "/dev/bus/usb"], stderr=subprocess.DEVNULL)

            env = os.environ.copy()
            env["SANE_CONFIG_DIR"] = "/etc/sane.d"
            res = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10, env=env)
            output = res.stdout.strip()
            devices = []

            for line in output.splitlines():
                if "device `" in line:
                    dev_id = line.split("`")[1].split("'")[0]
                    desc = line.split("is a")[-1].strip() if "is a" in line else dev_id
                    friendly_name = desc.replace("all-in-one", "").replace("Hewlett-Packard", "HP").strip()

                    priority = 5
                    backend_tag = dev_id.split(":")[0].lower()
                    if "hpljm1005" in backend_tag:
                        priority = 1
                        friendly_name += " (推荐·专用后端)"
                    elif "escl" in backend_tag or "airscan" in backend_tag:
                        priority = 2
                        friendly_name += " (推荐·免驱后端)"
                    elif "genesys" in backend_tag or "pixma" in backend_tag:
                        priority = 3
                    elif "hpaio" in backend_tag:
                        priority = 10
                        friendly_name += " (HPLIP·若报I/O错误请切换)"

                    devices.append({
                        "id": dev_id,
                        "name": friendly_name,
                        "backend": backend_tag,
                        "priority": priority
                    })

            devices.sort(key=lambda x: x["priority"])
            self.write_json(True, "扫描仪检测成功", data={"devices": devices, "raw": output})
        except Exception as e:
            self.write_json(False, f"探测扫描仪异常: {str(e)}")

    def post(self):
        """采用原生流管道重定向，规避 hp-scan 依赖"""
        try:
            device = self.get_argument("device", "").strip()
            resolution = self.get_argument("resolution", "200").strip()
            mode = self.get_argument("mode", "Color").strip()
            out_format = self.get_argument("format", "jpg").strip().lower()
            copy_print = self.get_argument("copy_print", "0").strip()
            printer = self.get_argument("printer", "").strip()

            token = uuid.uuid4().hex[:8]
            timestamp = int(time.time())
            tmp_pnm = os.path.join(SCAN_DIR, f"temp_{timestamp}_{token}.pnm")
            target_file = os.path.join(SCAN_DIR, f"scan_{timestamp}_{token}.{out_format}")

            cmd = ["scanimage", "--resolution", str(resolution), "--mode", mode]
            if device:
                cmd.extend(["-d", device])

            print(f"[ScanHandler] 执行 SANE 扫描管道: {' '.join(cmd)}")

            env = os.environ.copy()
            env["SANE_CONFIG_DIR"] = "/etc/sane.d"

            with open(tmp_pnm, "wb") as f_out:
                res = subprocess.run(cmd, stdout=f_out, stderr=subprocess.PIPE, timeout=120, env=env)

            err_output = res.stderr.decode("utf-8", errors="ignore").strip()

            if res.returncode != 0 or not os.path.exists(tmp_pnm) or os.path.getsize(tmp_pnm) == 0:
                if os.path.exists(tmp_pnm):
                    os.remove(tmp_pnm)
                
                if "error during device i/o" in err_output.lower() or "code=9" in err_output.lower():
                    err_msg = "SANE 硬件 I/O 通信失败。若使用的是 HP M1005 等老款机型，请切换为 [hpljm1005:] 或 [escl:] 后端！"
                else:
                    err_msg = err_output or "扫描仪未返回数据，请检查盖板与电源。"

                self.write_json(False, f"扫描中断: {err_msg}")
                return

            with Image.open(tmp_pnm) as img:
                if out_format == "pdf":
                    img.convert("RGB").save(target_file, format="PDF", resolution=float(resolution))
                elif out_format == "png":
                    img.save(target_file, format="PNG")
                else:
                    img.convert("RGB").save(target_file, format="JPEG", quality=92)

            if os.path.exists(tmp_pnm):
                os.remove(tmp_pnm)

            print(f"[ScanHandler] ✔ 扫描完成并生成交付文件: {target_file}")

            copy_job = ""
            if copy_print == "1":
                lp_env = os.environ.copy()
                lp_env["CUPS_SERVER"] = "/run/cups/cups.sock"
                lp_cmd = ["lp"]
                if printer:
                    lp_cmd.extend(["-d", printer])
                lp_cmd.extend(["-o", "media=A4", "-o", "fit-to-page", target_file])
                lp_res = subprocess.run(lp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=lp_env)
                if lp_res.returncode == 0:
                    copy_job = lp_res.stdout.strip()
                    print(f"[ScanHandler] ✔ 自动复印下发: {copy_job}")

            self.write_json(
                True, 
                "扫描完成", 
                filename=os.path.basename(target_file), 
                url=f"/download/scan/{os.path.basename(target_file)}", 
                copy_job=copy_job
            )
        except subprocess.TimeoutExpired:
            self.write_json(False, "扫描仪响应超时，扫描头可能卡阻或未合上盖板。")
        except Exception as e:
            self.write_json(False, f"扫描执行异常: {str(e)}")

class ScanProbeHandler(BaseHandler):
    """设备轻量级快速探测接口"""
    def get(self):
        device = self.get_argument("device", "").strip()
        if not device:
            self.write_json(False, "未指定探测设备")
            return

        env = os.environ.copy()
        env["SANE_CONFIG_DIR"] = "/etc/sane.d"

        cmd = ["scanimage", "-d", device, "-A"]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5, env=env)
            err = res.stderr.lower()
            if "error during device i/o" in err or res.returncode != 0:
                self.write_json(
                    False, 
                    "该后端通信故障 (Error during device I/O)。若当前为 hpaio:，请换用 hpljm1005: 或 escl: 后端！"
                )
                return
            self.write_json(True, "后端通信正常，硬件就绪")
        except subprocess.TimeoutExpired:
            self.write_json(False, "设备握手超时，请检查 USB 连接。")
        except Exception as e:
            self.write_json(False, f"探测异常: {str(e)}")

class DownloadScanHandler(BaseHandler):
    def get(self, filename):
        filepath = os.path.join(SCAN_DIR, filename)
        if not os.path.exists(filepath):
            self.set_status(404)
            self.write("文件不存在")
            return

        ext = filename.split(".")[-1].lower()
        content_types = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "pdf": "application/pdf"
        }
        self.set_header("Content-Type", content_types.get(ext, "application/octet-stream"))
        with open(filepath, "rb") as f:
            self.write(f.read())
