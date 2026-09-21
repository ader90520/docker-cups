FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8 \
    TZ=Asia/Shanghai

# 1. 安装基础依赖包与完整驱动套件（包含 hplip-data 保证 models.dat 机型库完整）
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
    printer-driver-all \
    printer-driver-foo2zjs \
    hplip \
    hplip-data \
    sane-utils \
    libsane-hpaio \
    sane-airscan \
    python3 \
    python3-pil \
    python3-tornado \
    python3-requests \
    xz-utils \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc/* /usr/share/man/* /tmp/* \
    && sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen

# 2. 获取并部署 foo2zjs 常用打印机微码
RUN mkdir -p /usr/share/foo2zjs/firmware /usr/share/foo2xqx/firmware && \
    cd /tmp && \
    for m in 1005 1007 1008 1020; do getweb $m || true; done && \
    cp -f *.dl /usr/share/foo2zjs/firmware/ 2>/dev/null || true && \
    cp -f *.dl /usr/share/foo2xqx/firmware/ 2>/dev/null || true && \
    rm -rf /tmp/*

# 3. 跨架构自动匹配安装 HP 专有闭源插件与机型库映射
RUN set -e && \
    RAW_VER=$(dpkg -s hplip | grep '^Version:' | awk '{print $2}') && \
    HPLIP_VER=$(echo "$RAW_VER" | sed -E 's/[~+].*//; s/-.*//') && \
    ARCH=$(uname -m) && \
    echo ">>> [HPLIP 安装] 纯净版本: ${HPLIP_VER}，主机架构: ${ARCH}" && \
    mkdir -p /tmp/hp-plugin && cd /tmp/hp-plugin && \
    URL_MIRROR="https://www.openprinting.org/download/printdriver/auxfiles/HP/plugins" && \
    curl -fsSL -A "Mozilla/5.0" "${URL_MIRROR}/hplip-${HPLIP_VER}-plugin.run" -o plugin.run && \
    sh plugin.run --target /tmp/hp-plugin/extracted --noexec && \
    cd /tmp/hp-plugin/extracted && \
    mkdir -p /usr/share/hplip/data/plugins /usr/share/hplip/data/models /var/lib/hp /etc/hp /root/.hplip && \
    TARGET_SUFFIX="" && \
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then TARGET_SUFFIX="arm64"; \
    elif [ "$ARCH" = "armv7l" ] || [ "$ARCH" = "armv6l" ]; then TARGET_SUFFIX="arm32"; \
    elif [ "$ARCH" = "x86_64" ]; then TARGET_SUFFIX="x86_64"; \
    else TARGET_SUFFIX="x86_32"; fi && \
    for f in *-*.so; do \
        if echo "$f" | grep -q -- "-${TARGET_SUFFIX}\.so"; then \
            base_name=$(echo "$f" | sed "s/-${TARGET_SUFFIX}\.so/\.so/"); \
            cp -f "$f" "/usr/share/hplip/${base_name}"; \
            cp -f "$f" "/usr/lib/${base_name}" 2>/dev/null || true; \
            cp -f "$f" "/usr/lib/aarch64-linux-gnu/${base_name}" 2>/dev/null || true; \
            cp -f "$f" "/usr/lib/x86_64-linux-gnu/${base_name}" 2>/dev/null || true; \
        fi; \
    done && \
    cp -f plugin.spec /usr/share/hplip/ 2>/dev/null || true && \
    cat << EOF > /var/lib/hp/hplip-install.state && \
[installation]
version = ${HPLIP_VER}
plugin = 1
plugin_version = ${HPLIP_VER}
EOF
    cat << EOF > /etc/hp/hplip.conf && \
[hplip]
version=${HPLIP_VER}

[dirs]
home=/usr/share/hplip
run=/var/run
ppd=/usr/share/ppd/HP
ppdbase=/usr/share/ppd
doc=/usr/share/doc/hplip
html=/usr/share/doc/hplip
icon=/usr/share/applications
cupsbackend=/usr/lib/cups/backend
cupsfilter=/usr/lib/cups/filter
drv=/usr/share/cups/drv
internal_tag=${HPLIP_VER}

[installation]
date_time=09/21/2026
installed_version=${HPLIP_VER}
command_line=
EOF
    MODEL_PATH=$(find /usr -name "models.dat" 2>/dev/null | head -n 1) && \
    if [ -n "$MODEL_PATH" ]; then \
        cp -f "$MODEL_PATH" /usr/share/hplip/data/models/ 2>/dev/null || true; \
        cp -f "$MODEL_PATH" /usr/share/hplip/ 2>/dev/null || true; \
    fi && \
    ldconfig && \
    cd / && rm -rf /tmp/hp-plugin

# 4. 拷贝业务脚本及 Web 汉化
COPY entrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py
COPY cups_web_app.py /opt/cups_web_app.py
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po
COPY i18/zh_CN/index.html /tmp/index.html
COPY i18/zh_CN/zh_CN/ /tmp/zh_templates/

# 5. 语言包编译与运行目录就绪
RUN mkdir -p /usr/share/locale/zh_CN/LC_MESSAGES \
             /usr/share/cups/locale/zh_CN \
             /usr/share/cups/locale/zh \
             /usr/share/cups/doc-root/zh_CN \
             /usr/share/cups/templates/zh_CN \
             /etc/cups.orig /scans /tmp/mail_print_tasks /tmp/cups_web_uploads /etc/cups/ssl /var/lock/sane /var/run/dbus && \
    msguniq --use-first /tmp/cups_zh.po -o /tmp/cups_zh_clean.po && \
    msgfmt -o /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /tmp/cups_zh_clean.po && \
    cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/cups/locale/zh_CN/cups.mo && \
    cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/cups/locale/zh/cups.mo && \
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh_CN/ && \
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/ && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/index.html && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh_CN/index.html && \
    cp -rp /etc/cups/* /etc/cups.orig/ 2>/dev/null || true && \
    chmod 777 /scans /tmp/mail_print_tasks /tmp/cups_web_uploads /var/lock/sane /var/run/dbus && \
    chmod 700 /etc/cups/ssl && \
    chmod +x /entrypoint.sh /opt/mail_print.py /opt/cups_web_app.py && \
    rm -rf /tmp/*

EXPOSE 631 8088
VOLUME ["/etc/cups", "/scans"]
ENTRYPOINT ["/entrypoint.sh"]
CMD []
