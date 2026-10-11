FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8 \
    TZ=Asia/Shanghai \
    OPENCV_LOG_LEVEL=ERROR \
    QT_QPA_PLATFORM=offscreen \
    OMP_NUM_THREADS=4 \
    OPENBLAS_NUM_THREADS=4

# 基础系统环境、驱动与 Python 图像处理库 (精简可靠，不通过 pip 源码编译重型 ONNX 包，保障多架构 buildx 秒过)
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    cups-filters \
    cups-browsed \
    fonts-wqy-microhei \
    avahi-daemon \
    dbus \
    locales \
    gettext \
    ca-certificates \
    curl \
    wget \
    usbutils \
    net-tools \
    printer-driver-foo2zjs \
    hplip \
    sane-utils \
    libsane-common \
    libsane-hpaio \
    sane-airscan \
    python3 \
    python3-pil \
    python3-tornado \
    python3-requests \
    python3-numpy \
    python3-opencv \
    libgomp1 \
    libglib2.0-0 \
    xz-utils \
    dos2unix \
    && sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc/* /usr/share/man/* /root/.cache

COPY modules/ /opt/modules/
COPY webapp/ /opt/webapp/
COPY entrypoint.sh /entrypoint.sh
COPY i18/ /opt/i18/

RUN find /opt/modules/ /opt/webapp/ /entrypoint.sh -type f -exec dos2unix {} + 2>/dev/null || true && \
    chmod -R +x /opt/modules/ /opt/webapp/ /entrypoint.sh 2>/dev/null || true

ARG HPLIP_VER=3.22.10
RUN cd /tmp && \
    wget -q -c "https://downloads.sourceforge.net/project/hplip/hplip-plugins/${HPLIP_VER}/hplip-${HPLIP_VER}-plugin.run" && \
    chmod +x hplip-${HPLIP_VER}-plugin.run && \
    printf "y\na\ny\n" | hp-plugin -i -p /tmp 2>&1 || true && \
    rm -rf /tmp/hplip*

# 驱动微码安装、主题美化与中文语言包/主页精准注入
RUN /bin/bash /opt/modules/drivers/install_foo2zjs.sh || true && \
    /bin/bash /opt/modules/drivers/install_hp_plugin.sh || true && \
    /bin/bash /opt/modules/theme/patch_cups_theme.sh || true && \
    mkdir -p /usr/share/cups/templates/zh_CN \
             /usr/share/cups/templates/zh \
             /usr/share/cups/locale/zh_CN \
             /usr/share/cups/locale/zh \
             /usr/share/cups/doc-root/zh_CN && \
    if [ -f /opt/i18/cups_zh.po ]; then \
        msgfmt -o /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /opt/i18/cups_zh.po && \
        cp /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh/cups_zh.mo || true; \
    fi && \
    if [ -d /opt/i18/zh_CN ]; then \
        cp -rf /opt/i18/zh_CN/*.tmpl /usr/share/cups/templates/zh_CN/ 2>/dev/null || true && \
        cp -rf /opt/i18/zh_CN/*.tmpl /usr/share/cups/templates/zh/ 2>/dev/null || true; \
    elif [ -d /opt/i18/templates ]; then \
        cp -rf /opt/i18/templates/*.tmpl /usr/share/cups/templates/zh_CN/ 2>/dev/null || true && \
        cp -rf /opt/i18/templates/*.tmpl /usr/share/cups/templates/zh/ 2>/dev/null || true; \
    fi && \
    if [ -f /opt/i18/zh_CN/index.html ]; then \
        cp -f /opt/i18/zh_CN/index.html /usr/share/cups/doc-root/index.html && \
        cp -f /opt/i18/zh_CN/index.html /usr/share/cups/doc-root/zh_CN/index.html && \
        ln -sf /usr/share/cups/doc-root/index.html /usr/share/cups/doc-root/index.html.zh 2>/dev/null || true && \
        ln -sf /usr/share/cups/doc-root/index.html /usr/share/cups/doc-root/index.html.zh_CN 2>/dev/null || true; \
    elif [ -f /opt/i18/index.html ]; then \
        cp -f /opt/i18/index.html /usr/share/cups/doc-root/index.html && \
        ln -sf /usr/share/cups/doc-root/index.html /usr/share/cups/doc-root/index.html.zh 2>/dev/null || true && \
        ln -sf /usr/share/cups/doc-root/index.html /usr/share/cups/doc-root/index.html.zh_CN 2>/dev/null || true; \
    fi && \
    chmod -R 755 /usr/share/cups/templates/zh* /usr/share/cups/locale/zh* 2>/dev/null || true

RUN mkdir -p /opt/cups_data \
             /scans \
             /opt/webapp/data \
             /opt/webapp/static \
             /tmp/cups_web_uploads \
             /tmp/mail_print_tasks \
             /etc/cups/ssl \
             /var/lock/sane \
             /var/run/dbus && \
    chmod -R 777 /scans /opt/webapp/data /opt/webapp/static /tmp/mail_print_tasks /tmp/cups_web_uploads /var/lock/sane /var/run/dbus && \
    chmod 700 /etc/cups/ssl

EXPOSE 631 8088
ENTRYPOINT ["/entrypoint.sh"]
