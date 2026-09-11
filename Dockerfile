FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

# 安装 CUPS 2.4.x、基础驱动库及 Python 运行环境
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    cups-filters \
    cups-ipp-utils \
    printer-driver-all \
    printer-driver-gutenprint \
    hplip \
    foomatic-db-compressed-ppds \
    openprinting-ppds \
    avahi-daemon \
    avahi-utils \
    dbus \
    libnss-mdns \
    locales \
    python3 \
    python3-requests \
    procps \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 配置系统语言与字符编码
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen

ENV LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8

# 修复中文字符集模板缺失导致的白屏与不跳转
RUN mkdir -p /usr/share/cups/templates/zh_CN /usr/share/cups/doc-root/zh_CN

# 先把系统原生英文模板无损拷贝至 zh_CN 兜底，再用汉化包覆盖
RUN cp -r /usr/share/cups/templates/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true
COPY ./i18/zh_CN/zh_CN/ /usr/share/cups/templates/zh_CN/
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/zh_CN/index.html

# 放置邮件打印守护程序与启动脚本
COPY mail_print.py /opt/mail_print.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /opt/mail_print.py

EXPOSE 631 5353/udp

VOLUME ["/etc/cups"]

ENTRYPOINT ["/entrypoint.sh"]
