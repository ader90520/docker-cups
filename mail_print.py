import imaplib
import email
from email.header import decode_header
import os
import time
import subprocess
import requests

# 环境变量读取（未配置则进入休眠，不影响原生 CUPS）
IMAP_SERVER = os.getenv("IMAP_SERVER", "").strip()
EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASS = os.getenv("EMAIL_PASS", "").strip()
NOTIFY_URL = os.getenv("NOTIFY_URL", "").strip() # 支持企业微信 Webhook 或 PushPlus URL

SAVE_DIR = "/tmp/print_jobs"
os.makedirs(SAVE_DIR, exist_ok=True)

def send_notification(title, content):
    """
    发送微信通知：
    1. 若是企业微信机器人链接 (qyapi.weixin.qq.com)，发送 Markdown/文本消息
    2. 若是 PushPlus (www.pushplus.plus)，发送通用 JSON 模板
    """
    if not NOTIFY_URL:
        return
    try:
        if "qyapi.weixin.qq.com" in NOTIFY_URL:
            payload = {
                "msgtype": "text",
                "text": {"content": f"【{title}】\n{content}"}
            }
        else:
            payload = {
                "title": title,
                "content": content
            }
        requests.post(NOTIFY_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[Notice] 微信通知推送出错: {e}", flush=True)

def decode_mime_words(s):
    """解析邮件头中的编码文本（防止中文乱码）"""
    if not s:
        return ""
    decoded_fragments = []
    for fragment, encoding in decode_header(s):
        if isinstance(fragment, bytes):
            enc = encoding or "utf-8"
            try:
                decoded_fragments.append(fragment.decode(enc, errors="ignore"))
            except LookupError:
                decoded_fragments.append(fragment.decode("utf-8", errors="ignore"))
        else:
            decoded_fragments.append(str(fragment))
    return "".join(decoded_fragments)

def check_and_print():
    if not IMAP_SERVER or not EMAIL_USER or not EMAIL_PASS:
        return

    mail = None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")

        # 搜索所有未读邮件
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK" or not messages or not messages[0]:
            mail.logout()
            return

        msg_ids = messages[0].split()
        for num in msg_ids:
            res, data = mail.fetch(num, "(RFC822)")
            if res != "OK" or not data:
                continue

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject = decode_mime_words(msg.get("Subject", "无主题"))
            sender = decode_mime_words(msg.get("From", "未知发件人"))

            print(f"[Print Job] 检测到新邮件: 《{subject}》 来自: {sender}", flush=True)

            printed_any = False
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                filename = part.get_filename()
                if not filename:
                    continue

                filename = decode_mime_words(filename)
                ext = os.path.splitext(filename)[1].lower()

                # 仅支持 PDF 与常规图片格式
                if ext in [".pdf", ".jpg", ".jpeg", ".png"]:
                    # 为防止特殊字符导致命令行执行失败，清理文件名
                    safe_filename = "".join([c for c in filename if c.isalnum() or c in "._- "]).strip()
                    if not safe_filename:
                        safe_filename = f"task_{int(time.time())}{ext}"

                    filepath = os.path.join(SAVE_DIR, safe_filename)
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue

                    with open(filepath, "wb") as f:
                        f.write(payload)

                    print(f"[Processing] 正在送入 CUPS 打印队列: {safe_filename}", flush=True)

                    # 调用 lp 命令（-o fit-to-page 自适应纸张尺寸）
                    cmd = ["lp", "-o", "fit-to-page", filepath]
                    result = subprocess.run(cmd, capture_output=True, text=True)

                    if result.returncode == 0:
                        send_notification("🖨️ 打印任务已提交", f"文件: {filename}\n来源: {sender}\n状态: 任务已送入打印机")
                        printed_any = True
                    else:
                        err_msg = result.stderr.strip()
                        send_notification("❌ 打印失败", f"文件: {filename}\n来源: {sender}\n错误原因: {err_msg}")

                    # 无论成功失败，删除临时文件
                    if os.path.exists(filepath):
                        os.remove(filepath)

            # 将处理过的邮件标记已读并删除，避免重复投递
            mail.store(num, "+FLAGS", "\\Deleted")

        mail.expunge()
        mail.logout()

    except Exception as e:
        print(f"[Error] 邮件轮询异常: {e}", flush=True)
        if mail:
            try:
                mail.logout()
            except Exception:
                pass

if __name__ == "__main__":
    print("[System] 邮件与微信推送监听服务已启动...", flush=True)
    while True:
        try:
            check_and_print()
        except Exception as e:
            print(f"[Loop Exception] {e}", flush=True)
        time.sleep(10)
