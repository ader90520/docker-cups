# 🖨️ CUPS Docker 打印服务器 (全中文增强版)

> **全中文现代化 Web 界面 ｜ 局域网 AirPrint 免驱直连 ｜ 微信/邮件远程云打印 ｜ USB 热插拔自愈**

---

### 📦 架构与系统支持

* **镜像标签**：`ader90520/cups:latest`（或指定版本 `ader90520/cups:2.4.2`）
* **CPU 架构**：`linux/amd64` ｜ `linux/arm64` ｜ `linux/arm/v7`
* **适用硬件**：海纳思 HiNAS (海思机顶盒)、斐讯 N1 (iStoreOS / OpenWrt / Armbian)、群晖 / 威联通 NAS、各类 x86 软路由及迷你主机。

---

### 🚀 快速部署

#### 方法一：双模启动（推荐：局域网打印 + 微信/邮件远程云打印）

```bash
docker run -d \
  --name cups \
  --restart unless-stopped \
  --privileged \
  --net host \
  -v /dev/bus/usb:/dev/bus/usb \
  -v /opt/cups_data:/etc/cups \
  -e ADMIN_PASSWORD=admin \
  -e IMAP_SERVER=imap.qq.com \
  -e EMAIL_USER=your_email@qq.com \
  -e EMAIL_PASS=邮箱授权码 \
  -e NOTIFY_URL=[http://www.pushplus.plus/send](http://www.pushplus.plus/send) \
  -e PUSHPLUS_TOKEN=your_token_here \
  ader90520/cups:latest
```

#### 方法二：纯局域网模式（仅需手机/电脑局域网打印）

```bash
docker run -d \
  --name cups \
  --restart unless-stopped \
  --privileged \
  --net host \
  -v /dev/bus/usb:/dev/bus/usb \
  -v /opt/cups_data:/etc/cups \
  -e ADMIN_PASSWORD=admin \
  ader90520/cups:latest
```

---

### ⚙️ 环境变量参数表

| 变量名称 | 是否必填 | 默认值 / 示例 | 功能说明 |
| :--- | :---: | :--- | :--- |
| `ADMIN_PASSWORD` | **必填** | `admin` | CUPS 后台登录密码（默认管理账号为 `admin`，同时兼容 `CUPS_PASSWORD`） |
| `IMAP_SERVER` | 选填 | `imap.qq.com` | 接收打印任务的邮箱 IMAP 服务器（不填则自动休眠挂起） |
| `EMAIL_USER` | 选填 | `your_email@qq.com` | 接收打印文件的专属邮箱账号 |
| `EMAIL_PASS` | 选填 | `********` | 邮箱第三方客户端**独立授权码**（**注意：非网页登录密码**） |
| `NOTIFY_URL` | 选填 | `http://www.pushplus.plus/send` | 出纸结果推送接口（支持 PushPlus / 企业微信 Webhook） |
| `PUSHPLUS_TOKEN` | 选填 | `********` | PushPlus 推送平台的用户专属 Token |
| `TRIGGER_KEYWORD` | 选填 | *内置预设学科及年级词汇* | 触发词白名单（包含：打印、作业、试卷、语文、数学、英语等） |

---

### ✨ 特性与核心优化

* **🇨🇳 全原生中文 Web 界面**：底层内置编译完整的 `cups.mo` 词典，默认强行锁定 UTF-8 中文环境，彻底告别后台半汉化或英文回退。
* **🎨 现代化深蓝通栏与吸底排版**：全面优化 CUPS 默认竖向散乱的导航排版，顶部呈现深蓝通栏横向导航，底部版权自动吸底固定，移动端与 PC 端自适应对齐。
* **⚡ 断电自愈与小存储保护**：
  * **空目录自愈**：初次挂载宿主机目录时，自动从备份副本回填官方初始配置，彻底避免容器无限重启崩溃。
  * **残余锁清理**：容器启动前自动清除残留的 PID 锁，杜绝海纳思、N1 等设备意外断电后的启动卡死。
  * **轻量化限流**：关闭历史打印文件保留（`PreserveJobFiles No`），单日志限制 1MB，确保小容量 eMMC 设备长期稳定运行不爆盘。
* **🔌 USB 设备热插拔自愈**：开启 `--privileged` 容器特权并直通 `/dev/bus/usb` 总线，打印机关机再开机、拔插 USB 线均能秒级识别重连。
* **📱 局域网全终端免驱**：开启 `--net host` 模式，iPhone / iPad / Mac 通过原生 AirPrint 隔空打印秒级发现设备；Windows 与 Android（Mopria）直接添加网络打印机出纸。
* **🛡️ 智能防废纸过滤**：
  * 自动拦截小于 40KB 的邮件签名缩略图、表情及广告推销图片。
  * 支持 PDF、Word、TXT、JPG、PNG 等常用办公与学习文档格式直接转换出纸。

---

### 🛠️ 首次配置说明

#### 1. 进入 Web 界面添加打印机
1. 电脑或手机浏览器访问：`http://设备IP:631`。
2. 点击顶部导航栏 **“管理”** ➔ **“添加打印机”**，在弹窗中输入账号 `admin` 与部署时设置的密码。
3. 勾选在“本地打印机”中识别到的具体 USB 设备，点击继续。
4. 选择对应的品牌与驱动型号，**务必勾选“共享此打印机”**，点击添加完成配置。

#### 2. 绑定默认打印机（重要：确保云打印精准找到出纸目标）

```bash
# 步骤 1：查询系统成功识别并添加的打印机名称
docker exec -it cups lpstat -p

# 步骤 2：设为全局默认打印机（将下方 HP_LaserJet 替换为你实际查出来的名称）
docker exec -it cups lpoptions -d HP_LaserJet_Pro_MFP_M126a

# 步骤 3：验证默认设备绑定结果
docker exec -it cups lpstat -d
```

---

### 📄 日常打印方式

* **苹果设备（iOS / macOS）**：连接同一局域网 Wi-Fi，打开任意文档/图片，点击 **“分享 ➔ 打印”**，在列表中直接选择打印机即可。
* **Windows 电脑**：进入“设置 ➔ 蓝牙和其他设备 ➔ 打印机和扫描仪 ➔ 添加设备 ➔ 我需要的打印机不在列表中 ➔ 按名称选择共享打印机”，地址填写：
  ```text
  http://你的设备IP:631/printers/你的打印机名称
  ```
* **微信 / 远程邮件打印**：在微信群或聊天框中打开文件，长按点击 **“用其他应用打开” ➔ 选择“邮件”** 发送至配置的打印邮箱。邮件主题或文件名中包含作业、试卷等任意学科关键词，后台 10 秒内自动调起出纸，并在出纸完成后向微信推送 PushPlus 打印成功结果。
