FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

# 1. 精简驱动依赖 + 补充中文字体库（防 PDF/文档中文方块乱码）+ 运行时组件
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    cups-filters \
    cups-ipp-utils \
    hplip \
    printer-driver-gutenprint \
    printer-driver-foo2zjs \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
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
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
    && rm -rf /etc/xdg/autostart/*hp*.desktop 2>/dev/null || true

# 2. 生成中文字符集环境
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen

ENV LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8

# 3. 部署中文模板与完备的静态资源软链接（彻底避免 404 排版丢失）
RUN mkdir -p /usr/share/cups/templates/zh_CN /usr/share/cups/doc-root/zh_CN

# 拷贝汉化模板与中文首页
COPY ./i18/zh_CN/zh_CN/ /usr/share/cups/templates/zh_CN/
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/zh_CN/index.html
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/index.html

# 建立全局 CSS、图片及静态图标链接
RUN ln -sfn /usr/share/cups/doc-root/cups.css /usr/share/cups/doc-root/zh_CN/cups.css && \
    ln -sfn /usr/share/cups/doc-root/images /usr/share/cups/doc-root/zh_CN/images && \
    ln -sfn /usr/share/cups/doc-root/help /usr/share/cups/doc-root/zh_CN/help 2>/dev/null || true

# 制作官方出厂配置备份（用于挂载目录首次初始化）
RUN cp -rp /etc/cups /etc/cups.orig

# 4. 拷贝启动脚本与云打印守护脚本
COPY [eE]ntrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py

# 5. 清理换行符并赋予可执行权限
RUN dos2unix /entrypoint.sh /opt/mail_print.py 2>/dev/null || true && \
    chmod +x /entrypoint.sh /opt/mail_print.py

EXPOSE 631 5353/udp

VOLUME ["/etc/cups"]

ENTRYPOINT ["/entrypoint.sh"]
