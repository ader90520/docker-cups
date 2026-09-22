FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8 \
    TZ=Asia/Shanghai

# 1. 安装基础依赖包与完整驱动
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

# 2. foo2zjs 微码
RUN mkdir -p /usr/share/foo2zjs/firmware /usr/share/foo2xqx/firmware && \
    cd /tmp && \
    for m in 1005 1007 1008 1020; do getweb $m || true; done && \
    cp -f *.dl /usr/share/foo2zjs/firmware/ 2>/dev/null || true && \
    cp -f *.dl /usr/share/foo2xqx/firmware/ 2>/dev/null || true && \
    rm -rf /tmp/*

# 3. 跨架构匹配安装 HP 闭源插件与机型库映射（使用 printf 写入配置，彻底杜绝 parse error）
RUN set -e && \
    RAW_VER=$(dpkg -s hplip | grep '^Version:' | awk '{print $2}') && \
    HPLIP_VER=$(echo "$RAW_VER" | sed -E 's/[~+].*//; s/-.*//') && \
    ARCH=$(uname -m) && \
    echo ">>> [HPLIP 安装] 纯净版本: ${HPLIP_VER}，架构: ${ARCH}" && \
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
            cp -f "$f" "/usr/lib/arm-linux-gnueabihf/${base_name}" 2>/dev/null || true; \
            cp -f "$f" "/usr/lib/x86_64-linux-gnu/${base_name}" 2>/dev/null || true; \
        fi; \
    done && \
    cp -f plugin.spec /usr/share/hplip/ 2>/dev/null || true && \
    printf "[installation]\nversion = %s\nplugin = 1\nplugin_version = %s\n" "${HPLIP_VER}" "${HPLIP_VER}" > /var/lib/hp/hplip-install.state && \
    printf "[hplip]\nversion=%s\n\n[dirs]\nhome=/usr/share/hplip\nrun=/var/run\nppd=/usr/share/ppd/HP\nppdbase=/usr/share/ppd\ndoc=/usr/share/doc/hplip\nhtml=/usr/share/doc/hplip\nicon=/usr/share/applications\ncupsbackend=/usr/lib/cups/backend\ncupsfilter=/usr/lib/cups/filter\ndrv=/usr/share/cups/drv\ninternal_tag=%s\n\n[installation]\ndate_time=09/21/2026\ninstalled_version=%s\ncommand_line=\n" "${HPLIP_VER}" "${HPLIP_VER}" "${HPLIP_VER}" > /etc/hp/hplip.conf && \
    MODEL_PATH=$(find /usr -name "models.dat" 2>/dev/null | head -n 1) && \
    if [ -n "$MODEL_PATH" ]; then \
        cp -f "$MODEL_PATH" /usr/share/hplip/data/models/ 2>/dev/null || true; \
        cp -f "$MODEL_PATH" /usr/share/hplip/ 2>/dev/null || true; \
    fi && \
    ldconfig && \
    cd / && rm -rf /tmp/hp-plugin

# 4. 拷贝业务代码
COPY entrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py
COPY cups_web_app.py /opt/cups_web_app.py
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po
COPY i18/zh_CN/index.html /tmp/index.html
COPY i18/zh_CN/zh_CN/ /tmp/zh_templates/

# 5. 语言包编译与经典深蓝通栏主题样式注入（彻底修复导航横排与底部文字漂移）
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
    cp -f /tmp/index.html /usr/share/cups/doc-root/index.html && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh_CN/index.html && \
    cp -rp /etc/cups/* /etc/cups.orig/ 2>/dev/null || true && \
    printf "\n/* 修复中文汉化经典蓝色主题与横向导航锁死 */\n.header, .nav, div.header { background: #003366 !important; width: 100%% !important; margin: 0 !important; padding: 0 !important; }\n.header ul, ul.nav, .nav ul, div.header ul { display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important; align-items: center !important; list-style: none !important; margin: 0 !important; padding: 10px 20px !important; background: #003366 !important; }\n.header ul li, ul.nav li, .nav li, div.header ul li { display: inline-flex !important; margin-right: 25px !important; }\n.header ul li a, ul.nav li a, .nav li a, div.header ul li a { color: #ffffff !important; text-decoration: none !important; font-weight: bold !important; font-size: 15px !important; padding: 4px 8px !important; border-radius: 3px !important; }\n.header ul li a:hover, ul.nav li a:hover { background: #004c99 !important; }\n.body, .content { min-height: 480px !important; padding: 20px !important; }\n.footer, div.footer { clear: both !important; background: #003366 !important; color: #ffffff !important; text-align: center !important; padding: 15px 0 !important; margin-top: 40px !important; width: 100%% !important; display: block !important; }\n.footer a, div.footer a { color: #80bfff !important; text-decoration: underline !important; }\n" >> /usr/share/cups/doc-root/cups.css && \
    cp -f /usr/share/cups/doc-root/cups.css /usr/share/cups/doc-root/zh_CN/cups.css 2>/dev/null || true && \
    chmod 777 /scans /tmp/mail_print_tasks /tmp/cups_web_uploads /var/lock/sane /var/run/dbus && \
    chmod 700 /etc/cups/ssl && \
    chmod +x /entrypoint.sh /opt/mail_print.py /opt/cups_web_app.py && \
    rm -rf /tmp/*

EXPOSE 631 8088
VOLUME ["/etc/cups", "/scans"]
ENTRYPOINT ["/entrypoint.sh"]
CMD []
