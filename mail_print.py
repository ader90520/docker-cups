#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import gc
import re
import email
import imaplib
import subprocess
import requests
from email.header import decode_header

# ==================== 1. 全局配置与环境变量 ====================
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.qq.com")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")
NOTIFY_URL = os.getenv("NOTIFY_URL", "https://www.pushplus.plus/send")
DEFAULT_PRINTER_ENV = os.getenv("DEFAULT_PRINTER", "")

TEMP_DIR = "/tmp/mail_print_tasks"
os.makedirs(TEMP_DIR, exist_ok=True)


# ==================== 2. PushPlus 微信推送通道 ====================
def send_pushplus_notice(title, content):
    if not PUSHPLUS_TOKEN:
        print(f" [PushPlus Skip] 未配置 Token，跳过微信通知: {title}", flush=True)
        return
    try:
        headers = {"Content-Type": "application/json"}
        payload = {
            "token": PUSHPLUS_TOKEN.strip(),
            "title": title,
            "content": content,
            "template": "html"
        }
        res = requests.post(NOTIFY_URL, json=payload, headers=headers, timeout=8)
        ret = res.json()
        if res.status_code == 200 and ret.get("code") == 200:
            print(f" [PushPlus Success] 微信通知发送成功: {title}", flush=True)
        else:
            print(f" [PushPlus Warning] 接口返回异常: {res.text}", flush=True)
    except Exception as e:
        print(f" [PushPlus Error] 推送请求异常: {e}", flush=True)

def decode_mime_words(header_str):
    if not header_str:
        return ""
    fragments = decode_header(header_str)
    res = []
    for frag, charset in fragments:
        if isinstance(frag, bytes):
            try:
                res.append(frag.decode(charset or "utf-8", errors="ignore"))
            except Exception:
                res.append(frag.decode("utf-8", errors="ignore"))
        else:
            res.append(str(frag))
    return "".join(res)


# ==================== 3. 打印机探测与真实出纸状态监控 ====================
def get_active_printers():
    """
    动态通用探测 CUPS 中的打印机（支持任意品牌、任意型号）：
    1. 优先读取用户配置的环境变量 DEFAULT_PRINTER
    2. 其次读取 CUPS 默认打印机 (lpstat -d)
    3. 再次自动选取 CUPS 中已安装的第 1 台可用设备 (lpstat -p)
    """
    default_printer = None
    all_printers = []
    try:
        env = os.environ.copy()
        env["LC_ALL"] = "C"

        res_d = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=5)
        if "destination: " in res_d.stdout:
            default_printer = res_d.stdout.split("destination: ")[-1].strip()

        res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=5)
        for line in res_p.stdout.splitlines():
            if line.startswith("printer "):
                all_printers.append(line.split()[1].strip())
    except Exception as e:
        print(f" [Printer Detect Warning] 探测异常: {e}", flush=True)

    if DEFAULT_PRINTER_ENV and DEFAULT_PRINTER_ENV in all_printers:
        target = DEFAULT_PRINTER_ENV
    elif default_printer:
        target = default_printer
    elif all_printers:
        target = all_printers[0]
    else:
        target = None

    return target, all_printers

def diagnose_printer_hardware(printer_name):
    """提取打印机真实的物理异常状态（缺纸、卡纸、脱机等）"""
    try:
        env = dict(os.environ, LC_ALL="C")
        res = subprocess.run(["lpstat", "-p", printer_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=3)
        out = res.stdout.lower()

        if any(w in out for w in ["out of paper", "media-empty", "paper empty", "input tray empty"]):
            return "打印机【缺纸】，请在纸盒中添加 A4 纸！"
        elif any(w in out for w in ["jam", "paper-jam"]):
            return "打印机【卡纸】，请打开后盖取出夹纸！"
        elif any(w in out for w in ["offline", "not connected", "unable to locate"]):
            return "打印机【脱机/掉线】，请检查 USB 数据线或电源！"
        elif any(w in out for w in ["door open", "cover open"]):
            return "打印机【仓门未合上】，请检查打印机机盖！"
        elif any(w in out for w in ["toner", "ink"]):
            return "打印机【碳粉/墨水耗尽】，请检查耗材！"
        elif "paused" in out or "disabled" in out:
            return "打印机被系统【暂停/停用】，可能上次任务报错未自动恢复。"
        elif res.stdout.strip():
            return f"底层提示: {res.stdout.strip()}"
    except Exception:
        pass
    return "硬件未响应或通信中断"

def wait_for_job_real_print(printer_name, job_id, timeout=90):
    """
    轻量非阻塞监听真实出纸结果
    返回: (bool 成功状态, str 详情原因)
    """
    start_time = time.time()
    env = dict(os.environ, LC_ALL="C")
    num_match = re.search(r"\d+$", job_id)
    raw_num = num_match.group() if num_match else job_id

    while time.time() - start_time < timeout:
        time.sleep(2.5)  # 2.5 秒极轻量轮询，CPU 消耗低于 0.1%

        # 1. 检查打印机硬件是否发生报错
        p_check = subprocess.run(["lpstat", "-p", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
        p_out = p_check.stdout.lower()
        if any(err in p_out for err in ["disabled", "media-empty", "paper-jam", "out of paper"]):
            err_reason = diagnose_printer_hardware(printer_name)
            # 立即取消卡死任务，防止死锁后续排队任务
            subprocess.run(["cancel", job_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return False, err_reason

        # 2. 检查任务队列状态
        res_active = subprocess.run(["lpstat", "-o", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
        active_jobs = res_active.stdout

        # 若任务已出队，说明打印机硬件已接管并吐纸完毕
        if job_id not in active_jobs and raw_num not in active_jobs:
            time.sleep(1)
            final_p = subprocess.run(["lpstat", "-p", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
            if "disabled" in final_p.stdout.lower():
                return False, diagnose_printer_hardware(printer_name)
            return True, "物理出纸完成"

    # 超时仍未出纸，清理队列并上报
    subprocess.run(["cancel", job_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return False, f"打印超时（超过 {timeout} 秒未出纸，排查提示: {diagnose_printer_hardware(printer_name)}）"

def print_file(filepath, filename):
    """极速清晰渲染提交 + 物理出纸状态捕获"""
    printer_name, all_printers = get_active_printers()

    if not printer_name:
        err_msg = (
            "CUPS 系统中未检测到任何可用打印机！<br>"
            "请先在浏览器打开控制台添加打印机：<br>"
            "👉 http://盒子IP:631 -> [Administration] -> [Add Printer]"
        )
        print(f" [Print Error] 未检测到打印机，请先访问 Web 后台添加。", flush=True)
        send_pushplus_notice("❌ 打印失败提醒", f"文件 <b>{filename}</b> 提交失败：<br>{err_msg}")
        return False

    try:
        ext = os.path.splitext(filename)[1].lower()

        # 核心极速清晰出纸参数组合：
        # 1. pdftops-renderer=pdftocairo: 切换 Cairo 矢量引擎，解决大 PDF 延迟 5 分钟
        # 2. Resolution=300dpi: 保证试卷字迹极度清晰，并减少 75% 内存与光栅化计算
        # 3. fit-to-page: 自动适配 A4 边距，不拉伸变形
        # 4. job-sheets=none: 关闭冗余封面页
        cmd = [
            "lp",
            "-d", printer_name,
            "-o", "media=A4",
            "-o", "fit-to-page",
            "-o", "Resolution=300dpi",
            "-o", "pdftops-renderer=pdftocairo",
            "-o", "job-sheets=none"
        ]

        if ext in [".jpg", ".jpeg", ".png", ".bmp"]:
            cmd.extend(["-o", "ppi=300", "-o", "scaling=100"])

        cmd.append(filepath)

        t_start = time.time()
        print(f" [Exec] 正在向 [{printer_name}] 快速提交打印任务: {filename} ...", flush=True)
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)

        if res.returncode != 0:
            err_output = res.stderr.strip() or "底层渲染过滤失败"
            print(f" [Print Error] lp 提交失败: {err_output}", flush=True)
            send_pushplus_notice("❌ 打印失败（未出纸）", f"文件 <b>{filename}</b> 提交被拒：<br>{err_output}")
            return False

        # 提取整洁的任务编号 (例如从 request id is HP_1020-5 提取 HP_1020-5)
        match = re.search(r"request id is ([^\s]+)", res.stdout)
        job_id = match.group(1) if match else res.stdout.strip().split()[0]
        print(f" [Queue Success] 任务已进入硬件队列: {job_id}，正在监听物理出纸...", flush=True)

        # 轮询验证真实出纸结果
        is_printed, reason = wait_for_job_real_print(printer_name, job_id, timeout=90)
        cost_time = round(time.time() - t_start, 1)

        if is_printed:
            print(f" [Print Success] 物理出纸成功！总耗时: {cost_time}s", flush=True)
            send_pushplus_notice(
                "🖨️ 试卷/文档已出纸",
                f"打印机：<b>{printer_name}</b><br>"
                f"文件名：<b>{filename}</b><br>"
                f"耗时：<b>{cost_time} 秒</b><br>"
                f"状态：<b>出纸完成（300DPI 矢量清晰渲染）</b>"
            )
            return True
        else:
            print(f" [Print Failed] 未能出纸: {reason}", flush=True)
            send_pushplus_notice(
                "⚠️ 打印机未出纸报警",
                f"文件：<b>{filename}</b><br>"
                f"打印机：<b>{printer_name}</b><br>"
                f"状态：<span style='color:red;'><b>未出纸</b></span><br>"
                f"原因：<b>{reason}</b><br>"
                f"提示：异常任务已自动清理，排除故障后请重新发送。"
            )
            return False

    except subprocess.TimeoutExpired:
        print(f" [Print Error] 打印提交超时: {filename}", flush=True)
        send_pushplus_notice("❌ 打印超时提醒", f"向 <b>{printer_name}</b> 提交任务超时，请检查 USB 链路。")
        return False
    except Exception as e:
        print(f" [System Error] 提交异常: {e}", flush=True)
        return False
    finally:
        # 无论成功还是失败，均立刻销毁临时文件并强刷垃圾回收，确保小盒子连续打印不爆内存
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
        gc.collect()


# ==================== 4. 邮件守护主循环 ====================
def fetch_and_print():
    if not EMAIL_USER or not EMAIL_PASS:
        return

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993, timeout=12)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK" or not messages[0]:
            mail.logout()
            return

        for num in messages[0].split():
            status, data = mail.fetch(num, "(RFC822)")
            if status != "OK":
                continue

            msg = email.message_from_bytes(data[0][1])
            subject = decode_mime_words(msg.get("Subject", "无主题"))
            sender = decode_mime_words(msg.get("From", "未知发件人"))
            print(f" [New Mail] 收到新邮件: [{subject}] 来自: {sender}", flush=True)

            for part in msg.walk():
                if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                    continue

                filename = decode_mime_words(part.get_filename() or "")
                ext = os.path.splitext(filename)[1].lower()

                # 兼容各类试卷及日常作业文件
                if ext in [".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".txt"]:
                    print(f" [Downloading] 流式拉取附件: {filename}", flush=True)
                    filepath = os.path.join(TEMP_DIR, filename)
                    with open(filepath, "wb") as f:
                        f.write(part.get_payload(decode=True))

                    # 顺序打印附件，确保大文件连续打印时不撞车
                    print_file(filepath, filename)

            # 标记邮件为已读
            mail.store(num, "+FLAGS", "\\Seen")

        mail.close()
        mail.logout()
    except Exception as e:
        print(f" [Mail Loop Error] 轮询异常: {e}", flush=True)

def main():
    print("==================================================", flush=True)
    print(" 🚀 [Cloud Print Daemon] 邮件云打印监控已启动", flush=True)
    print(f" 监听邮箱: {EMAIL_USER}", flush=True)
    print(f" 微信通知: {'已启用 (PushPlus)' if PUSHPLUS_TOKEN else '未启用 (未配置 Token)'}", flush=True)
    print(" 核心特性: pdftocairo 矢量加速 | 300DPI 极速清晰 | 故障精准报警", flush=True)
    print("==================================================", flush=True)

    while True:
        try:
            fetch_and_print()
        except Exception as e:
            print(f" [Daemon Loop Error] 守护异常: {e}", flush=True)
        # 轮询时间设为 8 秒，兼顾灵敏响应与系统节能
        time.sleep(8)

if __name__ == "__main__":
    main()
