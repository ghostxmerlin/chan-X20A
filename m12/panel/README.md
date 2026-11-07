# M12 监控面板（Mac）

这是 chan-X20A 的监控端之一，专门看 M5Stack UnitV2 M12 摄像头：Mac 上的本地网页，只用 M12 输出的识别结果和系统状态，不拉视频。StackChan 本体的数据由 [Android 观察台](../../android/Collector/README.md)负责。M12 的硬件、接口和实测记录见 [M12 探索记录](../../docs/m12-unitv2.md)。

## 启动

在仓库根目录运行：

```bash
python3 m12/panel/server.py                          # M12 通过 USB 连着 Mac（出厂地址 10.254.239.1）
UNITV2_HOST=<m12-ip> python3 m12/panel/server.py     # M12 已连上局域网 WiFi
```

启动后会自动打开浏览器，地址是 http://localhost:8765 ，按 Ctrl+C 退出。面板只监听本机回环地址，只用 Python 标准库，不需要安装依赖。

## 功能

- 打开或关闭人脸检测、人脸识别（同一时间只能运行一个；两个都关时进入待机）
  - 设备固件没有"停止"功能，所以待机用的是 `audio_fft`：摄像头关闭，CPU 约 15%，输出不会发到串口。
    如果用 `camera_stream` 当待机，即使不看画面，CPU 也要 70% 左右。
- 「数据流（JSON）」：实时滚动显示识别结果、系统状态和事件；可以暂停、清空、按类别筛选，点一行可以展开
- 示意图显示人脸的位置和 5 个关键点，以及识别出的名字
- 人脸登记：开始登记 → 停止登记 → 保存到设备；也可以清空全部
- 显示芯片温度曲线（标出 95°C 过热保护线）、CPU、频率、负载、内存、运行时间

## 让 M12 连接 WiFi

```bash
python3 m12/panel/wifi_setup.py            # 按提示输入 WiFi 名和密码（密码不显示），先输入的优先连接
python3 m12/panel/wifi_setup.py --status   # 查看是否连上、拿到的 IP
python3 m12/panel/wifi_setup.py --dry-run  # 只预览新配置，不写入
```

- 密码在本机换算成 WPA PSK 哈希，只有哈希写进设备的 `/etc/wpa_supplicant.conf`（原文件备份为 `.bak`）。
- M12 只支持 2.4GHz WiFi。设备自己的热点（`M5UV2_xxxx`，走 `wlan1`）不受影响。
- 官方网页里的 WiFi 表单在这个固件里用不了：它提交到的 `/wifi_connect` 接口在服务端并不存在。
- 不小心切进 notebook（Jupyter）模式时，断电重启就能回来，这个模式不会保存。

## 说明

- 识别结果来自设备的 `POST /func/result`，系统状态通过 SSH 读取。账号密码默认用 M5Stack 公开的出厂值；改过密码的设备用环境变量指定，不要把真实密码写进仓库。
  可用的环境变量：`UNITV2_HOST`、`UNITV2_USER`、`UNITV2_PASS`、`PANEL_PORT`、`PANEL_NO_BROWSER=1`。
- 结果队列只能有一个消费者：不要同时打开官方网页，也不要在终端里同时运行 `curl .../func/result`，否则识别结果会被分走一部分。串口那一路（给 StackChan 的）不受影响。
- 名字只能用英文字母、数字、`_` 和 `-`。设备上的识别程序对中文名字的支持情况还没有验证过。
- 芯片温度到 95°C 时，设备会自动降频并停止识别服务，需要断电重启才能恢复。面板会显示红色提示。
