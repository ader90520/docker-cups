#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shutil
import subprocess
import tornado.ioloop
import tornado.web

from handlers.base_handler import BaseHandler, UPLOAD_DIR
from handlers.print_handler import PrintHandler
from handlers.idcard_handler import IDCardHandler
from handlers.scan_handler import ScanHandler, ScanProbeHandler, DownloadScanHandler
from handlers.printer_admin_handler import PrinterAdminHandler
from handlers.mail_handler import MailConfigHandler

class IndexRedirectHandler(tornado.web.RequestHandler):
    def head(self):
        self.redirect("/index.html")

    def get(self):
        self.redirect("/index.html")

def run_cmd(cmd, env=None):
    if env is None:
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=10)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def parse_printer_detailed_status(printer_name):
    """解析打印机状态：AirPrint共享标记、缺纸、卡纸、缺墨"""
    status_info = {
        "is_shared": False,
        "media_empty": False,
        "paper_jam": False,
        "toner_low": False,
        "toner_empty": False,
        "state_message": "就绪"
    }

    # 1. 检查是否开启共享 (AirPrint 广播核心属性)
    ok, out, _ = run_cmd(["lpoptions", "-p", printer_name])
    if ok and "printer-is-shared=true" in out:
        status_info["is_shared"] = True

    # 2. 检查详细告警状态
    ok, out, _ = run_cmd(["lpstat", "-p", printer_name, "-l"])
    if ok:
        out_lower = out.lower()
        if any(k in out_lower for k in ["media-empty", "out of paper", "offline", "缺纸"]):
            status_info["media_empty"] = True
        if any(k in out_lower for k in ["media-jam", "paper jam", "jam", "卡纸"]):
            status_info["paper_jam"] = True
        if any(k in out_lower for k in ["toner-low", "low on toner", "墨粉低"]):
            status_info["toner_low"] = True
        if any(k in out_lower for k in ["toner-empty", "out of toner", "无墨", "更换耗材"]):
            status_info["toner_empty"] = True

        for line in out.splitlines():
            line_s = line.strip()
            if line_s.startswith("Status:"):
                status_info["state_message"] = line_s.replace("Status:", "").strip()

    return status_info

def perform_system_diagnostics():
    """全面体检诊断：USB设备、Avahi广播、SANE驱动、PPD状态与磁盘健康"""
    issues = []

    # 1. USB 节点检测
    if not os.path.exists("/dev/bus/usb"):
        issues.append({
            "level": "danger",
            "title": "USB设备节点未映射",
            "detail": "宿主机 /dev/bus/usb 未映射进容器，打印机与扫描仪无法通信。"
        })

    # 2. Avahi 广播多重兼容检测（避免 pidof 截断进程名误报）
    ok_avahi, out_a, _ = run_cmd(["sh", "-c", "pgrep avahi-daemon || ps -ef | grep [a]vahi-daemon"])
    if not ok_avahi or not out_a:
        issues.append({
            "level": "danger",
            "title": "Avahi mDNS 广播未运行",
            "detail": "Avahi 广播服务离线，导致苹果 iPhone/Mac 无法通过隔空打印搜索到设备。"
        })

    # 3. SANE 扫描仪驱动检测 (支持 HP 一体机探测)
    ok_sane, out_s, _ = run_cmd(["scanimage", "-L"])
    if not ok_sane or "No scanners were identified" in out_s or not out_s:
        issues.append({
            "level": "warning",
            "title": "未检测到就绪的扫描仪",
            "detail": "SANE 未能识别 USB 扫描设备，请确认 USB 已插紧并支持 SANE/HPLIP。"
        })

    # 4. PPD 驱动健康度检测
    cups_ppd_dir = "/etc/cups/ppd"
    if os.path.exists(cups_ppd_dir):
        ppds = [f for f in os.listdir(cups_ppd_dir) if f.endswith(".ppd")]
        if not ppds:
            issues.append({
                "level": "warning",
                "title": "未发现已配置的打印机",
                "detail": "当前系统没有任何可用队列，请上传 PPD 驱动或在 631 控制台添加打印机。"
            })

    # 5. 存储空间容量预警
    total, used, free = shutil.disk_usage("/")
    used_pct = int((used / total) * 100)
    if used_pct >= 90:
        issues.append({
            "level": "danger",
            "title": f"系统存储空间爆满告急 ({used_pct}%)",
            "detail": "可用闪存不足，会导致打印任务写入失败、日志卡死，请立即点击下方一键清理！"
        })

    return issues

class DevicesHandler(BaseHandler):
    def get(self):
        printers_list = []
        default_printer = ""
        try:
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            res_a = subprocess.run(["lpstat", "-a"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
            for line in res_a.stdout.splitlines():
                parts = line.strip().split()
                if parts:
                    p = parts[0].strip()
                    if p and p not in printers_list:
                        printers_list.append(p)

            if not printers_list:
                res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
                for line in res_p.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        p = parts[1].strip()
                        if p and p not in printers_list:
                            printers_list.append(p)

            res_d = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
            for line in res_d.stdout.splitlines():
                if "：" in line:
                    default_printer = line.split("：")[-1].strip()
                elif ":" in line:
                    default_printer = line.split(":")[-1].strip()

            if not default_printer and printers_list:
                default_printer = printers_list[0]

        except Exception as e:
            print(f"[DevicesHandler] 设备提取异常: {e}", flush=True)

        devices = []
        for p in printers_list:
            details = parse_printer_detailed_status(p)
            devices.append({
                "name": p,
                "status": details["state_message"],
                "status_msg": details["state_message"],
                "is_default": (p == default_printer),
                "is_shared": details["is_shared"],
                "media_empty": details["media_empty"],
                "paper_jam": details["paper_jam"],
                "toner_low": details["toner_low"],
                "toner_empty": details["toner_empty"],
                "has_error": (details["media_empty"] or details["paper_jam"] or details["toner_empty"])
            })

        total, used, free = shutil.disk_usage("/")
        disk_info = {
            "total_gb": round(total / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "used_pct": int((used / total) * 100)
        }

        self.write_json(True, "", data={
            "printers": devices,
            "default": default_printer,
            "disk": disk_info,
            "diagnostics": perform_system_diagnostics()
        })

def make_app():
    static_path = os.path.join(os.path.dirname(__file__), "static")
    return tornado.web.Application([
        (r"/", IndexRedirectHandler),
        (r"/api/devices", DevicesHandler),
        (r"/api/print", PrintHandler),
        (r"/api/idcard", IDCardHandler),
        (r"/api/scan", ScanHandler),
        (r"/api/scan/devices", ScanProbeHandler),
        (r"/api/scan/probe", ScanProbeHandler),
        (r"/api/scan/download", DownloadScanHandler),
        (r"/api/printer_admin", PrinterAdminHandler),
        (r"/api/mail/config", MailConfigHandler),
        (r"/api/mail_config", MailConfigHandler),
        (r"/(.*)", tornado.web.StaticFileHandler, {"path": static_path, "default_filename": "index.html"}),
    ],
    autoreload=False,
    debug=False)

if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    app = make_app()
    app.listen(8088, address="0.0.0.0")
    print("[Server] CUPS Web 服务已启动，监听端口 0.0.0.0:8088...", flush=True)
    tornado.ioloop.IOLoop.current().start()
