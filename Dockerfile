ARG ARCH=amd64
FROM debian:bookworm-slim

ENV ADMIN_PASSWORD=admin

RUN apt-get update && apt-get install -y sudo cups cups-bsd cups-filters foomatic-db-compressed-ppds printer-driver-all openprinting-ppds hplip && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN adduser --home /home/admin --shell /bin/bash --gecos "admin" --disabled-password admin \
  && adduser admin sudo \
  && adduser admin lp \
  && adduser admin lpadmin

RUN echo 'admin ALL=(ALL:ALL) NOPASSWD:ALL' >> /etc/sudoers

# 1. 自动写入中文默认语言配置
RUN /usr/sbin/cupsd \
  && while [ ! -f /var/run/cups/cupsd.pid ]; do sleep 1; done \
  && cupsctl --remote-admin --remote-any --share-printers \
  && kill $(cat /var/run/cups/cupsd.pid) \
  && echo "ServerAlias *" >> /etc/cups/cupsd.conf \
  && echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf \
  && echo "DefaultLanguage zh_CN" >> /etc/cups/cupsd.conf

# 2. 将你仓库里 i18/zh_CN 目录下的汉化文件，对应复制到容器内的 templates 和 doc-root 目录
COPY ./i18/zh_CN/zh_CN/ /usr/share/cups/templates/zh_CN/
RUN mkdir -p /usr/share/cups/doc-root/zh_CN
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/zh_CN/index.html

RUN cp -rp /etc/cups /etc/cups-skel

ADD docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT [ "docker-entrypoint.sh" ]
CMD ["cupsd", "-f"]
VOLUME ["/etc/cups"]
EXPOSE 631
