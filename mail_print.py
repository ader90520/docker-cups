import imaplib
import email
from email.header import decode_header
import os
import time
import subprocess
import requests

IMAP_SERVER = os.getenv("IMAP_SERVER", "").strip()
EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASS = os.getenv("EMAIL_PASS", "").strip()
NOTIFY_URL = os.getenv("NOTIFY_URL", "").strip()

# 内置智能白名单关键词
DEFAULT_KEYWORDS = (
    "打,print,作业,试卷,练习,复习,打卡,"
    "语文,数学,英语,物理,化学,生物,历史,地理,政治,科学,"
    "一年级,二年级,三年级,四年级,五年级,六年级,"
    "初一,初二,初三,七年级,八年级,九年级,高一,高二,高三"
)
TRIGGER_KEYWORD = os.getenv("TRIGGER_KEYWORD", DEFAULT_KEYWORDS).strip()

SAVE_DIR = "/tmp/print_jobs"
os.makedirs(SAVE_DIR, exist_ok=True)

def send_notification(title, content):
    if not NOTIFY_URL:
        return
    try:
        if "qyapi.weixin.qq.com" in NOTIFY_URL:
            payload = {"msgtype": "text", "text": {"content": f"【{title}】\n{content}"}}
        else:
            payload = {"title": title, "content": content}
        requests.post(NOTIFY_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[Notice] 微信通知推送异常: {e}", flush=True)

def decode_mime_words(s):
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

def is_valid_trigger(text_to_check):
    if not TRIGGER_KEYWORD:
        return True
    keywords = [k.strip().lower() for k in TRIGGER_KEYWORD.split(",") if k.strip()]
    target = text_to_check.lower()
    return any(k in target for k in keywords)

def check_and_print():
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, timeout=15)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("INBOX")

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

            valid_attachments = []
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue

                filename = part.get_filename()
                if not filename:
                    continue

                filename = decode_mime_words(filename)
                ext = os.path.splitext(filename)[1].lower()

                if ext in [".pdf", ".jpg", ".jpeg", ".png"]:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue

                    # 拦截小于 40KB 的小图标/签名图片
                    if ext in [".jpg", ".jpeg", ".png"] and len(payload) < 40 * 1024:
                        print(f"[Filter] 忽略内嵌广告图标: {filename} ({len(payload)//1024} KB)", flush=True)
                        continue

                    valid_attachments.append((filename, ext, payload))

            if not valid_attachments:
                mail.store(num, "+FLAGS", "\\Deleted")
                continue

            # 主题或附件名命中任一关键词即放行
            all_names_to_check = subject + " " + " ".join([att[0] for att in valid_attachments])
            if not is_valid_trigger(all_names_to_check):
                print(f"[Ignore] 未命中打印关键词，跳过: 《{subject}》 来自: {sender}", flush=True)
                mail.store(num, "+FLAGS", "\\Deleted")
                continue

            print(f"[Print Job] 命中打印指令: 《{subject}》 来自: {sender}", flush=True)

            for filename, ext, payload in valid_attachments:
                safe_filename = "".join([c for c in filename if c.isalnum() or c in "._- "]).strip()
                if not safe_filename:
                    safe_filename = f"task_{int(time.time())}{ext}"

                filepath = os.path.join(SAVE_DIR, safe_filename)
                with open(filepath, "wb") as f:
                    f.write(payload)

                cmd = ["lp", "-o", "fit-to-page", filepath]
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0:
                    send_notification("🖨️ 打印成功", f"文件: {filename}\n来源: {sender}\n主题: {subject}")
                else:
                    err_msg = result.stderr.strip()
                    send_notification("❌ 打印失败", f"文件: {filename}\n来源: {sender}\n原因: {err_msg}")

                if os.path.exists(filepath):
                    os.remove(filepath)

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
    time.sleep(5)

    # 未配置邮箱时自动进入低功耗挂起，不消耗 CPU
    if not (IMAP_SERVER and EMAIL_USER and EMAIL_PASS):
        print("[System] 未检测到邮箱配置，进入深度休眠（作为纯局域网 AirPrint 服务器运行）。", flush=True)
        while True:
            time.sleep(3600)

    print("[System] 邮件云打印监听服务已就绪...", flush=True)
    while True:
        try:
            check_and_print()
        except Exception as e:
            print(f"[Loop Exception] {e}", flush=True)
        time.sleep(10)
