#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import subprocess
import urllib.parse
from handlers.base_handler import BaseHandler

class PrinterAdminHandler(BaseHandler):
    def get(self):
        action = self.get_argument("action", "").strip()

        # 1. 扫描底层物理 USB 与网络端口（杜绝 %20 乱码与不合规命名）
        if action == "discovered_devices":
            try:
                env = os.environ.copy()
                env["CUPS_SERVER"] = "/run/cups/cups.sock"
                env["LANG"] = "C"

                # 探测 USB 端口及免驱端口
                res = subprocess.run(
                    ["lpinfo", "-v"], 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    text=True, 
                    timeout=8, 
                    env=env
                )
                devices = []

                for line in res.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("direct usb://") or line.startswith("network ") or line.startswith("direct hp:/"):
                        parts = line.split(" ", 1)
                        if len(parts) == 2:
                            uri = parts[1].strip()
                            # 彻底解码 URL 编码字符（如 %20 -> 空格）
                            decoded_uri = urllib.parse.unquote(uri)
                            
                            # 提取清晰友好的物理型号名称（过滤掉协议头与序列号参数）
                            friendly_name = decoded_uri.split("://")[-1].split("?")[0].replace("/", " ").strip()
                            if not friendly_name:
                                friendly_name = decoded_uri

                            devices.append({
                                "uri": uri,
                                "name": friendly_name
                            })

                self.write_json(True, "扫描物理端口成功", data=devices)
            except subprocess.TimeoutExpired:
                self.write_json(False, "扫描物理端口超时，请检查 USB 物理连接")
            except Exception as e:
                self.write_json(False, f"扫描物理端口异常: {str(e)}")

        # 2. 真实查询系统驱动库（对接 631 驱动池，支持关键词搜索）
        elif action == "drivers":
            q = self.get_argument("q", "").strip().lower()
            try:
                env = os.environ.copy()
                env["CUPS_SERVER"] = "/run/cups/cups.sock"
                env["LANG"] = "C"

                res = subprocess.run(
                    ["lpinfo", "-m"], 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    text=True, 
                    timeout=15, 
                    env=env
                )
                drivers = []

                for line in res.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(" ", 1)
                    drv_id = parts[0].strip()
                    drv_name = parts[1].strip() if len(parts) > 1 else drv_id

                    # 关键词匹配，支持如 m126、1020、hplip、foo2zjs 等
                    if not q or (q in drv_id.lower() or q in drv_name.lower()):
                        drivers.append({
                            "id": drv_id,
                            "name": drv_name
                        })
                        if len(drivers) >= 80:  # 限制条目，防前端渲染卡顿
                            break

                self.write_json(True, "检索系统驱动成功", data=drivers)
            except subprocess.TimeoutExpired:
                self.write_json(False, "查询系统驱动库超时")
            except Exception as e:
                self.write_json(False, f"检索驱动异常: {str(e)}")

        else:
            self.write_json(False, "未知操作请求")

    def post(self):
        """执行打印机创建与同步注册至 631 后台"""
        try:
            uri = self.get_argument("uri", "").strip()
            name = self.get_argument("name", "").strip()
            driver = self.get_argument("driver", "").strip()
            ppd_file = self.request.files.get("ppd_file", [])

            if not uri or not name:
                self.write_json(False, "打印机物理端口与名称不能为空！")
                return

            # 安全防护：严格限制打印机系统标识符，杜绝任何命令注入风险
            decoded_name = urllib.parse.unquote(name)
            clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', decoded_name).strip('_')
            if not clean_name:
                clean_name = "Printer_Device"

            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            # 组装标准安全参数列表（严格不使用 shell=True）
            cmd = ["lpadmin", "-p", clean_name, "-v", uri, "-E"]

            if ppd_file:
                # 优先使用上传的自定义 PPD 文件
                ppd_tmp = f"/tmp/{clean_name}.ppd"
                with open(ppd_tmp, "wb") as f:
                    f.write(ppd_file[0]["body"])
                cmd.extend(["-P", ppd_tmp])
            elif driver and driver != "raw":
                cmd.extend(["-m", driver])
            else:
                cmd.extend(["-m", "raw"])

            print(f"[PrinterAdmin] 正在向 631 执行注册: {' '.join(cmd)}")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

            if res.returncode != 0:
                self.write_json(False, f"CUPS 631 拒绝添加: {res.stderr.strip()}")
                return

            # 设为共享、启动打印队列并设置为默认
            subprocess.run(["cupsenable", clean_name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["cupsaccept", clean_name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["lpadmin", "-d", clean_name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            self.write_json(True, f"✔ 打印机【{clean_name}】已成功安装并同步至 631！")
        except Exception as e:
            self.write_json(False, f"添加打印机异常: {str(e)}")
