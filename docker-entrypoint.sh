#!/bin/bash
set -e

# 如果没有配置文件，则从骨架目录拷贝
if [ ! -f /etc/cups/cupsd.conf ]; then
    cp -r /etc/cups-skel/* /etc/cups/
fi

# 设置 admin 密码
if [ -n "$ADMIN_PASSWORD" ]; then
    echo "admin:$ADMIN_PASSWORD" | chpasswd
fi

# 在后台启动邮件云打印监听（不阻塞主进程）
python3 /usr/local/bin/mail_print.py &

# 执行原定启动命令 (cupsd -f)
exec "$@"
