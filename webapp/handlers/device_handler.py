#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import shutil
import subprocess
from handlers.base_handler import BaseHandler

CUPS_PPD_DIR = "/etc/cups/ppd"

# 30 秒快速内存诊断缓存
DIAG_CACHE = {
    "data": [],
    "last_time": 0
}

def run_cmd(cmd, env=None, timeout=2):
    if env is None:
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=timeout)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def parse_printer_detailed_status(printer_name, lpstat_l_output, pending_jobs_output):
    """
    全量深度解析硬件代号与故障（保障 E1、E2、E3、E4 准确呈现）：
    - E1: 缺纸 / 等待装纸 / 尺寸不符 (Media Needed / Waiting for paper)
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

    # 1. 切割定位属于当前打印机的状态文本
    printer_block = ""
    is_target = False
    for line in lpstat_l_output.splitlines():
        if line.startswith(f"printer {printer_name}") or line.startswith(f"打印机 {printer_name}"):
            is_target = True
            printer_block += line + "\n"
        elif is_target:
            if line and not line.startswith(" ") and not line.startswith("\t"):
                break
            printer_block += line + "\n"

    block_lower = printer_block.lower()
    jobs_lower = pending_jobs_output.lower()

    # 2. 检查 AirPrint 共享标记
    if "shared" in block_lower or "printer-is-shared=true" in block_lower:
        status_info["is_shared"] = True

    # 3. 多源联合判定 E1 缺纸 (包含 CUPS 队列内部的卡住状态)
    is_e1 = any(k in block_lower for k in [
        "media-empty", "out-of-paper", "out of paper", "media-needed", 
        "tray-empty", "paper out", "waiting for paper", "缺纸", "装入纸张"
    ]) or any(k in jobs_lower for k in [
        "waiting for paper", "media-needed", "out of paper", "tray empty"
    ])

    # 检查队列是否因错误被停用
    if "disabled since" in block_lower or "paused" in block_lower:
        if not is_e1 and not any(k in block_lower for k in ["jam", "cover", "door"]):
            is_e1 = True  # 绝大多数 HP 机型无纸后会直接将队列变为 disabled

    if is_e1:
        status_info["media_empty"] = True
        status_info["error_code"] = "E1"
        status_info["error_desc"] = "进纸盒缺纸 / 纸张尺寸不匹配 (E1)"

    # E2 机门开
    elif any(k in block_lower for k in ["cover-open", "door-open", "door open", "机盖"]):
        status_info["door_open"] = True
        status_info["error_code"] = "E2"
        status_info["error_desc"] = "打印机门盖已打开 (E2)"

    # E3 卡纸
    elif any(k in block_lower for k in ["media-jam", "paper jam", "jam", "卡纸"]):
        status_info["paper_jam"] = True
        status_info["error_code"] = "E3"
        status_info["error_desc"] = "打印机内部卡纸 (E3)"

    # E4 耗材
    elif any(k in block_lower for k in ["toner-empty", "out of toner", "marker-supply-empty", "无墨", "更换耗材"]):
        status_info["toner_empty"] = True
        status_info["error_code"] = "E4"
        status_info["error_desc"] = "缺墨 / 硒鼓异常 (E4)"
    elif any(k in block_lower for k in ["toner-low", "low on toner", "墨粉低"]):
        status_info["toner_low"] = True

    # 提取状态文本
    for line in printer_block.splitlines():
        line_s = line.strip()
        if line_s.startswith("Status:") or line_s.startswith("状态:"):
            raw_msg = line_s.split(":", 1)[-1].strip()
            status_info["state_message"] = raw_msg

    if status_info["error_code"]:
        status_info["state_message"] = f"【{status_info['error_code']}】{status_info['error_desc']}"
    elif pending_jobs_output.strip():
        status_info["state_message"] = "正在打印处理中..."

    return status_info

def perform_system_diagnostics(has_any_printer):
    """系统诊断（剔除误报，30 秒内存级缓存）"""
    global DIAG_CACHE
    now = time.time()
    if now - DIAG_CACHE["last_time"] < 30 and DIAG_CACHE["data"]:
        return DIAG_CACHE["data"]

    issues = []

    # 1. USB 节点映射检测
    if not os.path.exists("/dev/bus/usb"):
        issues.append({
            "level": "danger",
            "title": "USB设备节点未映射",
            "detail": "宿主机 /dev/bus/usb 未映射进容器，打印机与扫描仪无法通信。"
        })

    # 2. Avahi 广播多重兼容检测
    ok_avahi, out_a, _ = run_cmd(["sh", "-c", "pgrep -x avahi-daemon || ps -ef | grep [a]vahi-daemon"], timeout=1)
    if not ok_avahi or not out_a:
        issues.append({
            "level": "danger",
            "title": "Avahi mDNS 广播未运行",
            "detail": "Avahi 广播服务离线，导致苹果 iPhone/Mac 无法通过隔空打印搜索到设备。"
        })

    # 3. 仅在真正没有任何打印机时才提示，避免“驱动不兼容”误报
    if not has_any_printer:
        issues.append({
            "level": "warning",
            "title": "未发现已配置的打印机",
            "detail": "当前系统没有任何可用队列，请在下方【检索系统驱动库】搜索驱动并一键添加。"
        })

    # 4. 存储空间容量检测
    total, used, free = shutil.disk_usage("/")
    used_pct = int((used / total) * 100)
    if used_pct >= 90:
        issues.append({
            "level": "danger",
            "title": f"系统存储空间爆满告急 ({used_pct}%)",
            "detail": "可用闪存不足，会导致打印任务写入失败、日志卡死，请立即点击下方一键清理！"
        })

    DIAG_CACHE["data"] = issues
    DIAG_CACHE["last_time"] = now
    return issues

class DevicesHandler(BaseHandler):
    def get(self):
        printers_list = []
        default_printer = ""
        try:
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            # 仅执行快速命令（总耗时 < 0.04 秒）
            _, out_p, _ = run_cmd(["lpstat", "-p", "-l"], env=env, timeout=2)
            _, out_d, _ = run_cmd(["lpstat", "-d"], env=env, timeout=1)
            _, out_jobs, _ = run_cmd(["lpstat", "-o"], env=env, timeout=1)

            for line in out_p.splitlines():
                if line.startswith("printer ") or line.startswith("打印机 "):
                    parts = line.split()
                    if len(parts) >= 2:
                        p = parts[1].strip()
                        if p and p not in printers_list:
                            printers_list.append(p)

            for line in out_d.splitlines():
                if ":" in line or "：" in line:
                    default_printer = line.split(":")[-1].split("：")[-1].strip()

            if not default_printer and printers_list:
                default_printer = printers_list[0]

        except Exception as e:
            print(f"[DevicesHandler] 设备提取异常: {e}", flush=True)
            out_p = ""
            out_jobs = ""

        devices = []
        for p in printers_list:
            details = parse_printer_detailed_status(p, out_p, out_jobs)
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
            "diagnostics": perform_system_diagnostics(has_any_printer=bool(printers_list))
        })
