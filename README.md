CUPS Docker 镜像

构架
amd64
arm32v7
arm64v8
用途
启动容器
```bash
docker run -d \
--name=cups \
--restart=always \
--privileged=true \
--net=host \
-v /var/run/dbus:/var/run/dbus \
-v /var/lib/docker/data/cups/config:/etc/cups \
-v /dev/bus/usb:/dev/bus/usb \
-e ADMIN_PASSWORD=admin \
ader90520/cups:latest
```
配置
登录 CUPS 网页界面，连接端口 631（例如 https://localhost:631），并根据您的需求配置 CUPS。 默认凭证：管理员 / 管理员
要更改管理员密码，请将环境变量 ADMIN_PASSWORD 设置为你的密码.
```bash
docker run -d \
--name=cups \
--restart=always \
--privileged=true \
--net=host \
-v /var/run/dbus:/var/run/dbus \
-v /var/lib/docker/data/cups/config:/etc/cups \
-v /dev/bus/usb:/dev/bus/usb \
-e ADMIN_PASSWORD=admin \
ader90520/cups:latest

```
