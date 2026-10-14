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

def get_scan_env():
    env = os.environ.copy()
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    env["SANE_CONFIG_DIR"] = "/etc/sane.d"
    # 全面补齐 ARM32 / ARM64 / x86_64 下的驱动动态库搜索路径
    sane_lib_paths = [
        "/usr/lib/sane",
        "/usr/lib/arm-linux-gnueabihf/sane",
        "/usr/lib/aarch64-linux-gnu/sane",
        "/usr/lib/x86_64-linux-gnu/sane",
        "/usr/share/hplip/scan/plugins",
        "/usr/share/hplip/data/plugins"
    ]
    env["LD_LIBRARY_PATH"] = ":".join(sane_lib_paths) + ":" + env.get("LD_LIBRARY_PATH", "")
    return env

def run_cmd(cmd, env=None, timeout=12):
    if env is None:
        env = get_scan_env()
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=timeout)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def enumerate_all_scanners():
    """多级嗅探扫描仪硬件，附带深度排查诊断日志"""
    devices = []
    seen = set()
    diag_logs = []

    # 1. 深度执行 scanimage -L
    ok, out, err = run_cmd(["scanimage", "-L"], timeout=8)
    if ok and out:
        for line in out.splitlines():
            line_s = line.strip()
            if "is a" in line_s:
                m = re.search(r"`([^']+)'\s+is\s+a\s+(.+)", line_s)
                if m:
                    dev_id = m.group(1).strip()
                    dev_name = m.group(2).strip()
                    if dev_id not in seen:
                        devices.append({"id": dev_id, "name": dev_name, "type": "sane"})
                        seen.add(dev_id)
    else:
        if err:
            diag_logs.append(f"scanimage 扫描错误: {err}")

    # 2. 针对 HP 惠普多功能一体机（若标准 SANE 没探测到，触发 hp-probe 探测）
    if not devices:
        ok_hp, out_hp, err_hp = run_cmd(["hp-probe", "-busb"], timeout=8)
        if ok_hp and out_hp:
            for line in out_hp.splitlines():
                if "hp:/" in line:
                    match = re.search(r"(hp:/usb/[^\s]+)", line)
                    if match:
                        hp_uri = match.group(1).strip()
                        hpaio_id = hp_uri.replace("hp:/usb/", "hpaio:/usb/")
                        if hpaio_id not in seen:
                            devices.append({"id": hpaio_id, "name": "HP 多功能一体机 (HPLIP hpaio引擎)", "type": "hp"})
                            seen.add(hpaio_id)
        else:
            if err_hp:
                diag_logs.append(f"hp-probe 探测提示: {err_hp}")

    # 3. 硬件级 USB 原始连通性检查（如果前两步都没识别到）
    usb_status = "未发现任何 USB 打印/扫描设备"
    ok_ls, out_ls, _ = run_cmd(["lsusb"])
    if ok_ls and out_ls:
        has_printer_chip = False
        for line in out_ls.splitlines():
            if any(v in line.lower() for v in ["hewlett", "hp", "epson", "canon", "brother", "printer", "scanner"]):
                has_printer_chip = True
                usb_status = f"USB 物理已连接 ({line.strip()})，但尚未匹配到 SANE 驱动插件"
                break
        if not has_printer_chip:
            usb_status = "USB 总线未读到打印机/扫描仪芯片，请确认数据线已插紧并开机"
    
    return devices, diag_logs, usb_status

def validate_safe_file_path(filename):
    """防路径遍历安全校验工具"""
    if not filename:
        return None
    safe_name = os.path.basename(filename)
    if not re.match(r'^[a-zA-Z0-9_\-]+\.(jpg|jpeg|png|pnm|pdf)$', safe_name, re.I):
        return None
    full_path = os.path.abspath(os.path.join(SCANS_DIR, safe_name))
    if not full_path.startswith(os.path.abspath(SCANS_DIR) + os.sep):
        return None
    if not os.path.exists(full_path):
        return None
    return full_path

class ScanProbeHandler(BaseHandler):
    def get(self):
        devices, diags, usb_status = enumerate_all_scanners()
        # 无论是否为空，都把排查信息附带输出，前端可直接展示排查提示
        msg = "成功检索到扫描仪" if devices else f"未检测到可用扫描仪。硬件状态: {usb_status}"
        self.write_json(True, msg, data={
            "devices": devices,
            "usb_status": usb_status,
            "diags": diags
        })

class ScanHandler(BaseHandler):
    def post(self):
        device = self.get_argument("device", "").strip()
        resolution = self.get_argument("resolution", "150").strip()
        mode = self.get_argument("mode", "Color").strip()

        if resolution not in ["75", "100", "150", "200", "300", "600"]:
            resolution = "150"
        
        if mode not in ["Color", "Gray", "Lineart"]:
            mode = "Color"

        if device.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:/=?&]+$', device):
            device = ""

        if not SCAN_LOCK.acquire(blocking=False):
            self.write_json(False, "扫描仪当前正在执行任务，请等待上一个任务完成后再试！")
            return

        try:
            available_devices, diags, usb_status = enumerate_all_scanners()
            if not available_devices:
                self.write_json(False, f"未检测到可用扫描设备！诊断建议: {usb_status}")
                return

            valid_ids = [d["id"] for d in available_devices]
            if not device or device not in valid_ids:
                device = available_devices[0]["id"]

            timestamp = int(time.time())
            final_jpg = os.path.join(SCANS_DIR, f"scan_{timestamp}.jpg")
            temp_pnm = os.path.join(SCANS_DIR, f"temp_{timestamp}.pnm")

            env = get_scan_env()

            cmd_jpeg = [
                "scanimage",
                "-d", device,
                f"--resolution={resolution}",
                f"--mode={mode}",
                "--format=jpeg"
            ]

            print(f"[ScanHandler] 执行硬件扫描: {' '.join(cmd_jpeg)}", flush=True)

            is_success = False
            last_err = ""
            try:
                with open(final_jpg, "wb") as f_out:
                    p = subprocess.Popen(cmd_jpeg, stdout=f_out, stderr=subprocess.PIPE, env=env)
                    _, stderr_data = p.communicate(timeout=60)

                if p.returncode == 0 and os.path.exists(final_jpg) and os.path.getsize(final_jpg) > 0:
                    is_success = True
                else:
                    last_err = stderr_data.decode("utf-8", errors="ignore").strip()
            except Exception as e:
                is_success = False
                last_err = str(e)

            # 回退通用 PNM 格式
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
                        err_pnm = stderr_data.decode("utf-8", errors="ignore").strip()
                        self.write_json(False, f"扫描硬件通信失败: {err_pnm or last_err or '设备无响应'}")
                        return
                except Exception as e:
                    self.write_json(False, f"扫描执行异常: {str(e)}")
                    return
                finally:
                    if os.path.exists(temp_pnm):
                        try: os.remove(temp_pnm)
                        except Exception: pass

            if not is_success or not os.path.exists(final_jpg) or os.path.getsize(final_jpg) == 0:
                self.write_json(False, f"未能从扫描仪获取到图像。底层报告: {last_err or '数据流为空'}")
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

class ScanPrintHandler(BaseHandler):
    def post(self):
        try:
            filename = self.get_argument("filename", "").strip()
            printer = self.get_argument("printer", "").strip()
            copies = self.get_argument("copies", "1").strip()

            file_path = validate_safe_file_path(filename)
            if not file_path:
                self.write_json(False, "指定的扫描文件不存在或路径非法")
                return

            try:
                copies_int = int(copies)
                if not (1 <= copies_int <= 99):
                    copies_int = 1
            except ValueError:
                copies_int = 1

            # 核心纠偏：统一改用 127.0.0.1:631，彻底杜绝 Unix Socket 权限断开
            env = os.environ.copy()
            env["CUPS_SERVER"] = "127.0.0.1:631"
            env["LANG"] = "C"

            if not printer:
                res_d = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
                for line in res_d.stdout.splitlines():
                    if ":" in line or "：" in line:
                        printer = line.replace("：", ":").split(":")[-1].strip()
                        break

            if not printer:
                res_a = subprocess.run(["lpstat", "-a"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
                lines = res_a.stdout.splitlines()
                if lines:
                    printer = lines[0].split()[0].strip()

            if not printer:
                self.write_json(False, "系统内未发现可用打印机，请先添加打印机")
                return

            cmd = [
                "lp",
                "-d", printer,
                "-n", str(copies_int),
                "-o", "media=A4",
                "-o", "PageSize=A4",
                "-o", "fit-to-page",
                "-o", "scaling=100",
                "-o", "natural-scaling=100",
                "-o", "position=center",
                file_path
            ]

            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=30)
            if res.returncode == 0:
                self.write_json(True, f"扫描件已直接下发至【{printer}】！", job=res.stdout.strip())
            else:
                self.write_json(False, f"CUPS 拒绝打印: {res.stderr.strip()}")
        except subprocess.TimeoutExpired:
            self.write_json(False, "打印任务派发超时，请检查打印机连接状态")
        except Exception as e:
            self.write_json(False, f"直接打印异常: {str(e)}")
