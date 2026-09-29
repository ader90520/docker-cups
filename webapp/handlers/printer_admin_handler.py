#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shutil
import subprocess
import urllib.parse
from handlers.base_handler import BaseHandler, UPLOAD_DIR

# 虚拟后端与传输协议黑名单（彻底剔除无意义的系统协议项）
IGNORED_BACKENDS = {
    "beh", "ipps", "https", "http", "ipp", "socket", "lpd", 
    "smb", "scsi", "serial", "parallel", "cups-brf", "implicitclass"
}

MODEL_DIR = "/usr/share/cups/model"

class PrinterAdminHandler(BaseHandler):
    def get(self):
        action = self.get_argument("action", "").strip()

        # 1. 扫描底层物理端口（只展示真实物理连接的打印机型号）
        if action == "discovered_devices":
            try:
                env = os.environ.copy()
                env["CUPS_SERVER"] = "/run/cups/cups.sock"
                env["LANG"] = "C"

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
                    if not line or " " not in line:
                        continue

                    parts = line.split(" ", 1)
                    uri = parts[1].strip()

                    # 提取协议头（如 usb, hp, hpaio 等）
                    scheme = uri.split("://")[0].split(":")[0].lower()

                    # 拦截并过滤所有虚拟协议和空协议项
                    if scheme in IGNORED_BACKENDS or uri.endswith("://") or uri.endswith(":/"):
                        continue

                    decoded_uri = urllib.parse.unquote(uri)

                    friendly_name = ""
                    if "://" in decoded_uri:
                        path_part = decoded_uri.split("://")[-1].split("?")[0]
                        friendly_name = path_part.replace("/", " ").replace("_", " ").strip()
                    elif ":/" in decoded_uri:
                        path_part = decoded_uri.split(":/")[-1].split("?")[0]
                        friendly_name = path_part.replace("/", " ").replace("_", " ").strip()
                    
                    if not friendly_name:
                        friendly_name = decoded_uri

                    if friendly_name.lower() in IGNORED_BACKENDS:
                        continue

                    devices.append({
                        "uri": uri,
                        "name": friendly_name
                    })

                self.write_json(True, "扫描物理端口成功", data=devices)
            except subprocess.TimeoutExpired:
                self.write_json(False, "扫描物理端口超时")
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

                    if not q or (q in drv_id.lower() or q in drv_name.lower()):
                        drivers.append({
                            "id": drv_id,
                            "name": drv_name
                        })
                        if len(drivers) >= 80:
                            break

                self.write_json(True, "检索系统驱动成功", data=drivers)
            except subprocess.TimeoutExpired:
                self.write_json(False, "查询系统驱动库超时")
            except Exception as e:
                self.write_json(False, f"检索驱动异常: {str(e)}")

        else:
            self.write_json(False, "未知操作请求")

    def post(self):
        action = self.get_argument("action", "").strip()
        printer = self.get_argument("printer", "").strip()

        # 安全防注入校验：防止命令注入与参数篡改
        if printer and (printer.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:+]+$', printer)):
            self.write_json(False, "非法打印机设备名称")
            return

        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        # 分支 1: 切换局域网与隔空打印(AirPrint)共享并重启 Avahi
        if action == "toggle_share":
            if not printer:
                self.write_json(False, "未指定打印机名称")
                return
            enable_str = self.get_argument("shared", "true").lower()
            is_share = enable_str in ["true", "1", "yes"]
            flag = "true" if is_share else "false"

            try:
                res = subprocess.run(["lpadmin", "-p", printer, "-o", f"printer-is-shared={flag}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                # 重启 avahi 广播服务以立即生效
                subprocess.run(["service", "avahi-daemon", "restart"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode == 0:
                    msg = f"已{'开启' if is_share else '关闭'} [{printer}] 的局域网与隔空打印(AirPrint)共享！"
                    self.write_json(True, msg)
                else:
                    self.write_json(False, f"设置共享失败: {res.stderr.strip()}")
            except Exception as e:
                self.write_json(False, f"设置共享异常: {str(e)}")
            return

        # 分支 2: 设为默认打印机
        elif action == "set_default":
            if not printer:
                self.write_json(False, "未指定打印机名称")
                return
            try:
                res = subprocess.run(["lpadmin", "-d", printer], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                if res.returncode == 0:
                    self.write_json(True, f"已将 [{printer}] 设置为系统默认打印机")
                else:
                    self.write_json(False, f"设置默认失败: {res.stderr.strip()}")
            except Exception as e:
                self.write_json(False, f"设置默认异常: {str(e)}")
            return

        # 分支 3: 打印测试页
        elif action == "test_page":
            if not printer:
                self.write_json(False, "未指定打印机名称")
                return
            try:
                test_file = "/usr/share/cups/data/testprint"
                if os.path.exists(test_file):
                    cmd = ["lp", "-d", printer, test_file]
                else:
                    cmd = ["lp", "-d", printer, "-o", "media=A4", "/etc/issue"]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                if res.returncode == 0:
                    self.write_json(True, f"测试页已成功发送至 {printer}")
                else:
                    self.write_json(False, f"下发测试页失败: {res.stderr.strip()}")
            except Exception as e:
                self.write_json(False, f"打印测试页异常: {str(e)}")
            return

        # 分支 4: 一键清空卡死任务与打印队列
        elif action == "cancel_all":
            try:
                subprocess.run(["cancel", "-a"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                self.write_json(True, "等待中的打印队列已全部清空")
            except Exception as e:
                self.write_json(False, f"清空队列异常: {str(e)}")
            return

        # 分支 5: 上传 PPD 驱动并同步至 631 驱动库
        elif action == "upload_ppd":
            files = self.request.files.get("ppd_file", [])
            if not files:
                self.write_json(False, "未收到上传的 PPD 驱动文件")
                return

            ppd = files[0]
            fname = os.path.basename(ppd["filename"])
            if not fname.lower().endswith(".ppd"):
                self.write_json(False, "仅支持标准 .ppd 格式驱动文件")
                return

            try:
                os.makedirs(MODEL_DIR, exist_ok=True)
                save_path = os.path.join(MODEL_DIR, fname)
                with open(save_path, "wb") as f:
                    f.write(ppd["body"])

                os.chmod(save_path, 0o644)
                # 刷新 CUPS 驱动库缓存，使 631 后台添加打印机时即可直接选择
                subprocess.run(["cupsfilter", "-m", "application/vnd.cups-raw", save_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.write_json(True, f"驱动 [{fname}] 已成功安装到系统驱动库！在 631 后台添加打印机时即可直接选取。")
            except Exception as e:
                self.write_json(False, f"安装驱动异常: {str(e)}")
            return

        # 分支 6: 一键深度清理闪存垃圾与临时文件
        elif action == "clean_disk":
            try:
                shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                subprocess.run(["sh", "-c", "rm -rf /tmp/mail_* /tmp/cups_* /tmp/*.pdf /tmp/*.jpg /tmp/*.png 2>/dev/null || true"])
                self.write_json(True, "临时打印缓存与垃圾文件已成功清理完成，闪存空间已释放！")
            except Exception as e:
                self.write_json(False, f"清理异常: {str(e)}")
            return

        # 分支 7: 创建并注册打印机至 631 后台
        try:
            uri = self.get_argument("uri", "").strip()
            name = self.get_argument("name", "").strip()
            driver = self.get_argument("driver", "").strip()
            ppd_file = self.request.files.get("ppd_file", [])

            if not uri or not name:
                self.write_json(False, "打印机物理端口与名称不能为空！")
                return

            decoded_name = urllib.parse.unquote(name)
            clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', decoded_name).strip('_')
            if not clean_name:
                clean_name = "Printer_Device"

            cmd = ["lpadmin", "-p", clean_name, "-v", uri, "-E"]
            ppd_tmp = ""

            if ppd_file:
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

            # 清理临时 PPD 文件防磁盘残留
            if ppd_tmp and os.path.exists(ppd_tmp):
                try:
                    os.remove(ppd_tmp)
                except Exception:
                    pass

            if res.returncode != 0:
                self.write_json(False, f"CUPS 631 拒绝添加: {res.stderr.strip()}")
                return

            subprocess.run(["cupsenable", clean_name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["cupsaccept", clean_name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["lpadmin", "-d", clean_name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            self.write_json(True, f"✔ 打印机【{clean_name}】已成功安装并同步至 631！")
        except Exception as e:
            self.write_json(False, f"添加打印机异常: {str(e)}")
