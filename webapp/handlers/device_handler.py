#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
from handlers.base_handler import BaseHandler

class DeviceHandler(BaseHandler):
    def get(self):
        printers = self._get_printers()
        scanners = self._get_scanners()
        # 兼容各前端字段读取习惯 (devices, printers, scanners)
        self.write_json(
            success=True,
            printers=printers,
            devices=printers,
            scanners=scanners,
            data={"printers": printers, "scanners": scanners}
        )

    def _get_printers(self):
        printers = []
        default_printer = ""
        try:
            res_def = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, text=True, timeout=5)
            for line in res_def.stdout.splitlines():
                if "destination:" in line:
                    default_printer = line.split("destination:")[-1].strip()
        except Exception:
            pass

        try:
            res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, text=True, timeout=5)
            for line in res_p.stdout.splitlines():
                if line.startswith("printer"):
                    parts = line.split()
                    if len(parts) >= 2:
                        p_name = parts[1]
                        status = "idle"
                        if "now printing" in line or "processing" in line:
                            status = "busy"
                        elif "disabled" in line or "paused" in line:
                            status = "paused"
                        printers.append({
                            "name": p_name,
                            "id": p_name,
                            "status": status,
                            "is_default": (p_name == default_printer)
                        })
        except Exception:
            pass
        return printers

    def _get_scanners(self):
        scanners = []
        try:
            res_s = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, text=True, timeout=5)
            for line in res_s.stdout.splitlines():
                line = line.strip()
                if line.startswith("device"):
                    parts = line.split("`")
                    if len(parts) >= 2:
                        dev_id = parts[1].split("'")[0]
                        desc = parts[1].split("' is a ")[-1] if "' is a " in parts[1] else dev_id
                        scanners.append({"id": dev_id, "name": desc})
        except Exception:
            pass
        return scanners
