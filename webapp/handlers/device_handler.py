#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shutil
import subprocess
from handlers.base_handler import BaseHandler

CUPS_PPD_DIR = "/etc/cups/ppd"

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
    深度解析硬件状态与错误代号：
    - E1: 缺纸 / 纸张尺寸不匹配 (Out of Paper / Media Needed)
    - E2: 机盖打开 (Door Open)
    - E3: 卡纸 (Paper Jam)
    - E4 / Toner: 缺墨 / 硒鼓异常
    """
    status_info = {
        "is_shared": False,
        "media_empty": False,
        "paper_jam": False,
        "door_open": False,
        "toner_low": False,
        "toner_empty": False,
        "error_code": "",      # 硬件专属代号 (如 E1, E2, E3)
        "error_desc": "",      # 具体原因提示
        "state_message": "就绪"
    }

    # 1. 检查是否开启共享 (AirPrint 发现的核心)
    ok, out, _ = run_cmd(["lpoptions", "-p", printer_name])
    if ok and "printer-is-shared=true" in out:
        status_info["is_shared"] = True

    # 2. 检查详细告警状态 (解析 cups reasons)
    ok, out, _ = run_cmd(["lpstat", "-p", printer_name, "-l"])
    if ok:
        out_lower = out.lower()

        # E1 缺纸判断
        if any(k in out_lower for k in ["media-empty", "out of paper", "media-needed", "tray-empty", "缺纸"]):
            status_info["media_empty"] = True
            status_info["error_code"] = "E1"
            status_info["error_desc"] = "进纸盒缺纸 / 纸张尺寸不符 (E1)"

        # E2 门盖开启判断
        if any(k in out_lower for k in ["cover-open", "door-open", "door open", "机盖"]):
            status_info["door_open"] = True
            status_info["error_code"] = "E2"
            status_info["error_desc"] = "机盖已打开 (E2)"

        # E3 卡纸判断
        if any(k in out_lower for k in ["media-jam", "paper jam", "jam", "卡纸"]):
            status_info["paper_jam"] = True
            status_info["error_code"] = "E3"
            status_info["error_desc"] = "内部卡纸 (E3)"

        # E4 缺墨/耗材告警
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
                # 如果 message 里面有明确纸张提示
                if "paper" in raw_msg.lower() or "tray" in raw_msg.lower():
                    if not status_info["error_code"]:
                        status_info["media_empty"] = True
                        status_info["error_code"] = "E1"
                        status_info["error_desc"] = f"硬件上报: {raw_msg}"

    return status_info

def perform_system_diagnostics():
    """全面诊断：USB设备、Avahi广播、SANE驱动、PPD状态与磁盘健康"""
    issues = []

    # 1. USB 节点映射检测
    if not os.path.exists("/dev/bus/usb"):
        issues.append({
            "level": "danger",
            "title": "USB设备节点未映射",
            "detail": "宿主机 /dev/bus/usb 未映射进容器，打印机与扫描仪无法通信。"
        })

    # 2. Avahi 广播多重兼容检测
    ok_avahi, out_a, _ = run_cmd(["sh", "-c", "pgrep -x avahi-daemon || ps -ef | grep [a]vahi-daemon"])
    if not ok_avahi or not out_a:
        run_cmd(["sh", "-c", "rm -rf /var/run/avahi-daemon/* && avahi-daemon -D 2>/dev/null || service avahi-daemon start 2>/dev/null || true"])
        ok_retry, out_r, _ = run_cmd(["sh", "-c", "pgrep -x avahi-daemon || ps -ef | grep [a]vahi-daemon"])
        if not ok_retry or not out_r:
            issues.append({
                "level": "danger",
                "title": "Avahi mDNS 广播未运行",
                "detail": "Avahi 广播服务离线，导致苹果 iPhone/Mac 无法通过隔空打印搜索到设备。"
            })

    # 3. SANE 扫描仪硬件与驱动全链路探测 (适配 HP M126a 复合机通信)
    has_scanner = False
    ok_sane, out_s, _ = run_cmd(["scanimage", "-L"])
    if ok_sane and out_s and ("No scanners were identified" not in out_s):
        has_scanner = True

    if not has_scanner:
        # HP 专有底层协议探测
        ok_hp, out_hp, _ = run_cmd(["hp-probe", "-busb"])
        if ok_hp and ("hp:" in out_hp or "hpaio" in out_hp):
            has_scanner = True

    if not has_scanner:
        # 物理端口硬件识别兜底
        ok_find, out_find, _ = run_cmd(["sane-find-scanner", "-q"])
        if ok_find and "found USB scanner" in out_find:
            has_scanner = True

    if not has_scanner:
        issues.append({
            "level": "warning",
            "title": "未检测到就绪的扫描仪",
            "detail": "SANE 未识别到可用扫描端点。若已插入 HP 一体机，请在 631 或终端运行一次 hp-setup，并确认 USB 权限充足。"
        })

    # 4. PPD 驱动健康度检测
    if os.path.exists(CUPS_PPD_DIR):
        ppds = [f for f in os.listdir(CUPS_PPD_DIR) if f.endswith(".ppd")]
        if not ppds:
            issues.append({
                "level": "warning",
                "title": "未发现已配置的打印机",
                "detail": "当前系统没有任何可用队列，请上传 PPD 驱动或在下方搜索驱动添加。"
            })

    # 5. 存储空间容量诊断
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

            _, out_a, _ = run_cmd(["lpstat", "-a"], env=env)
            for line in out_a.splitlines():
                parts = line.strip().split()
                if parts:
                    p = parts[0].strip()
                    if p and p not in printers_list:
                        printers_list.append(p)

            if not printers_list:
                _, out_p, _ = run_cmd(["lpstat", "-p"], env=env)
                for line in out_p.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        p = parts[1].strip()
                        if p and p not in printers_list:
                            printers_list.append(p)

            _, out_d, _ = run_cmd(["lpstat", "-d"], env=env)
            for line in out_d.splitlines():
                if ":" in line or "：" in line:
                    default_printer = line.split(":")[-1].split("：")[-1].strip()

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
                "error_code": details["error_code"],
                "error_desc": details["error_desc"],
                "has_error": bool(details["error_code"])
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
