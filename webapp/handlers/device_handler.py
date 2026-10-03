#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import glob
import time
import shutil
import subprocess
from handlers.base_handler import BaseHandler

CUPS_PPD_DIR = "/etc/cups/ppd"

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

def read_ieee1284_raw_status():
    raw_status = ""
    try:
        for dev_name in os.listdir("/sys/class/usblp") if os.path.exists("/sys/class/usblp") else []:
            status_file = os.path.join("/sys/class/usblp", dev_name, "device", "ieee1284_id")
            if os.path.exists(status_file):
                try:
                    with open(status_file, "r", encoding="latin-1") as f:
                        raw_status += f.read() + " "
                except Exception:
                    pass
    except Exception:
        pass
    return raw_status.lower()

def parse_printer_detailed_status(printer_name, lpstat_l_output, pending_jobs_output, lpstat_p_output):
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
    p_lower = lpstat_p_output.lower()
    raw_1284 = read_ieee1284_raw_status()

    if "shared" in block_lower or "printer-is-shared=true" in block_lower:
        status_info["is_shared"] = True

    is_e1 = (
        "paper_empty" in raw_1284 or "out_of_paper" in raw_1284 or
        any(k in block_lower for k in ["media-empty", "out-of-paper", "out of paper", "media-needed", "tray-empty", "paper out", "waiting for paper", "缺纸", "装入纸张"]) or
        any(k in jobs_lower for k in ["waiting for paper", "media-needed", "out of paper", "tray empty", "load paper"]) or
        any(k in p_lower for k in ["media-empty", "out of paper", "offline", "paused", "disabled"])
    )

    is_e2 = "door_open" in raw_1284 or any(k in block_lower for k in ["cover-open", "door-open", "door open", "机盖"])
    is_e3 = "jam" in raw_1284 or any(k in block_lower for k in ["media-jam", "paper jam", "jam", "卡纸"])
    is_e4 = any(k in block_lower for k in ["toner-empty", "out of toner", "marker-supply-empty", "无墨", "更换耗材"])

    if is_e1:
        status_info["media_empty"] = True
        status_info["error_code"] = "E1"
        status_info["error_desc"] = "进纸盒缺纸 / 纸张尺寸不匹配 (E1)"
    elif is_e2:
        status_info["door_open"] = True
        status_info["error_code"] = "E2"
        status_info["error_desc"] = "打印机门盖已打开 (E2)"
    elif is_e3:
        status_info["paper_jam"] = True
        status_info["error_code"] = "E3"
        status_info["error_desc"] = "打印机内部卡纸 (E3)"
    elif is_e4:
        status_info["toner_empty"] = True
        status_info["error_code"] = "E4"
        status_info["error_desc"] = "缺墨 / 硒鼓异常 (E4)"

    if any(k in block_lower for k in ["toner-low", "low on toner", "墨粉低"]):
        status_info["toner_low"] = True

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
    global DIAG_CACHE
    now = time.time()
    if now - DIAG_CACHE["last_time"] < 30 and DIAG_CACHE["data"]:
        return DIAG_CACHE["data"]

    issues = []

    if not os.path.exists("/dev/bus/usb"):
        issues.append({
            "level": "danger",
            "title": "USB设备节点未映射",
            "detail": "宿主机 /dev/bus/usb 未映射进容器，打印机与扫描仪无法通信。"
        })

    ok_avahi, out_a, _ = run_cmd(["sh", "-c", "pgrep -x avahi-daemon || ps -ef | grep [a]vahi-daemon"], timeout=1)
    if not ok_avahi or not out_a:
        issues.append({
            "level": "danger",
            "title": "Avahi mDNS 广播未运行",
            "detail": "Avahi 广播服务离线，导致苹果 iPhone/Mac 无法通过隔空打印搜索到设备。"
        })

    if not has_any_printer:
        issues.append({
            "level": "warning",
            "title": "未发现已配置的打印机",
            "detail": "当前系统没有任何可用队列，请在下方【检索系统驱动库】搜索驱动并一键添加。"
        })

    DIAG_CACHE["data"] = issues
    DIAG_CACHE["last_time"] = now
    return issues

class DevicesHandler(BaseHandler):
    def get(self):
        printers_list = []
        default_printer = ""
        queue_count = 0
        completed_jobs = []

        try:
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            _, out_l, _ = run_cmd(["lpstat", "-p", "-l"], env=env, timeout=2)
            _, out_p, _ = run_cmd(["lpstat", "-p"], env=env, timeout=1)
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

            # 1. 统计当前等待队列任务数
            for line in out_jobs.splitlines():
                if line.strip():
                    queue_count += 1

            # 2. 统计最近已完成打印历史记录（截取最新 10 条）
            _, out_comp, _ = run_cmd(["lpstat", "-W", "completed", "-o"], env=env, timeout=2)
            for line in reversed(out_comp.splitlines()[-10:]):
                line_s = line.strip()
                if line_s:
                    parts = line_s.split()
                    completed_jobs.append({
                        "job_id": parts[0] if len(parts) > 0 else "N/A",
                        "user": parts[1] if len(parts) > 1 else "local",
                        "time": " ".join(parts[3:6]) if len(parts) >= 6 else "已完成"
                    })

        except Exception as e:
            print(f"[DevicesHandler] 设备提取异常: {e}", flush=True)
            out_l = ""
            out_p = ""
            out_jobs = ""

        devices = []
        for p in printers_list:
            details = parse_printer_detailed_status(p, out_l, out_jobs, out_p)
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

        # 精简响应数据，仅保留核心的三项指标及诊断信息
        self.write_json(True, "", data={
            "printers": devices,
            "default": default_printer,
            "diagnostics": perform_system_diagnostics(has_any_printer=bool(printers_list)),
            "queue_count": queue_count,
            "completed_jobs": completed_jobs
        })
