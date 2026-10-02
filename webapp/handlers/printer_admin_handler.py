#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import glob
import time
import shutil
import threading
import subprocess
import urllib.parse
from handlers.base_handler import BaseHandler, UPLOAD_DIR

IGNORED_BACKENDS = {
    "beh", "ipps", "https", "http", "ipp", "socket", "lpd", 
    "smb", "scsi", "serial", "parallel", "cups-brf", "implicitclass"
}

MODEL_DIR = "/usr/share/cups/model"

def perform_cleanup():
    """静默清理过期临时文件"""
    try:
        if os.path.exists(UPLOAD_DIR):
            for item in os.listdir(UPLOAD_DIR):
                item_p = os.path.join(UPLOAD_DIR, item)
                try:
                    if os.path.isfile(item_p) or os.path.islink(item_p):
                        os.remove(item_p)
                    elif os.path.isdir(item_p):
                        shutil.rmtree(item_p, ignore_errors=True)
                except Exception:
                    pass
        subprocess.run(["sh", "-c", "rm -rf /tmp/mail_* /tmp/cups_* /tmp/*.pdf /tmp/*.jpg /tmp/*.png /tmp/*.run /tmp/hp-plugin-* 2>/dev/null || true"])
    except Exception as e:
        print(f"[AutoClean] 定时清理异常: {e}", flush=True)

def daily_cleanup_daemon():
    while True:
        time.sleep(86400)
        perform_cleanup()

threading.Thread(target=daily_cleanup_daemon, daemon=True).start()

class PrinterAdminHandler(BaseHandler):
    def get(self):
        action = self.get_argument("action", "").strip()

        if action == "discovered_devices":
            try:
                env = os.environ.copy()
                env["CUPS_SERVER"] = "/run/cups/cups.sock"
                env["LANG"] = "C"

                res = subprocess.run(["lpinfo", "-v"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=12, env=env)
                devices = []
                seen_uris = set()

                for line in res.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue

                    # lpinfo -v 标准格式: direct uri "MAKE MODEL" "INFO"
                    # 正则捕获类别、完整 URI 以及双引号中的厂商型号信息
                    m = re.match(r'^([a-zA-Z0-9_\-]+)\s+(\S+)(?:\s+"([^"]+)")?(?:\s+"([^"]+)")?', line)
                    if not m:
                        continue

                    dev_type = m.group(1).lower()
                    uri = m.group(2).strip()
                    desc_model = m.group(3) or ""
                    desc_info = m.group(4) or ""

                    # 严格过滤虚拟设备与非物理协议
                    scheme = uri.split("://")[0].split(":")[0].lower()
                    if scheme in IGNORED_BACKENDS or uri.endswith("://") or uri.endswith(":/"):
                        continue
                    if any(uri.startswith(bad) for bad in ["cups-brf:", "hp-fax:", "beh:", "implicitclass:"]):
                        continue

                    # 锁定真实 USB 或 HP 通信端口
                    if uri.startswith("hp:/usb/") or uri.startswith("usb://") or dev_type in ["direct"]:
                        friendly_name = desc_info or desc_model
                        if not friendly_name:
                            decoded_uri = urllib.parse.unquote(uri)
                            if "model=" in decoded_uri:
                                friendly_name = decoded_uri.split("model=")[-1].split("&")[0].replace("+", " ")
                            elif decoded_uri.startswith("hp:/usb/"):
                                path_part = decoded_uri.split("hp:/usb/")[-1].split("?")[0]
                                friendly_name = path_part.replace("/", " ").replace("_", " ").strip()
                            elif "://" in decoded_uri:
                                path_part = decoded_uri.split("://")[-1].split("?")[0]
                                friendly_name = path_part.replace("/", " ").replace("_", " ").strip()
                            elif ":/" in decoded_uri:
                                path_part = decoded_uri.split(":/")[-1].split("?")[0]
                                friendly_name = path_part.replace("/", " ").replace("_", " ").strip()

                        if not friendly_name:
                            friendly_name = urllib.parse.unquote(uri)

                        friendly_name = friendly_name.strip()

                        # 过滤掉协议短词 "hp", "usb", "direct" 假值
                        if uri not in seen_uris and uri not in ["hp", "usb", "direct"]:
                            devices.append({"uri": uri, "name": friendly_name})
                            seen_uris.add(uri)

                self.write_json(True, "扫描物理端口成功", data=devices)
            except Exception as e:
                self.write_json(False, f"扫描物理端口异常: {str(e)}")

        elif action == "drivers":
            q = self.get_argument("q", "").strip().lower()
            try:
                env = os.environ.copy()
                env["CUPS_SERVER"] = "/run/cups/cups.sock"
                env["LANG"] = "C"

                res = subprocess.run(["lpinfo", "-m"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20, env=env)
                drivers = []
                for line in res.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(" ", 1)
                    drv_id = parts[0].strip()
                    drv_name = parts[1].strip() if len(parts) > 1 else drv_id

                    if not q:
                        if any(k in drv_id.lower() or k in drv_name.lower() for k in ["hplip", "foo2zjs", "laserjet", "series"]):
                            drivers.append({"id": drv_id, "name": drv_name})
                    else:
                        if q in drv_id.lower() or q in drv_name.lower():
                            drivers.append({"id": drv_id, "name": drv_name})

                    if len(drivers) >= 80:
                        break

                self.write_json(True, "检索系统驱动成功", data=drivers)
            except Exception as e:
                self.write_json(False, f"检索驱动异常: {str(e)}")
        else:
            self.write_json(False, "未知操作请求")

    def post(self):
        action = self.get_argument("action", "").strip()
        printer = self.get_argument("printer", "").strip()

        # 防参数注入
        if printer and (printer.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:+]+$', printer)):
            self.write_json(False, "非法打印机设备名称")
            return

        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"

        if action == "resume_printer":
            if not printer:
                self.write_json(False, "未指定打印机名称")
                return
            try:
                pjl_signal = b"\x1b%-12345X@PJL\r\n@PJL RESET\r\n@PJL CONTINUE\r\n\x1b%-12345X"
                for dev in glob.glob("/dev/usb/lp*"):
                    try:
                        with open(dev, "wb") as f:
                            f.write(pjl_signal)
                    except Exception:
                        pass
                subprocess.run(["cupsenable", "-c", printer], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                subprocess.run(["cupsaccept", printer], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                if os.path.exists("/dev/bus/usb"):
                    subprocess.run(["chmod", "-R", "666", "/dev/bus/usb"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.write_json(True, f"已向【{printer}】下发远程恢复指令！已模拟按下物理恢复键。")
            except Exception as e:
                self.write_json(False, f"远程恢复失败: {str(e)}")
            return

        elif action == "reset_usb":
            try:
                subprocess.run(["cancel", "-a"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                subprocess.run(["cupsenable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                subprocess.run(["cupsaccept"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                if os.path.exists("/dev/bus/usb"):
                    subprocess.run(["chmod", "-R", "666", "/dev/bus/usb"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["hp-probe", "-busb"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.write_json(True, "USB 通信与驱动锁已成功复位！")
            except Exception as e:
                self.write_json(False, f"复位异常: {str(e)}")
            return

        elif action == "toggle_share":
            if not printer:
                self.write_json(False, "未指定打印机名称")
                return
            enable_str = self.get_argument("shared", "true").lower()
            is_share = enable_str in ["true", "1", "yes"]
            flag = "true" if is_share else "false"
            try:
                res = subprocess.run(["lpadmin", "-p", printer, "-o", f"printer-is-shared={flag}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                subprocess.run(["service", "avahi-daemon", "restart"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode == 0:
                    self.write_json(True, f"已{'开启' if is_share else '关闭'} [{printer}] 的局域网与隔空打印(AirPrint)共享！")
                else:
                    self.write_json(False, f"设置共享失败: {res.stderr.strip()}")
            except Exception as e:
                self.write_json(False, f"设置共享异常: {str(e)}")
            return

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

        elif action == "test_page":
            if not printer:
                self.write_json(False, "未指定打印机名称")
                return
            try:
                test_file = "/usr/share/cups/data/testprint"
                cmd = ["lp", "-d", printer, test_file] if os.path.exists(test_file) else ["lp", "-d", printer, "-o", "media=A4", "/etc/issue"]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                if res.returncode == 0:
                    self.write_json(True, f"测试页已发送至 {printer}")
                else:
                    self.write_json(False, f"下发测试页失败: {res.stderr.strip()}")
            except Exception as e:
                self.write_json(False, f"测试页异常: {str(e)}")
            return

        elif action == "cancel_all":
            try:
                subprocess.run(["cancel", "-a"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                self.write_json(True, "等待中的打印队列已全部清空")
            except Exception as e:
                self.write_json(False, f"清空队列异常: {str(e)}")
            return

        elif action == "upload_ppd":
            files = self.request.files.get("ppd_file", [])
            if not files:
                self.write_json(False, "未收到上传的 PPD 驱动文件")
                return
            ppd = files[0]
            fname = os.path.basename(ppd["filename"])
            if not re.match(r'^[a-zA-Z0-9_\.\-]+\.ppd$', fname, re.I):
                self.write_json(False, "仅支持标准 .ppd 格式驱动文件，且文件名不能包含特殊字符")
                return
            try:
                os.makedirs(MODEL_DIR, exist_ok=True)
                save_path = os.path.join(MODEL_DIR, fname)
                with open(save_path, "wb") as f:
                    f.write(ppd["body"])
                os.chmod(save_path, 0o644)
                subprocess.run(["cupsfilter", "-m", "application/vnd.cups-raw", save_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.write_json(True, f"驱动 [{fname}] 已成功安装到系统驱动库！")
            except Exception as e:
                self.write_json(False, f"安装驱动异常: {str(e)}")
            return

        elif action == "upload_scanner_plugin":
            files = self.request.files.get("plugin_file", [])
            if not files:
                self.write_json(False, "未收到上传的插件安装包")
                return
            upload_file = files[0]
            fname = os.path.basename(upload_file["filename"])
            if not re.match(r'^[a-zA-Z0-9_\.\-]+\.run$', fname, re.I):
                self.write_json(False, "仅支持 HP 官方扫描插件包 (*-plugin.run)")
                return
            
            tmp_plugin_path = f"/tmp/{fname}"
            extract_dir = "/tmp/hp-plugin-extracted"
            try:
                os.makedirs("/var/lib/hp", exist_ok=True)
                os.makedirs("/usr/share/hplip/scan/plugins", exist_ok=True)
                os.makedirs("/usr/share/hplip/data/plugins", exist_ok=True)
                os.makedirs("/usr/share/hplip/data/firmware", exist_ok=True)
                os.makedirs("/usr/lib/sane", exist_ok=True)
                os.makedirs("/usr/lib/arm-linux-gnueabihf/sane", exist_ok=True)
                shutil.rmtree(extract_dir, ignore_errors=True)

                with open(tmp_plugin_path, "wb") as f:
                    f.write(upload_file["body"])
                os.chmod(tmp_plugin_path, 0o755)

                subprocess.run(["sh", tmp_plugin_path, "--target", extract_dir, "--noexec"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)

                installed_count = 0
                if os.path.exists(extract_dir):
                    for item in os.listdir(extract_dir):
                        if item.endswith("-arm32.so") or item.endswith("-arm64.so"):
                            base_name = re.sub(r'-arm(32|64)\.so$', '.so', item)
                            src = os.path.join(extract_dir, item)
                            shutil.copy2(src, f"/usr/share/hplip/scan/plugins/{base_name}")
                            shutil.copy2(src, f"/usr/share/hplip/data/plugins/{base_name}")
                            shutil.copy2(src, f"/usr/lib/sane/{base_name}")
                            shutil.copy2(src, f"/usr/lib/arm-linux-gnueabihf/sane/{base_name}")
                            shutil.copy2(src, f"/usr/lib/{base_name}")
                            installed_count += 1
                        elif item.endswith(".fw.gz"):
                            shutil.copy2(os.path.join(extract_dir, item), f"/usr/share/hplip/data/firmware/{item}")

                with open("/var/lib/hp/hplip.state", "w", encoding="utf-8") as sf:
                    sf.write("[plugin]\ninstalled = 1\neula = 1\nversion = 3.22.10\n\n[installation]\nversion = 3.22.10\n")

                subprocess.run(["ldconfig"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                if installed_count > 0:
                    self.write_json(True, f"扫描插件 [{fname}] 已成功安装并完成动态库注册！({installed_count}个组件生效)")
                else:
                    self.write_json(False, "未能从插件包中找到对应架构二进制文件。")
            except Exception as e:
                self.write_json(False, f"安装插件异常: {str(e)}")
            finally:
                if os.path.exists(tmp_plugin_path):
                    try: os.remove(tmp_plugin_path)
                    except Exception: pass
                shutil.rmtree(extract_dir, ignore_errors=True)
            return

        elif action == "clean_disk":
            perform_cleanup()
            self.write_json(True, "临时打印缓存与垃圾文件已成功清理完成！已同步开启后台每日自动静默清理。")
            return

        # ================= 核心添加打印机逻辑（双向兼容全部前端传参） =================
        try:
            uri = self.get_argument("uri", "").strip() or self.get_argument("device_uri", "").strip()
            name = self.get_argument("name", "").strip() or self.get_argument("printer_name", "").strip()
            driver = self.get_argument("driver", "").strip() or self.get_argument("ppd_name", "").strip()
            description = self.get_argument("description", "").strip()
            ppd_file = self.request.files.get("ppd_file", [])

            # 严格防止把截断的短词 "hp" 或 "usb" 传给系统
            if not uri or uri in ["hp", "usb", "direct"]:
                self.write_json(False, "无效的设备 URI！请确认物理打印机已插紧并开机。")
                return

            if not name:
                self.write_json(False, "打印机物理端口与名称不能为空！")
                return

            clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', urllib.parse.unquote(name)).strip('_') or "Printer_Device"
            cmd = ["lpadmin", "-p", clean_name, "-v", uri, "-E"]

            if description:
                cmd.extend(["-D", description])

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

            # 执行系统 lpadmin 调用
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=15)
            if ppd_tmp and os.path.exists(ppd_tmp):
                try: os.remove(ppd_tmp)
                except Exception: pass

            # 关键修复：过滤 CUPS 2.4+ 弃用警告。只有 returncode != 0 时才真正判定为失败
            if res.returncode != 0:
                self.write_json(False, f"CUPS 631 拒绝添加: {res.stderr.strip()}")
                return

            # 双向互通保障：立即激活打印机状态并通知 CUPS 接收作业
            subprocess.run(["cupsenable", clean_name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            subprocess.run(["cupsaccept", clean_name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            subprocess.run(["lpadmin", "-d", clean_name], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)

            self.write_json(True, f"✔ 打印机【{clean_name}】已成功安装并同步至 631！")
        except subprocess.TimeoutExpired:
            self.write_json(False, "CUPS 系统通信超时，请检查服务状态")
        except Exception as e:
            self.write_json(False, f"添加打印机异常: {str(e)}")
