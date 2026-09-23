#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import subprocess
from handlers.base_handler import BaseHandler

class DeviceHandler(BaseHandler):
    """
    负责获取当前已在 CUPS 中添加并启用的打印机与扫描仪设备列表
    强制 LANG=C 保证输出格式一致，不受容器内中文环境干扰
    """
    def get(self):
        printers = []
        default_printer = ""
        
        # 强制指定英文环境，防止中文字符串导致正则或 startswith 匹配落空
        env = os.environ.copy()
        env["LANG"] = "C"
        env["LC_ALL"] = "C"
        env["CUPS_SERVER"] = "/run/cups/cups.sock"

        # 1. 获取默认打印机队列
        try:
            res_def = subprocess.run(
                ["lpstat", "-d"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True, 
                timeout=5,
                env=env
            )
            for line in res_def.stdout.splitlines():
                if "destination:" in line:
                    default_printer = line.split("destination:")[-1].strip()
        except Exception:
            pass

        # 2. 获取所有已配置的打印机（双重容错：lpstat -p 与 lpstat -a）
        try:
            # 优先使用 lpstat -p 抓取状态
            res_p = subprocess.run(
                ["lpstat", "-p"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True, 
                timeout=5,
                env=env
            )
            for line in res_p.stdout.splitlines():
                line = line.strip()
                if line.startswith("printer") or line.startswith("打印机"):
                    parts = line.split()
                    if len(parts) >= 2:
                        p_name = parts[1]
                        status = "就绪"
                        if "now printing" in line or "processing" in line or "正在打印" in line:
                            status = "打印中"
                        elif "disabled" in line or "paused" in line or "禁用" in line or "暂停" in line:
                            status = "暂停"
                        printers.append({
                            "name": p_name,
                            "id": p_name,
                            "status": status,
                            "is_default": (p_name == default_printer)
                        })

            # 兜底机制：若 lpstat -p 未获取到，调用 lpstat -a 获取首列队列名
            if not printers:
                res_a = subprocess.run(
                    ["lpstat", "-a"], 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    text=True, 
                    timeout=5,
                    env=env
                )
                for line in res_a.stdout.splitlines():
                    line = line.strip()
                    if line:
                        p_name = line.split()[0]
                        printers.append({
                            "name": p_name,
                            "id": p_name,
                            "status": "就绪",
                            "is_default": (p_name == default_printer)
                        })
        except Exception as e:
            print(f"[DeviceHandler] 解析异常: {e}")

        # 3. 扫描仪探测 (带超时防护)
        scanners = []
        try:
            res_s = subprocess.run(
                ["scanimage", "-L"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True, 
                timeout=5
            )
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

        # 多重字段兼容返回
        self.write_json(
            success=True,
            printers=printers,
            devices=printers,
            scanners=scanners,
            data={"printers": printers, "scanners": scanners}
        )
