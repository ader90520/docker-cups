#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import subprocess
import json
import tornado.web
from PIL import Image

SCAN_DIR = "/scans"

class DevicesHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        printers, scanners = [], []
        
        try:
            res = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in res.stdout.splitlines():
                if line.startswith("printer"):
                    printers.append(line.split()[1])
        except Exception:
            pass

        try:
            res = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            for line in res.stdout.splitlines():
                if "device `" in line:
                    dev_id = line.split("`")[1].split("'")[0]
                    desc = line.split("' is a ")[-1] if "' is a " in line else dev_id
                    scanners.append({"id": dev_id, "name": desc})
        except Exception:
            pass

        self.write(json.dumps({"printers": printers, "scanners": scanners}))

class DoScanHandler(tornado.web.RequestHandler):
    def post(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            device = data.get("device", "").strip()
            mode = data.get("mode", "Color")
            resolution = data.get("resolution", "150")
            fmt = data.get("format", "pdf").lower()
            action = data.get("action", "scan")
            target_printer = data.get("printer", "")

            timestamp = time.strftime('%Y%m%d_%H%M%S')
            raw_tiff = f"/tmp/scan_{timestamp}.tiff"
            out_filename = f"scan_{timestamp}.{fmt}"
            final_path = os.path.join(SCAN_DIR, out_filename)

            cmd = ["scanimage", "-d", device, f"--mode={mode}", f"--resolution={resolution}", "--format=tiff"]
            with open(raw_tiff, "wb") as f:
                p = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=60)
            
            if p.returncode != 0:
                self.set_status(500)
                self.write(json.dumps({"success": False, "msg": f"扫描失败: {p.stderr.decode('utf-8', errors='ignore')}"}))
                if os.path.exists(raw_tiff): os.remove(raw_tiff)
                return

            im = Image.open(raw_tiff)
            if fmt == "pdf":
                if im.mode in ('RGBA', 'LA'): im = im.convert('RGB')
                im.save(final_path, 'PDF', resolution=float(resolution))
            elif fmt in ["jpg", "jpeg"]:
                im.convert('RGB').save(final_path, 'JPEG', quality=90)
            elif fmt == "png":
                im.save(final_path, 'PNG')
            else:
                os.rename(raw_tiff, final_path)

            if os.path.exists(raw_tiff): os.remove(raw_tiff)

            if action == "copy" and target_printer:
                subprocess.run(["lp", "-d", target_printer, final_path], check=True)

            self.write(json.dumps({
                "success": True, 
                "msg": "复印指令已发送" if action == "copy" else "扫描完成", 
                "filename": out_filename,
                "url": f"/scans/{out_filename}"
            }))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"success": False, "msg": str(e)}))
