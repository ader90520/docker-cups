DEFAULT_PRINTER_ENV = os.getenv("DEFAULT_PRINTER", "")

def get_active_printers():
    """
    动态通用探测 CUPS 中的打印机（支持任意品牌、任意型号）：
    1. 优先读取用户启动容器时指定的 DEFAULT_PRINTER 环境变量
    2. 其次读取 CUPS 系统中设置的默认打印机 (lpstat -d)
    3. 再次自动选取 CUPS 中已安装的第 1 台可用打印机 (lpstat -p)
    4. 若未安装任何打印机，返回 None，绝不写死任何具体型号！
    """
    default_printer = None
    all_printers = []
    try:
        env = os.environ.copy()
        env["LC_ALL"] = "C"

        # 1. 探测 CUPS 系统默认设备
        res_d = subprocess.run(["lpstat", "-d"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=5)
        if "destination: " in res_d.stdout:
            default_printer = res_d.stdout.split("destination: ")[-1].strip()

        # 2. 探测所有已安装设备
        res_p = subprocess.run(["lpstat", "-p"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=5)
        for line in res_p.stdout.splitlines():
            if line.startswith("printer "):
                p_name = line.split()[1].strip()
                all_printers.append(p_name)
    except Exception as e:
        print(f" [Printer Detect Warning] 探测异常: {e}", flush=True)

    # 确定最终出纸设备
    if DEFAULT_PRINTER_ENV and DEFAULT_PRINTER_ENV in all_printers:
        target = DEFAULT_PRINTER_ENV
    elif default_printer:
        target = default_printer
    elif all_printers:
        target = all_printers[0]
    else:
        target = None  # 彻底去掉写死型号，无设备即为 None

    return target, all_printers

def print_file(filepath, filename):
    """向系统 CUPS 发送打印任务并进行真实状态监控"""
    printer_name, all_printers = get_active_printers()
    
    # 彻底杜绝误报他人型号：无打印机时友好精准提示
    if not printer_name:
        err_msg = (
            "CUPS 系统中未检测到任何可用打印机！<br>"
            "请先在浏览器打开 CUPS 控制台添加你的打印机：<br>"
            "👉 访问地址：http://盒子IP:631 -> [Administration] -> [Add Printer]"
        )
        print(f" [Print Error] 未检测到任何可用打印机，请先访问 Web 后台添加。", flush=True)
        send_pushplus_notice("❌ 打印失败提醒", f"文件 <b>{filename}</b> 提交失败：<br>{err_msg}")
        return False

    try:
        optimize_image_for_print(filepath)
        cmd = [
            "lp",
            "-d", printer_name,
            "-o", "fit-to-page",
            "-o", "media=A4",
            filepath
        ]

        print(f" [Exec] 正在向 [{printer_name}] 提交打印: {filename}", flush=True)
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        if res.returncode == 0:
            job_id = res.stdout.strip()
            print(f" [Print Success] 出纸成功: {filename} -> {job_id}", flush=True)
            send_pushplus_notice(
                "🖨️ 打印机出纸提醒",
                f"已向打印机 <b>{printer_name}</b> 提交任务：<br>"
                f"文件名：<b>{filename}</b><br>"
                f"任务编号：{job_id}<br>"
                f"正在出纸，请在设备旁稍候。"
            )
            return True
        else:
            err_output = res.stderr.strip()
            print(f" [Print Error] lp 失败: {err_output}", flush=True)
            send_pushplus_notice("❌ 打印失败提醒", f"打印 <b>{filename}</b> 出错：<br>{err_output}")
            return False

    except subprocess.TimeoutExpired:
        print(f" [Print Error] 打印提交超时: {filename}", flush=True)
        send_pushplus_notice("❌ 打印超时提醒", f"向 <b>{printer_name}</b> 提交任务超时，请检查打印机状态。")
        return False
    except Exception as e:
        print(f" [System Error] 提交异常: {e}", flush=True)
        return False
    finally:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
        gc.collect()
