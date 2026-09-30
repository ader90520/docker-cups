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
    """
    深度解析打印机状态与硬件故障代号：
    - E1: 缺纸 / 纸张尺寸不匹配 (Out of Paper / Media Needed)
    - E2: 机盖打开 (Cover / Door Open)
    - E3: 内部卡纸 (Paper Jam)
    - E4: 缺墨 / 硒鼓异常 (Toner Empty / Missing)
    """
    status_info = {
        "is_shared": False,
        "media_empty": False,
        "paper_jam": False,
        "door_open": False,
        "toner_low": False,
        "toner_empty": False,
        "error_code": "",      # 硬件代号 (E1, E2, E3, E4)
        "error_desc": "",      # 故障中文说明
        "state_message": "就绪"
    }

    # 1. 检查是否开启共享 (AirPrint 隔空打印发现的核心属性)
    ok, out, _ = run_cmd(["lpoptions", "-p", printer_name])
    if ok and "printer-is-shared=true" in out:
        status_info["is_shared"] = True

    # 2. 深入解析 CUPS 底层详细状态与错误原因
    ok, out, _ = run_cmd(["lpstat", "-p", printer_name, "-l"])
    if ok:
        out_lower = out.lower()

        # E1: 缺纸 / 纸张尺寸不匹配
        if any(k in out_lower for k in ["media-empty", "out of paper", "media-needed", "tray-empty", "offline", "缺纸"]):
            status_info["media_empty"] = True
            status_info["error_code"] = "E1"
            status_info["error_desc"] = "进纸盒缺纸 / 纸张尺寸不匹配 (E1)"

        # E2: 机门盖打开
        if any(k in out_lower for k in ["cover-open", "door-open", "door open", "机盖"]):
            status_info["door_open"] = True
            status_info["error_code"] = "E2"
            status_info["error_desc"] = "打印机门盖已打开 (E2)"

        # E3: 内部卡纸
        if any(k in out_lower for k in ["media-jam", "paper jam", "jam", "卡纸"]):
            status_info["paper_jam"] = True
            status_info["error_code"] = "E3"
            status_info["error_desc"] = "打印机内部卡纸 (E3)"

        # E4: 耗材缺墨或未安装
        if any(k in out_lower for k in ["toner-empty", "out of toner", "marker-supply-empty", "无墨", "更换耗材"]):
            status_info["toner_empty"] = True
            status_info["error_code"] = "E4"
            status_info["error_desc"] = "缺墨 / 硒鼓异常 (E4)"
        elif any(k in out_lower for k in ["toner-low", "low on toner", "墨粉低"]):
            status_info["toner_low"] = True

        for line in out.splitlines():
            line_s = line.strip()
            if line_s.startswith("Status:"):
                raw_msg = line_s.replace("Status:", "").strip()
                status_info["state_message"] = raw_msg
                # 从原始上报消息中进一步校验缺纸特征
                if any(w in raw_msg.lower() for w in ["paper", "tray", "media"]):
                    if not status_info["error_code"]:
                        status_info["media_empty"] = True
                        status_info["error_code"] = "E1"
                        status_info["error_desc"] = f"硬件上报: {raw_msg}"

    return status_info

def perform_system_diagnostics():
    """全面体检诊断：USB设备节点、Avahi广播服务、SANE扫描驱动、PPD状态与磁盘健康"""
    issues = []

    # 1. USB 设备节点映射检测
    if not os.path.exists("/dev/bus/usb"):
        issues.append({
            "level": "danger",
            "title": "USB设备节点未映射",
            "detail": "宿主机 /dev/bus/usb 未映射进容器，打印机与扫描仪无法通信。"
        })

    # 2. Avahi 广播多重兼容检测（避免进程名截断误报，附带自愈探测）
    ok_avahi, out_a, _ = run_cmd(["sh", "-c", "pgrep -x avahi-daemon || ps -ef | grep [a]vahi-daemon"])
    if not ok_avahi or not out_a:
        # 尝试唤醒一次
        run_cmd(["sh", "-c", "rm -rf /var/run/avahi-daemon/* && avahi-daemon -D 2>/dev/null || service avahi-daemon start 2>/dev/null || true"])
        ok_retry, out_r, _ = run_cmd(["sh", "-c", "pgrep -x avahi-daemon || ps -ef | grep [a]vahi-daemon"])
        if not ok_retry or not out_r:
            issues.append({
                "level": "danger",
                "title": "Avahi mDNS 广播未运行",
                "detail": "Avahi 广播服务离线，导致苹果 iPhone/Mac 无法通过隔空打印搜索到设备。"
            })

    # 3. SANE 扫描仪硬件与驱动多重链路检测 (强化对 HP M126a 复合一体机的识别)
    has_scanner = False
    ok_sane, out_s, _ = run_cmd(["scanimage", "-L"])
    if ok_sane and out_s and ("No scanners were identified" not in out_s):
        has_scanner = True

    if not has_scanner:
        # HP 专有 hpaio 底层探测
        ok_hp, out_hp, _ = run_cmd(["hp-probe", "-busb"])
        if ok_hp and ("hp:" in out_hp or "hpaio" in out_hp):
            has_scanner = True

    if not has_scanner:
        # 底层 USB 物理描述符探测兜底
        ok_find, out_find, _ = run_cmd(["sane-find-scanner", "-q"])
        if ok_find and "found USB scanner" in out_find:
            has_scanner = True

    if not has_scanner:
        issues.append({
            "level": "warning",
            "title": "未检测到就绪的扫描仪",
            "detail": "SANE 未能识别 USB 扫描设备。若已插入 HP 一体机，请确认 USB 已插紧并支持 SANE/HPLIP 驱动。"
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

    # 5. 存储空间容量预警 (防海纳思闪存写满)
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

            # 1. 优先通过 lpstat -a 提取队列名称
            res_a = subprocess.run(["lpstat", "-a"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
            for line in res_a.stdout.splitlines():
                parts = line.strip().split()
                if parts:
                    p = parts[0].strip()
                    if p and p not in printers_list:
                        printers_list.append(p)

            # 2. 如果 -a 未取到，通过 lpstat -p 兜底
            if not printers_list:
                res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
                for line in res_p.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        p = parts[1].strip()
                        if p and p not in printers_list:
                            printers_list.append(p)

            # 3. 提取默认打印机
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
                "door_open": details["door_open"],
                "toner_low": details["toner_low"],
                "toner_empty": details["toner_empty"],
                "error_code": details["error_code"],       # 返回 E1/E2/E3/E4
                "error_desc": details["error_desc"],       # 返回中文说明
                "has_error": bool(details["error_code"])   # 标记是否有活跃硬件报警
            })

        # 磁盘空间监控 (防海纳思闪存写满)
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
