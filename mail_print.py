def send_notification(title, content):
    if not NOTIFY_URL:
        return
    try:
        if "pushplus.plus" in NOTIFY_URL:
            # pushplus 微信公众号推送格式
            token = os.getenv("PUSHPLUS_TOKEN", "").strip()
            payload = {
                "token": token,
                "title": title,
                "content": content.replace("\n", "<br>"),  # 换行格式化
                "template": "html"
            }
        elif "qyapi.weixin.qq.com" in NOTIFY_URL:
            # 企业微信 Webhook 格式
            payload = {"msgtype": "text", "text": {"content": f"【{title}】\n{content}"}}
        else:
            payload = {"title": title, "content": content}

        requests.post(NOTIFY_URL, json=payload, timeout=8)
    except Exception as e:
        print(f"[Notice] 推送通知异常: {e}", flush=True)
