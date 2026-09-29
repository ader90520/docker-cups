#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shutil
import subprocess
from handlers.base_handler import BaseHandler, UPLOAD_DIR

CUPS_PPD_DIR = "/etc/cups/ppd"
MODEL_DIR = "/usr/share/cups/model"

def run_cmd(cmd, env=None):
    if env is None:
        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"
        env["LANG"] = "C"
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=10)
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)

def parse_printer_detailed_status(printer_name):
    """解析打印机状态：AirPrint共享标记、缺纸、卡纸、缺墨、耗材余量"""
    status_info = {
        "is_shared": False,
        "media_empty": False,
        "paper_jam": False,
        "toner_low": False,
        "toner_empty": False,
        "state_message": "就绪"
    }

    # 1. 检查是否开启共享 (AirPrint 发现的核心)
    ok, out, _ = run_cmd(["lpoptions", "-p", printer_name])
    if ok and "printer-is-shared=true" in out:
        status_info["is_shared"] = True

    # 2. 检查详细告警状态
    ok, out, _ = run_cmd(["lpstat", "-p", printer_name, "-l"])
    if ok:
        out_lower = out.lower()
        if any(k in out_lower for k in ["media-empty", "out of paper", "offline", "缺纸"]):
            status_info["media_empty"] = True
        if any(k in out_lower for k in ["media-jam", "paper jam", "jam", "卡纸"]):
            status_info["paper_jam"] = True
        if any(k in out_lower for k in ["toner-low", "low on toner", "墨粉低"]):
            status_info["toner_low"] = True
        if any(k in out_lower for k in ["toner-empty", "out of toner", "无墨", "更换耗材"]):
            status_info["toner_empty"] = True

        for line in out.splitlines():
            line_s = line.strip()
            if line_s.startswith("Status:"):
                status_info["state_message"] = line_s.replace("Status:", "").strip()

    return status_info

def perform_system_diagnostics():
    """全面诊断：USB挂载、Avahi广播、SANE驱动、PPD状态与磁盘健康"""
    issues = []

    # 1. USB 设备节点映射检测
    if not os.path.exists("/dev/bus/usb"):
        issues.append({
            "level": "danger",
            "title": "USB设备节点未映射",
            "detail": "宿主机 /dev/bus/usb 未映射进容器，打印机与扫描仪无法通信。"
        })

    # 2. Avahi 守护进程检测 (隔空打印核心)
    _, out, _ = run_cmd(["pidof", "avahi-daemon"])
    if not out:
        issues.append({
            "level": "danger",
            "title": "Avahi mDNS 广播未运行",
            "detail": "Avahi 广播离线，导致苹果 iPhone/Mac 无法通过隔空打印搜索到设备。"
        })

    # 3. SANE 扫描仪驱动与硬件通信检测
    ok, out, err = run_cmd(["scanimage", "-L"])
    if not ok or "No scanners were identified" in out or not out:
        issues.append({
            "level": "warning",
            "title": "未检测到就绪的扫描仪",
            "detail": "SANE 未识别到可用扫描仪。若有多功能一体机，请确认 USB 已插紧并支持 HPLIP/SANE。"
        })

    # 4. PPD 驱动健康度检测
    if os.path.exists(CUPS_PPD_DIR):
        ppds = [f for f in os.listdir(CUPS_PPD_DIR) if f.endswith(".ppd")]
        if not ppds:
            issues.append({
                "level": "warning",
                "title": "未发现已配置的打印机",
                "detail": "当前系统没有任何可用队列，请上传 PPD 驱动或在 631 控制台添加打印机。"
            })

    # 5. 闪存磁盘空间容量诊断
    total, used, free = shutil.disk_usage("/")
    used_pct = int((used / total) * 100)
    if used_pct >= 90:
        issues.append({
            "level": "danger",
            "title": f"系统存储空间爆满告急 ({used_pct}%)",
            "detail": "可用闪存不足，会导致打印任务写入失败、日志卡死，请立即点击下方一键清理！"
        })

    return issues

class DevicesHandler(BaseHandler):
    def get(self):
        printers_list = []
        default_printer = ""
        try:
            env = os.environ.copy()
            env["CUPS_SERVER"] = "/run/cups/cups.sock"
            env["LANG"] = "C"

            # 1. 优先通过 lpstat -a 获取队列
            _, out_a, _ = run_cmd(["lpstat", "-a"], env=env)
            for line in out_a.splitlines():
                parts = line.strip().split()
                if parts:
                    p = parts[0].strip()
                    if p and p not in printers_list:
                        printers_list.append(p)

            # 2. 兜底提取 -p
            if not printers_list:
                _, out_p, _ = run_cmd(["lpstat", "-p"], env=env)
                for line in out_p.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        p = parts[1].strip()
                        if p and p not in printers_list:
                            printers_list.append(p)

            # 3. 提取默认打印机
            _, out_d, _ = run_cmd(["lpstat", "-d"], env=env)
            for line in out_d.splitlines():
                if ":" in line or "：" in line:
                    default_printer = line.split(":")[-1].split("：")[-1].strip()

            if not default_printer and printers_list:
                default_printer = printers_list[0]

        except Exception as e:
            print(f"[DevicesHandler] 设备提取异常: {e}", flush=True)

        devices = []
        for p in printers_list:
            details = parse_printer_detailed_status(p)
            devices.append({
                "name": p,
                "is_default": (p == default_printer),
                "is_shared": details["is_shared"],
                "media_empty": details["media_empty"],
                "paper_jam": details["paper_jam"],
                "toner_low": details["toner_low"],
                "toner_empty": details["toner_empty"],
                "status_msg": details["state_message"]
            })

        # 磁盘空间监控
        total, used, free = shutil.disk_usage("/")
        disk_info = {
            "total_gb": round(total / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "used_pct": int((used / total) * 100)
        }

        self.write_json(True, "", data={
            "printers": devices,
            "default": default_printer,
            "disk": disk_info,
            "diagnostics": perform_system_diagnostics()
        })

class PrinterAdminHandler(BaseHandler):
    def post(self):
        action = self.get_argument("action", "").strip()
        printer = self.get_argument("printer", "").strip()

        # 安全防注入校验
        if printer and (printer.startswith("-") or not re.match(r'^[a-zA-Z0-9_.\-:+]+$', printer)):
            self.write_json(False, "非法打印机设备名称")
            return

        env = os.environ.copy()
        env["CUPS_SERVER"] = "/run/cups/cups.sock"

        # 1. 切换共享打印机并刷新 Avahi (隔空打印关键)
        if action == "toggle_share":
            enable_str = self.get_argument("shared", "true").lower()
            is_share = enable_str in ["true", "1", "yes"]
            flag = "true" if is_share else "false"

            ok, _, err = run_cmd(["lpadmin", "-p", printer, "-o", f"printer-is-shared={flag}"], env=env)
            # 重启 avahi 广播立即生效
            run_cmd(["service", "avahi-daemon", "restart"])
            if ok:
                msg = f"已{'开启' if is_share else '关闭'} [{printer}] 的局域网与隔空打印(AirPrint)共享！"
                self.write_json(True, msg)
            else:
                self.write_json(False, f"设置共享失败: {err}")

        # 2. 设为默认打印机
        elif action == "set_default":
            ok, _, err = run_cmd(["lpadmin", "-d", printer], env=env)
            if ok:
                self.write_json(True, f"已将 [{printer}] 设置为默认打印机")
            else:
                self.write_json(False, f"设置默认失败: {err}")

        # 3. 发送测试页
        elif action == "test_page":
            test_file = "/usr/share/cups/data/testprint"
            if not os.path.exists(test_file):
                test_file = "/opt/webapp/static/favicon.ico"
            ok, _, err = run_cmd(["lp", "-d", printer, test_file], env=env)
            if ok:
                self.write_json(True, f"已向 [{printer}] 发送测试打印页！")
            else:
                self.write_json(False, f"测试页发送失败: {err}")

        # 4. 清空打印队列
        elif action == "cancel_all":
            run_cmd(["cancel", "-a"], env=env)
            self.write_json(True, "已强制清空所有卡死与排队的打印任务！")

        # 5. 上传 PPD 驱动并同步到 631 驱动库
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

            os.makedirs(MODEL_DIR, exist_ok=True)
            save_path = os.path.join(MODEL_DIR, fname)
            with open(save_path, "wb") as f:
                f.write(ppd["body"])

            os.chmod(save_path, 0o644)
            # 刷新 CUPS 驱动库缓存，使 631 后台即时可选
            run_cmd(["cupsfilter", "-m", "application/vnd.cups-raw", save_path])
            self.write_json(True, f"驱动 [{fname}] 已成功安装到系统驱动库！在 631 后台添加打印机时即可直接选取。")

        # 6. 一键深度清理闪存垃圾
        elif action == "clean_disk":
            try:
                # 清理 Web 上传缓存
                shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                # 清理系统 /tmp 缓存与遗留打印任务
                run_cmd(["sh", "-c", "rm -rf /tmp/mail_* /tmp/cups_* /tmp/*.pdf /tmp/*.jpg /tmp/*.png 2>/dev/null || true"])
                self.write_json(True, "临时打印缓存与垃圾文件已成功清理完成，闪存空间已释放！")
            except Exception as e:
                self.write_json(False, f"清理异常: {str(e)}")

        else:
            self.write_json(False, "未知操作指令")
