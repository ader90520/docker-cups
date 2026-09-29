#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
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
    """解析打印机状态：AirPrint共享标记、缺纸、卡纸、缺墨、耗材余量"""
    status_info = {
        "is_shared": False,
        "media_empty": False,
        "paper_jam": False,
        "toner_low": False,
        "toner_empty": False,
        "state_message": "就绪"
    }

    # 1. 检查是否开启共享 (AirPrint 发现的核心)
    ok, out, _ = run_cmd(["lpoptions", "-p", printer_name])
    if ok and "printer-is-shared=true" in out:
        status_info["is_shared"] = True

    # 2. 检查详细告警状态 (卡纸/缺纸/缺墨)
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
    """全面诊断：USB挂载、Avahi广播、SANE驱动、PPD状态与磁盘健康"""
    issues = []

    # 1. USB 设备节点映射检测
    if not os.path.exists("/dev/bus/usb"):
        issues.append({
            "level": "danger",
            "title": "USB设备节点未映射",
            "detail": "宿主机 /dev/bus/usb 未映射进容器，打印机与扫描仪无法通信。"
        })

    # 2. Avahi 广播多重兼容检测（改用 pgrep 与 ps 复合探测，彻底根治精简系统误报）
    ok_avahi, out_a, _ = run_cmd(["sh", "-c", "pgrep -x avahi-daemon || ps -ef | grep [a]vahi-daemon"])
    if not ok_avahi or not out_a:
        # 尝试静默自愈唤醒一次
        run_cmd(["sh", "-c", "rm -rf /var/run/avahi-daemon/* && avahi-daemon -D 2>/dev/null || service avahi-daemon start 2>/dev/null || true"])
        # 二次核验
        ok_retry, out_r, _ = run_cmd(["sh", "-c", "pgrep -x avahi-daemon || ps -ef | grep [a]vahi-daemon"])
        if not ok_retry or not out_r:
            issues.append({
                "level": "danger",
                "title": "Avahi mDNS 广播未运行",
                "detail": "Avahi 广播服务离线，导致苹果 iPhone/Mac 无法通过隔空打印搜索到设备。"
            })

    # 3. SANE 扫描仪驱动与硬件通信检测 (适配 HP 一体机 hpaio 协议与 USB 物理端口)
    ok_sane, out_s, _ = run_cmd(["scanimage", "-L"])
    has_scanner = ok_sane and out_s and ("No scanners were identified" not in out_s)

    if not has_scanner:
        # 针对 HP 打印扫描一体机 (如 M126a) 进行底层 USB 物理端点探测兜底
        ok_find, out_find, _ = run_cmd(["sane-find-scanner", "-q"])
        if ok_find and "found USB scanner" in out_find:
            has_scanner = True

    if not has_scanner:
        issues.append({
            "level": "warning",
            "title": "未检测到就绪的扫描仪",
            "detail": "SANE 未识别到可用扫描仪。若是 HP M126a 等一体机，请确认 USB 插紧，并确认容器已赋予 --privileged 权限。"
        })

    # 4. PPD 驱动健康度检测
    if os.path.exists(CUPS_PPD_DIR):
        ppds = [f for f in os.listdir(CUPS_PPD_DIR) if f.endswith(".ppd")]
        if not ppds:
            issues.append({
                "level": "warning",
                "title": "未发现已配置的打印机",
                "detail": "当前系统没有任何可用队列，请上传 PPD 驱动或在 631 控制台添加打印机。"
            })

    # 5. 闪存磁盘空间容量诊断
    total, used, free = shutil.disk_usage("/")
    used_pct = int((used / total) * 100)
    if used_pct >= 90:
        issues.append({
            "level": "danger",
            "title": f"系统存储空间爆满告急 ({used_pct}%)",
            "detail": "可用闪存不足，会导致打印任务写入失败、日志卡死，请立即点击上方一键清理！"
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

            # 1. 优先通过 lpstat -a 获取队列
            _, out_a, _ = run_cmd(["lpstat", "-a"], env=env)
            for line in out_a.splitlines():
                parts = line.strip().split()
                if parts:
                    p = parts[0].strip()
                    if p and p not in printers_list:
                        printers_list.append(p)

            # 2. 兜底提取 -p
            if not printers_list:
                _, out_p, _ = run_cmd(["lpstat", "-p"], env=env)
                for line in out_p.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        p = parts[1].strip()
                        if p and p not in printers_list:
                            printers_list.append(p)

            # 3. 提取默认打印机
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
                "toner_low": details["toner_low"],
                "toner_empty": details["toner_empty"],
                "has_error": (details["media_empty"] or details["paper_jam"] or details["toner_empty"])
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
