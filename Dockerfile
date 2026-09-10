ARG ARCH=amd64
FROM debian:bookworm-slim

ENV ADMIN_PASSWORD=admin

# 安装 CUPS、全套打印驱动以及 Python 依赖
RUN apt-get update && apt-get install -y \
    sudo cups cups-bsd cups-filters foomatic-db-compressed-ppds \
    printer-driver-all openprinting-ppds hplip \
    python3 python3-requests \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 创建管理用户
RUN adduser --home /home/admin --shell /bin/bash --gecos "admin" --disabled-password admin \
  && adduser admin sudo \
  && adduser admin lp \
  && adduser admin lpadmin

RUN echo 'admin ALL=(ALL:ALL) NOPASSWD:ALL' >> /etc/sudoers

# 预设中文语言与允许任意访问
RUN /usr/sbin/cupsd \
  && while [ ! -f /var/run/cups/cupsd.pid ]; do sleep 1; done \
  && cupsctl --remote-admin --remote-any --share-printers \
  && kill $(cat /var/run/cups/cupsd.pid) \
  && echo "ServerAlias *" >> /etc/cups/cupsd.conf \
  && echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf \
  && echo "DefaultLanguage zh_CN" >> /etc/cups/cupsd.conf

# 拷贝汉化文件
COPY ./i18/zh_CN/zh_CN/ /usr/share/cups/templates/zh_CN/
RUN mkdir -p /usr/share/cups/doc-root/zh_CN
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/zh_CN/index.html

# 拷贝后台打印脚本
COPY mail_print.py /usr/local/bin/mail_print.py
RUN chmod +x /usr/local/bin/mail_print.py

# 备份初始配置
RUN cp -rp /etc/cups /etc/cups-skel

ADD docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT [ "docker-entrypoint.sh" ]
CMD ["cupsd", "-f"]
VOLUME ["/etc/cups"]
EXPOSE 631
