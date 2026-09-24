#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import subprocess
from handlers.base_handler import BaseHandler

CACHED_SCANNERS = []
LAST_SCAN_TIME = 0

class DeviceHandler(BaseHandler):
    def get(self):
        global CACHED_SCANNERS, LAST_SCAN_TIME
        printers = []
        default_printer = ""
        
        # 强制指定英文环境，防止输出格式变异
        env = os.environ.copy()
        env["LANG"] = "C"
        env["LC_ALL"] = "C"
        env["CUPS_SERVER"] = "/run/cups/cups.sock"

        # 1. 获取默认打印机
        try:
            res_def = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, text=True, timeout=2, env=env)
            for line in res_def.stdout.splitlines():
                if "destination:" in line:
                    default_printer = line.split("destination:")[-1].strip()
        except Exception:
            pass

        # 2. 获取所有打印机队列名 (双重保险：先 lpstat -a 必定能拿到队列名)
        p_names = []
        try:
            res_a = subprocess.run(["lpstat", "-a"], stdout=subprocess.PIPE, text=True, timeout=2, env=env)
            for line in res_a.stdout.splitlines():
                line = line.strip()
                if line:
                    p_names.append(line.split()[0])
        except Exception:
            pass

        # 如果 lpstat -a 没取到，尝试 lpstat -p
        if not p_names:
            try:
                res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, text=True, timeout=2, env=env)
                for line in res_p.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("printer") or line.startswith("打印机"):
                        parts = line.split()
                        if len(parts) >= 2:
                            p_names.append(parts[1])
            except Exception:
                pass

        # 3. 为每个打印机检测详细状态与告警 (缺纸、卡纸、脱机)
        for p in set(p_names):
            status = "就绪"
            has_error = False
            alerts = []

            try:
                res_detail = subprocess.run(["lpstat", "-p", p, "-l"], stdout=subprocess.PIPE, text=True, timeout=2, env=env)
                out = res_detail.stdout.lower()
                
                if "now printing" in out or "processing" in out:
                    status = "正在打印"
                elif "disabled" in out or "paused" in out:
                    status = "已暂停"

                if "media-empty" in out or "out-of-paper" in out:
                    alerts.append("⚠️ 缺纸")
                    has_error = True
                if "media-jam" in out or "jam" in out:
                    alerts.append("🚨 卡纸")
                    has_error = True
                if "toner-low" in out or "marker-supply-low" in out:
                    alerts.append("⚠️ 墨粉将尽")
                if "offline" in out or "not connected" in out:
                    alerts.append("🔌 脱机")
                    has_error = True
            except Exception:
                pass

            display_status = status
            if alerts:
                display_status = f"{status} ({' | '.join(alerts)})"

            printers.append({
                "name": p,
                "id": p,
                "status": display_status,
                "is_default": (p == default_printer),
                "has_error": has_error
            })

        # 4. 扫描仪探测 (带 60 秒内存缓存，避免拖慢网页)
        now = time.time()
        scanners = CACHED_SCANNERS
        if now - LAST_SCAN_TIME > 60:
            try:
                res_s = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, text=True, timeout=1.2)
                temp = []
                for line in res_s.stdout.splitlines():
                    if line.strip().startswith("device"):
                        parts = line.split("`")
                        if len(parts) >= 2:
                            dev_id = parts[1].split("'")[0]
                            desc = parts[1].split("' is a ")[-1] if "' is a " in parts[1] else dev_id
                            temp.append({"id": dev_id, "name": desc})
                CACHED_SCANNERS = temp
                scanners = temp
                LAST_SCAN_TIME = now
            except Exception:
                LAST_SCAN_TIME = now

        self.write_json(True, printers=printers, devices=printers, scanners=scanners, data={"printers": printers, "scanners": scanners})
