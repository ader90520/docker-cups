FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

# 1. 安装 CUPS 2.4.x、核心驱动、Avahi/D-Bus 与 Python 依赖
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
    python3-pil \
    procps \
    curl \
    dos2unix \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /etc/xdg/autostart/*hp*.desktop 2>/dev/null || true

# 2. 生成中文字符集环境
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen

ENV LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8

# 3. 中文模板与主页（坚决不混入英文底包，确保原厂 CSS 与排版 100% 正常）
RUN mkdir -p /usr/share/cups/templates/zh_CN /usr/share/cups/doc-root/zh_CN
COPY ./i18/zh_CN/zh_CN/ /usr/share/cups/templates/zh_CN/
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/zh_CN/index.html
# 同步一份中文主页到根目录，确保任何浏览器直接打开 IP:631 都是中文
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/index.html

# 制作官方初始配置备份（供持久化卷首次初始化）
RUN cp -rp /etc/cups /etc/cups.orig

# 4. 拷贝启动脚本与云打印守护脚本
COPY [eE]ntrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py

# 5. 清理换行符并授权
RUN dos2unix /entrypoint.sh /opt/mail_print.py 2>/dev/null || true && \
    chmod +x /entrypoint.sh /opt/mail_print.py

EXPOSE 631 5353/udp

VOLUME ["/etc/cups"]

ENTRYPOINT ["/entrypoint.sh"]
