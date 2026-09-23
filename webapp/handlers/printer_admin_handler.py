#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import subprocess
from handlers.base_handler import BaseHandler

class PrinterAdminHandler(BaseHandler):
    """
    负责 8088 端口扫描物理设备、检索 631 驱动库以及添加/删除打印机
    与 CUPS 631 底层完全同步
    """
    def set_cups_env(self):
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        return env

    def get(self):
        action = self.get_argument("action", "devices")
        env = self.set_cups_env()

        # 扫描 USB / 网络物理打印机
        if action == "discovered_devices":
            devices = []
            try:
                res = subprocess.run(["lpinfo", "-v"], stdout=subprocess.PIPE, text=True, timeout=8, env=env)
                for line in res.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("direct usb://") or line.startswith("network") or line.startswith("direct"):
                        parts = line.split(" ", 1)
                        if len(parts) == 2:
                            uri = parts[1].strip()
                            devices.append({
                                "uri": uri,
                                "name": uri.split("://")[-1].replace("/", " ")
                            })
            except Exception:
                pass
            self.write_json(True, data=devices)

        # 获取 CUPS 系统安装的驱动库
        elif action == "drivers":
            keyword = self.get_argument("q", "").lower()
            drivers = []
            try:
                res = subprocess.run(["lpinfo", "-m"], stdout=subprocess.PIPE, text=True, timeout=10, env=env)
                count = 0
                for line in res.stdout.splitlines():
                    parts = line.split(" ", 1)
                    if len(parts) == 2:
                        drv_id = parts[0].strip()
                        drv_name = parts[1].strip()
                        if keyword:
                            if keyword in drv_id.lower() or keyword in drv_name.lower():
                                drivers.append({"id": drv_id, "name": drv_name})
                                count += 1
                        else:
                            if any(k in drv_name.lower() for k in ["hp", "epson", "canon", "generic", "foo2zjs"]):
                                drivers.append({"id": drv_id, "name": drv_name})
                                count += 1
                        if count >= 100:
                            break
            except Exception:
                pass
            self.write_json(True, data=drivers)

    def post(self):
        env = self.set_cups_env()
        name = self.get_argument("name", "").strip().replace(" ", "_")
        uri = self.get_argument("uri", "").strip()
        driver = self.get_argument("driver", "").strip()

        if not name or not uri:
            self.write_json(False, "打印机名称和设备连接地址不能为空")
            return

        try:
            cmd = ["lpadmin", "-p", name, "-v", uri, "-E"]
            if driver and driver != "raw":
                cmd.extend(["-m", driver])
            else:
                cmd.extend(["-m", "raw"])

            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15, env=env)
            if res.returncode != 0:
                self.write_json(False, f"CUPS 添加失败: {res.stderr.strip()}")
                return

            subprocess.run(["cupsenable", name], env=env)
            subprocess.run(["cupsaccept", name], env=env)

            is_default = self.get_argument("is_default", "0")
            if is_default == "1":
                subprocess.run(["lpadmin", "-d", name], env=env)

            self.write_json(True, f"打印机 [{name}] 已成功添加并同步至 631 后台！")
        except Exception as e:
            self.write_json(False, f"添加执行异常: {str(e)}")
