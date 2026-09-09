ARG ARCH=amd64
FROM debian:bullseye-slim

ENV ADMIN_PASSWORD=admin

RUN apt-get update \
  && apt-get install -y sudo cups cups-bsd cups-filters foomatic-db-compressed-ppds printer-driver-all openprinting-ppds hpijs-ppds hp-ppd hplip \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

RUN adduser --home /home/admin --shell /bin/bash --gecos "admin" --disabled-password admin \
  && adduser admin sudo \
  && adduser admin lp \
  && adduser admin lpadmin

RUN echo 'admin ALL=(ALL:ALL) NOPASSWD:ALL' >> /etc/sudoers

RUN /usr/sbin/cupsd \
  && while [ ! -f /var/run/cups/cupsd.pid ]; do sleep 1; done \
  && cupsctl --remote-admin --remote-any --share-printers \
  && kill $(cat /var/run/cups/cupsd.pid) \
  && echo "ServerAlias *" >> /etc/cups/cupsd.conf \
  && echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf

RUN cp -rp /etc/cups /etc/cups-skel

ADD docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT [ "docker-entrypoint.sh" ]
CMD ["cupsd", "-f"]
VOLUME ["/etc/cups"]
EXPOSE 631
