FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

# 1. 安装 CUPS 2.4.x、核心驱动、Avahi/D-Bus 与 Python 运行环境
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
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# 2. 生成中文字符集环境
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen

ENV LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8

# 3. 修复中文字符集模板缺失导致的白屏与设置项无法跳转
RUN mkdir -p /usr/share/cups/templates/zh_CN /usr/share/cups/doc-root/zh_CN

# 先将英文模板全量兜底复制到 zh_CN，确保 set-printer-options-*.tmpl 完整存在
RUN cp -r /usr/share/cups/templates/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true

# 覆盖自定义中文模板与主页
COPY ./i18/zh_CN/zh_CN/ /usr/share/cups/templates/zh_CN/
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/zh_CN/index.html

# 4. 拷贝启动脚本与邮件云打印脚本
# 使用通配符匹配 entrypoint.sh，防止平台间字符编码隐形差异
COPY [eE]ntrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py

# 5. 转换 Windows 换行符（CRLF->LF）并赋予可执行权限，防止脚本在 Linux 下因换行符报错
RUN dos2unix /entrypoint.sh /opt/mail_print.py 2>/dev/null || true && \
    chmod +x /entrypoint.sh /opt/mail_print.py

EXPOSE 631 5353/udp

VOLUME ["/etc/cups"]

ENTRYPOINT ["/entrypoint.sh"]
