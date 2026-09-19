FROM debian:bullseye-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

# 安装 CUPS、驱动、SANE 扫描工具、无头转换工具及核心中文矢量字体
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-filters \
    cups-bsd \
    printer-driver-all \
    printer-driver-foo2zjs \
    hplip \
    sane-utils \
    libsane-hpaio \
    ghostscript \
    poppler-utils \
    libreoffice-writer-nogui \
    libreoffice-calc-nogui \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    python3 \
    python3-pip \
    python3-pil \
    python3-requests \
    python3-tornado \
    tzdata \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 开启 CUPS 局域网远程共享与管理权限
RUN sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf && \
    sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf && \
    sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf && \
    sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf && \
    echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf

# 部署工作文件
WORKDIR /opt
COPY mail_print.py /opt/mail_print.py
COPY cups_web_app.py /opt/cups_web_app.py
COPY entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh /opt/mail_print.py /opt/cups_web_app.py

EXPOSE 631 8000

VOLUME ["/etc/cups", "/scans"]

ENTRYPOINT ["/opt/entrypoint.sh"]
