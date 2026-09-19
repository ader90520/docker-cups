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
from PIL import Image, ImageEnhance

# ==================== 1. 全局配置与环境变量 ====================
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.qq.com")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")
NOTIFY_URL = os.getenv("NOTIFY_URL", "https://www.pushplus.plus/send")
DEFAULT_PRINTER_ENV = os.getenv("DEFAULT_PRINTER", "")

TEMP_DIR = "/tmp/mail_print_tasks"
SCAN_DIR = os.getenv("SCAN_DIR", "/scans")
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)


# ==================== 2. 图像去黑底与格式前置转换 ====================
def clean_and_whiten_image(image_path):
    """去除手机拍摄阴影与发暗底色，加深字迹"""
    try:
        with Image.open(image_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            gray = img.convert("L")
            enhancer = ImageEnhance.Contrast(gray)
            gray = enhancer.enhance(2.0)

            # 阈值漂白算法：像素值 > 140 漂白为纯白 (255)，< 140 笔画加黑
            table = []
            for i in range(256):
                if i > 140:
                    table.append(255)
                else:
                    table.append(int((i / 140.0) ** 1.5 * 180))

            clean_img = gray.point(table, mode="L")
            clean_img.save(image_path, format="JPEG", quality=95)
            print(f" [Image Clean] 照片漂白去黑底完成: {os.path.basename(image_path)}", flush=True)
            return True
    except Exception as e:
        print(f" [Image Clean Warning] 去黑底跳过: {e}", flush=True)
        return False

def images_to_single_pdf(image_paths, output_pdf_path):
    """将多张图片自动合并为一份 A4 多页 PDF，消除单张打印时的页间机械停顿"""
    try:
        pil_images = []
        for img_p in image_paths:
            clean_and_whiten_image(img_p)
            with Image.open(img_p) as im:
                pil_images.append(im.convert("RGB"))

        if not pil_images:
            return False

        first_image = pil_images[0]
        other_images = pil_images[1:] if len(pil_images) > 1 else []
        first_image.save(output_pdf_path, save_all=True, append_images=other_images, resolution=300.0)
        print(f" [Multi-Images] 成功将 {len(image_paths)} 张图片合并为多页 PDF: {os.path.basename(output_pdf_path)}", flush=True)
        return True
    except Exception as e:
        print(f" [Multi-Images Error] 合并图片至 PDF 失败: {e}", flush=True)
        return False

def convert_office_to_pdf(doc_path):
    """Office 文档 (doc/docx/xls/xlsx/ppt/pptx) 无头转 PDF"""
    try:
        out_dir = os.path.dirname(doc_path)
        base_name = os.path.splitext(os.path.basename(doc_path))[0]
        target_pdf = os.path.join(out_dir, f"{base_name}.pdf")

        cmd = [
            "libreoffice",
            "--headless", "--invisible", "--nodefault",
            "--nofirststartwizard", "--nolockcheck", "--nologo", "--norestore",
            "--convert-to", "pdf",
            "--outdir", out_dir,
            doc_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=40)
        if os.path.exists(target_pdf):
            return target_pdf
    except Exception as e:
        print(f" [Office Convert Error] Office 转 PDF 异常: {e}", flush=True)
    return None

def convert_ofd_to_pdf(ofd_path):
    """通过 ofdrw 命令行转 OFD 为 PDF (若环境中集成该 jar)"""
    try:
        out_dir = os.path.dirname(ofd_path)
        base_name = os.path.splitext(os.path.basename(ofd_path))[0]
        target_pdf = os.path.join(out_dir, f"{base_name}_ofd.pdf")
        jar_tool = "/opt/ofdrw-converter.jar"

        if os.path.exists(jar_tool):
            cmd = ["java", "-jar", jar_tool, ofd_path, target_pdf]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
            if os.path.exists(target_pdf):
                return target_pdf
    except Exception as e:
        print(f" [OFD Convert Error] OFD 转 PDF 异常: {e}", flush=True)
    return None


# ==================== 3. PushPlus 微信通知 ====================
def send_pushplus_notice(title, content):
    if not PUSHPLUS_TOKEN:
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
            print(f" [PushPlus Success] 微信通知成功: {title}", flush=True)
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


# ==================== 4. 打印机探测与出纸状态监控 ====================
def get_active_printers():
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
    start_time = time.time()
    env = dict(os.environ, LC_ALL="C")
    num_match = re.search(r"\d+$", job_id)
    raw_num = num_match.group() if num_match else job_id

    while time.time() - start_time < timeout:
        time.sleep(2.5)

        # 检查硬件告警
        p_check = subprocess.run(["lpstat", "-p", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
        p_out = p_check.stdout.lower()
        if any(err in p_out for err in ["disabled", "media-empty", "paper-jam", "out of paper"]):
            err_reason = diagnose_printer_hardware(printer_name)
            subprocess.run(["cancel", job_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return False, err_reason

        # 检查队列
        res_active = subprocess.run(["lpstat", "-o", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
        active_jobs = res_active.stdout

        if job_id not in active_jobs and raw_num not in active_jobs:
            time.sleep(1)
            final_p = subprocess.run(["lpstat", "-p", printer_name], stdout=subprocess.PIPE, text=True, env=env, timeout=3)
            if "disabled" in final_p.stdout.lower():
                return False, diagnose_printer_hardware(printer_name)
            return True, "物理出纸完成"

    subprocess.run(["cancel", job_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return False, f"打印超时（超过 {timeout} 秒未出纸，排查建议: {diagnose_printer_hardware(printer_name)}）"

def print_file(filepath, filename):
    printer_name, all_printers = get_active_printers()

    if not printer_name:
        err_msg = "CUPS 系统中未检测到任何可用打印机，请先在 631 网页后台添加打印机。"
        print(f" [Print Error] {err_msg}", flush=True)
        send_pushplus_notice("❌ 打印失败提醒", f"文件 <b>{filename}</b> 提交失败：<br>{err_msg}")
        return False

    try:
        # 300DPI + Cairo 矢量渲染 + 灰度模式加速（防多页停顿）
        cmd = [
            "lp",
            "-d", printer_name,
            "-o", "media=A4",
            "-o", "fit-to-page",
            "-o", "Resolution=300dpi",
            "-o", "pdftops-renderer=pdftocairo",
            "-o", "ColorModel=Gray",
            "-o", "job-sheets=none",
            filepath
        ]

        t_start = time.time()
        print(f" [Exec] 正在向 [{printer_name}] 快速提交打印任务: {filename} ...", flush=True)
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)

        if res.returncode != 0:
            err_output = res.stderr.strip() or "底层渲染过滤失败"
            print(f" [Print Error] lp 提交失败: {err_output}", flush=True)
            send_pushplus_notice("❌ 打印失败（未出纸）", f"文件 <b>{filename}</b> 提交被拒：<br>{err_output}")
            return False

        match = re.search(r"request id is ([^\s]+)", res.stdout)
        job_id = match.group(1) if match else res.stdout.strip().split()[0]
        print(f" [Queue Success] 任务进入硬件队列: {job_id}，正在监听物理出纸...", flush=True)

        is_printed, reason = wait_for_job_real_print(printer_name, job_id, timeout=90)
        cost_time = round(time.time() - t_start, 1)

        if is_printed:
            print(f" [Print Success] 物理出纸成功！总耗时: {cost_time}s", flush=True)
            send_pushplus_notice(
                "🖨️ 试卷/文档已出纸",
                f"打印机：<b>{printer_name}</b><br>"
                f"文件名：<b>{filename}</b><br>"
                f"耗时：<b>{cost_time} 秒</b><br>"
                f"状态：<b>出纸完成（300DPI 极速矢量渲染）</b>"
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
    except Exception as e:
        print(f" [System Error] 提交异常: {e}", flush=True)
        return False
    finally:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
        gc.collect()


# ==================== 5. 邮件守护主循环 ====================
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

            image_tasks = []
            doc_tasks = []

            for part in msg.walk():
                if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                    continue

                filename = decode_mime_words(part.get_filename() or "")
                ext = os.path.splitext(filename)[1].lower()
                filepath = os.path.join(TEMP_DIR, filename)

                with open(filepath, "wb") as f:
                    f.write(part.get_payload(decode=True))

                if ext in [".jpg", ".jpeg", ".png", ".bmp", ".heic"]:
                    image_tasks.append(filepath)
                elif ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".ofd", ".txt"]:
                    doc_tasks.append((filepath, filename, ext))

            # 1. 图片任务合并：多张图片自动合成单份 PDF
            if image_tasks:
                if len(image_tasks) == 1:
                    clean_and_whiten_image(image_tasks[0])
                    print_file(image_tasks[0], os.path.basename(image_tasks[0]))
                else:
                    combined_pdf = os.path.join(TEMP_DIR, f"合并试卷_{int(time.time())}.pdf")
                    if images_to_single_pdf(image_tasks, combined_pdf):
                        print_file(combined_pdf, os.path.basename(combined_pdf))
                    for p in image_tasks:
                        if os.path.exists(p):
                            try: os.remove(p)
                            except Exception: pass

            # 2. 文档任务统一转 PDF 打印
            for fpath, fname, ext in doc_tasks:
                if ext == ".pdf":
                    print_file(fpath, fname)
                elif ext in [".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]:
                    conv_pdf = convert_office_to_pdf(fpath)
                    if conv_pdf:
                        print_file(conv_pdf, os.path.basename(conv_pdf))
                    else:
                        send_pushplus_notice("❌ 打印失败", f"文档 <b>{fname}</b> 排版转换失败。")
                elif ext == ".ofd":
                    conv_pdf = convert_ofd_to_pdf(fpath)
                    if conv_pdf:
                        print_file(conv_pdf, os.path.basename(conv_pdf))
                    else:
                        send_pushplus_notice("❌ 打印失败", f"OFD 文件 <b>{fname}</b> 解析失败。")

            mail.store(num, "+FLAGS", "\\Seen")

        mail.close()
        mail.logout()
    except Exception as e:
        print(f" [Mail Loop Error] 轮询异常: {e}", flush=True)


# ==================== 6. 主程序入口 ====================
def main():
    print("==================================================", flush=True)
    print(" 🚀 [Cloud Print Daemon] 邮件云打印监控已启动", flush=True)
    print(f" 监听邮箱: {EMAIL_USER}", flush=True)
    print(f" 微信通知: {'已启用 (PushPlus)' if PUSHPLUS_TOKEN else '未启用'}", flush=True)
    print(" 核心特性: 多格式转PDF | 多图合并连打 | 试卷去黑底 | 300DPI 极速矢量", flush=True)
    print("==================================================", flush=True)

    while True:
        try:
            fetch_and_print()
        except Exception as e:
            print(f" [Daemon Loop Error] 守护异常: {e}", flush=True)
        time.sleep(8)

if __name__ == "__main__":
    main()
