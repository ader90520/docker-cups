#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import subprocess
import tornado.web

class DeviceHandler(tornado.web.RequestHandler):
    """
    设备探测处理器：
    向 8088 页面提供局域网 CUPS 打印队列和 SANE 扫描仪列表
    """
    def set_default_headers(self):
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.set_header("Access-Control-Allow-Origin", "*")

    def get(self):
        printers = self._get_printers()
        scanners = self._get_scanners()
        
        self.write({
            "success": True,
            "printers": printers,
            "scanners": scanners,
            "devices": printers  # 兼容部分前端旧接口取 devices 字段
        })

    def _get_printers(self):
        """解析系统 CUPS 打印队列与运行状态"""
        printers = []
        default_printer = ""

        # 获取默认打印机
        try:
            res_def = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            for line in res_def.stdout.splitlines():
                if "destination:" in line:
                    default_printer = line.split("destination:")[-1].strip()
        except Exception:
            pass

        # 获取所有打印机及其状态
        try:
            res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
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
                            "status": status,
                            "is_default": (p_name == default_printer)
                        })
        except Exception as e:
            print(f"[DeviceHandler] 获取打印机异常: {e}")

        return printers

    def _get_scanners(self):
        """解析 SANE 扫描仪硬件列表"""
        scanners = []
        try:
            res_s = subprocess.run(["scanimage", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
            for line in res_s.stdout.splitlines():
                line = line.strip()
                if line.startswith("device"):
                    # 格式形如: device `hpaio:/usb/...' is a Hewlett-Packard LaserJet flatbed scanner
                    parts = line.split("`")
                    if len(parts) >= 2:
                        dev_id = parts[1].split("'")[0]
                        desc = parts[1].split("' is a ")[-1] if "' is a " in parts[1] else dev_id
                        scanners.append({
                            "id": dev_id,
                            "name": desc
                        })
        except Exception as e:
            print(f"[DeviceHandler] 获取扫描仪异常: {e}")

        return scanners
