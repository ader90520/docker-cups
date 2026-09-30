#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shutil
import subprocess
from handlers.base_handler import BaseHandler

CUPS_PPD_DIR = "/etc/cups/ppd"

def run_cmd(cmd, env=None, timeout=8):
    if env is None:
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=timeout)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def parse_printer_detailed_status(printer_name):
    """
    深度捕获硬件代号：
    - E1: 缺纸 / 纸张尺寸不匹配
    - E2: 机盖打开
    - E3: 内部卡纸
    - E4: 缺墨 / 耗材异常
    """
    status_info = {
        "is_shared": False,
        "media_empty": False,
        "paper_jam": False,
        "door_open": False,
        "toner_low": False,
        "toner_empty": False,
        "error_code": "",
        "error_desc": "",
        "state_message": "就绪"
    }

    # 1. 检测 AirPrint 共享
    ok, out, _ = run_cmd(["lpoptions", "-p", printer_name])
    if ok and "printer-is-shared=true" in out:
        status_info["is_shared"] = True

    # 2. 从 lpstat -p [name] -l 抓取
    ok, out, _ = run_cmd(["lpstat", "-p", printer_name, "-l"])
    out_lower = (out or "").lower()

    # 3. 补充从活跃任务排查卡死特征
    _, out_jobs, _ = run_cmd(["lpstat", "-o", printer_name])
    has_pending_jobs = bool(out_jobs.strip())

    # 4. 深度探测 HPLIP 状态 (专门针对 HP M126a 等机型)
    _, out_hp, _ = run_cmd(["hp-info", "-i", "-d", f"hp:/{printer_name}"], timeout=4)
    out_hp_lower = (out_hp or "").lower()

    # E1 缺纸判断（多源联合校验）
    is_e1 = any(k in out_lower for k in ["media-empty", "out of paper", "media-needed", "tray-empty", "paper out", "缺纸"]) or \
            any(k in out_hp_lower for k in ["out of paper", "tray empty", "media empty", "paper-out"])

    if is_e1:
        status_info["media_empty"] = True
        status_info["error_code"] = "E1"
        status_info["error_desc"] = "进纸盒缺纸 / 纸张尺寸不匹配 (E1)"

    # E2 机门打开
    elif any(k in out_lower for k in ["cover-open", "door-open", "door open", "机盖"]) or "door open" in out_hp_lower:
        status_info["door_open"] = True
        status_info["error_code"] = "E2"
        status_info["error_desc"] = "打印机门盖已打开 (E2)"

    # E3 内部卡纸
    elif any(k in out_lower for k in ["media-jam", "paper jam", "jam", "卡纸"]) or "jam" in out_hp_lower:
        status_info["paper_jam"] = True
        status_info["error_code"] = "E3"
        status_info["error_desc"] = "打印机内部卡纸 (E3)"

    # E4 缺墨
    elif any(k in out_lower for k in ["toner-empty", "out of toner", "marker-supply-empty", "无墨", "更换耗材"]):
        status_info["toner_empty"] = True
        status_info["error_code"] = "E4"
        status_info["error_desc"] = "缺墨 / 硒鼓异常 (E4)"

    for line in out.splitlines():
        line_s = line.strip()
        if line_s.startswith("Status:"):
            raw_msg = line_s.replace("Status:", "").strip()
            status_info["state_message"] = raw_msg
            if not status_info["error_code"] and any(w in raw_msg.lower() for w in ["paper", "tray"]):
                status_info["media_empty"] = True
                status_info["error_code"] = "E1"
                status_info["error_desc"] = f"硬件上报: {raw_msg}"

    if status_info["error_code"]:
        status_info["state_message"] = f"【{status_info['error_code']}】{status_info['error_desc']}"
    elif has_pending_jobs:
        status_info["state_message"] = "正在打印处理中..."

    return status_info

def perform_system_diagnostics():
    """系统体检与扫描/广播检测"""
    issues = []

    if not os.path.exists("/dev/bus/usb"):
        issues.append({
            "level": "danger",
            "title": "USB设备节点未映射",
            "detail": "宿主机 /dev/bus/usb 未映射进容器，打印机与扫描仪无法通信。"
        })

    # Avahi 广播检测与快速拉起
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

    # SANE 扫描仪硬件与驱动多重链路检测
    has_scanner = False
    ok_sane, out_s, _ = run_cmd(["scanimage", "-L"], timeout=5)
    if ok_sane and out_s and ("No scanners were identified" not in out_s):
        has_scanner = True

    if not has_scanner:
        ok_hp, out_hp, _ = run_cmd(["hp-probe", "-busb"], timeout=5)
        if ok_hp and ("hp:" in out_hp or "hpaio" in out_hp):
            has_scanner = True

    if not has_scanner:
        ok_find, out_find, _ = run_cmd(["sane-find-scanner", "-q"], timeout=5)
        if ok_find and "found USB scanner" in out_find:
            has_scanner = True

    if not has_scanner:
        issues.append({
            "level": "warning",
            "title": "未检测到就绪的扫描仪",
            "detail": "SANE 未能识别 USB 扫描端点（若打印机正处于 E1 缺纸阻塞状态，请先加纸并点击【复位USB通信】）。"
        })

    # 存储容量
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
