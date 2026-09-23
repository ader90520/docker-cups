cat << 'EOF' > /tmp/device_handler.py
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

        # 2. 深度获取打印机状态（诊断缺纸、缺墨、卡纸、脱机）
        try:
            res_l = subprocess.run(["lpstat", "-p", "-l"], stdout=subprocess.PIPE, text=True, timeout=3, env=env)
            current_p = None
            p_map = {}

            for line in res_l.stdout.splitlines():
                line_str = line.strip()
                if line.startswith("printer"):
                    parts = line.split()
                    if len(parts) >= 2:
                        current_p = parts[1]
                        status = "空闲就绪"
                        if "now printing" in line or "processing" in line:
                            status = "正在打印"
                        elif "disabled" in line or "paused" in line:
                            status = "暂停"
                        p_map[current_p] = {"name": current_p, "status": status, "alert": ""}
                elif current_p and "Alerts:" in line:
                    alert_raw = line.split("Alerts:")[-1].strip().lower()
                    alerts = []
                    if "media-empty" in alert_raw or "out-of-paper" in alert_raw or "empty" in alert_raw:
                        alerts.append("⚠️ 缺纸")
                    if "media-jam" in alert_raw or "jam" in alert_raw:
                        alerts.append("🚨 机器卡纸")
                    if "toner-low" in alert_raw or "marker-supply-low" in alert_raw:
                        alerts.append("⚠️ 墨粉将尽")
                    if "offline" in alert_raw:
                        alerts.append("🔌 打印机脱机")
                    
                    if alerts:
                        p_map[current_p]["alert"] = " | ".join(alerts)

            for p_name, data in p_map.items():
                display_status = data["status"]
                if data["alert"]:
                    display_status = f"{data['status']} ({data['alert']})"
                
                printers.append({
                    "name": p_name,
                    "id": p_name,
                    "status": display_status,
                    "is_default": (p_name == default_printer),
                    "has_error": bool(data["alert"])
                })

            if not printers:
                res_a = subprocess.run(["lpstat", "-a"], stdout=subprocess.PIPE, text=True, timeout=2, env=env)
                for line in res_a.stdout.splitlines():
                    if line.strip():
                        name = line.split()[0]
                        printers.append({"name": name, "id": name, "status": "空闲就绪", "is_default": (name == default_printer)})
        except Exception as e:
            print(f"[DeviceHandler] 状态探测异常: {e}")

        # 3. 扫描仪探测 (带 60 秒内存缓存)
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
EOF

docker cp /tmp/device_handler.py cups:/opt/webapp/handlers/device_handler.py
