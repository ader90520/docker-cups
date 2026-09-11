# 🖨️ CUPS Docker 打印服务器 (全功能增强版)

> **支持局域网 AirPrint 免驱直连 + 微信/邮件远程云打印 + USB 热插拔识别**

---

### 📦 架构与系统支持

* **CPU 架构**：`linux/amd64` ｜ `linux/arm64` ｜ `linux/arm/v7`
* **适用平台**：iStoreOS / OpenWrt 软路由、海纳思 HiNAS (海思机顶盒)、群晖/威联通 NAS、x86 迷你主机等

---

### 🚀 快速部署

#### 方法一：双模启动（推荐：局域网打印 + 微信/邮件远程云打印）

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
  -e IMAP_SERVER=imap.qq.com \
  -e EMAIL_USER=your_email@qq.com \
  -e EMAIL_PASS=******** \
  -e NOTIFY_URL="[http://www.pushplus.plus/send?token=******](http://www.pushplus.plus/send?token=******)" \
  ader90520/cups:latest
```

#### 方法二：纯局域网模式（不启用外网与云打印）

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

---

### ⚙️ 环境变量参数表

| 变量名称 | 是否必填 | 默认值 / 示例 | 功能说明 |
| :--- | :---: | :--- | :--- |
| `ADMIN_PASSWORD` | **必填** | `admin` | CUPS 网页后台登录密码（管理账号固定为 `admin`） |
| `IMAP_SERVER` | 选填 | `imap.qq.com` | 邮箱 IMAP 接收服务器（不填则云打印自动进入休眠） |
| `EMAIL_USER` | 选填 | `********@qq.com` | 接收打印任务的专用邮箱账号 |
| `EMAIL_PASS` | 选填 | `********` | 邮箱第三方客户端授权码（**注意：非网页登录密码**） |
| `NOTIFY_URL` | 选填 | `http://www.pushplus.plus/...` | 微信出纸结果推送接口（支持 PushPlus / 企业微信 Webhook） |
| `TRIGGER_KEYWORD` | 选填 | *内置预设关键词* | 触发词白名单（默认包含打印、作业、试卷、各学科及年级词汇） |

---

### ✨ 特性说明

* **🔌 USB 设备热插拔识别**：注入 `--privileged=true` 特权指令并直通宿主机 `dbus` 与 `usb` 总线，打印机无论关机断电、重新插拔，系统均能秒级自动上线，彻底告别频繁重启容器。
* **📱 局域网全平台免驱**：开启 `--net=host` 模式，iPhone / iPad / Mac 自动识别 AirPrint 隔空打印，Windows 电脑与安卓设备（Mopria）直接添加网络打印机出纸。
* **🛡️ 智能作业识别与防废纸过滤**：
  * **广告拦截**：小于 40KB 的邮件签名图标、推销图片自动丢弃，绝不打出废纸。
  * **作业直通**：老师或家长发送包含“作业”、“试卷”、“语文”、“数学”、“二年级”等主题或附件，无需特殊指令即可自动打印。
* **💤 绿色低功耗休眠**：未配置邮箱变量时，后台监听程序自动进入休眠挂起（0% CPU 占用），原生 CUPS 局域网打印完全不受影响。

---

### 🛠️ 首次配置说明

#### 1. 进入 Web 界面添加打印机
1. 浏览器访问：`http://设备IP:631`。
2. 点击 **“管理 (Administration)”** ➔ **“添加打印机 (Add Printer)”**，输入账号 `admin` 与密码。
3. 勾选识别到的 USB 打印机，选择对应品牌的驱动，**务必勾选“共享此打印机 (Share This Printer)”** 完成添加。

#### 2. 绑定系统默认打印机（重要步骤：防止云打印报 No default destination）

```bash
# 步骤 1：查询识别到的打印机名称
docker exec -it cups lpstat -p

# 步骤 2：设为默认设备（请将下方 HP_LaserJet 替换为你实际查出来的名称）
docker exec -it cups lpoptions -d HP_LaserJet_Pro_MFP_M126a

# 步骤 3：验证绑定结果
docker exec -it cups lpstat -d
```

---

### 📄 日常打印方式

* **局域网打印**：手机与设备连接同一 Wi-Fi，打开文档点击“分享 ➔ 打印”，列表选中打印机即可出纸；Windows 电脑添加网络打印机，地址填写：`http://设备IP:631/printers/打印机名称`。
* **微信/远程打印**：微信收到 PDF 或图片，长按选择 **“用其他应用打开” ➔ 邮件**，发送给专属打印邮箱。邮件主题或附件名包含作业、试卷或相关学科词汇，10 秒内自动出纸，微信会同步收到出纸通知。
