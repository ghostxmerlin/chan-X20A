# DualKey 遥控固件（dualkey-0.1.2）

M5Stack **Chain DualKey**（C147，ESP32-S3FN8，8 MB Flash，无 PSRAM）加一个 **Chain Joystick**（U205），用作机器人 hello 的手持遥控。
它通过 Wi-Fi 直接连到耳朵服务器的 `/signal`，上报按键打分、摇杆方向和摇杆点击。意义由服务器决定，设备只上报原始事件。

## 硬件（已核对官方资料）

| 部件 | 管脚 | 本固件的用法 |
|---|---|---|
| KEY_1（离挂绳孔**远**的键） | G0（低电平=按下） | **好**（polarity +1） |
| KEY_2（离挂绳孔**近**的键） | G17（低电平=按下） | **坏**（polarity −1） |
| 2 颗 WS2812B | 数据 G21，电源 G40（开漏，**低电平=通电**，同官方 `bsp_ws2812_enable`） | 状态灯 |
| 三档开关 | G8 / G7（只读 ADC） | **不碰**。官方警告：不能把它们配成高电平输出，否则不能正常关机。 |
| Chain 口 | UART2 G6(TX)/G5(RX)，UART1 G48(TX)/G47(RX) | 两个口都扫描，找到摇杆就用 |

- 产品页（管脚表、G7/G8 警告）：<https://docs.m5stack.com/en/products/sku/C147>
- 摇杆：<https://docs.m5stack.com/en/products/sku/U205>
- 出厂示例工程（本固件复用其 IDF 版 M5Chain 组件、管脚定义和摇杆用法）：<https://github.com/m5stack/M5DualKey-UserDemo>（commit `6f8e3cca`），`components/esp_duo/include/bsp/keyboard.h`、`main/chain_bus/chain_bus.cpp`
- M5Chain 摇杆示例：<https://github.com/m5stack/M5Chain/tree/main/examples/Joystick_Example>

关机：开关拨到中间、拔掉 USB。没有复位键：重启 = 中间档拔插 USB。
下载模式（万一刷坏）：开关拨到中间，按住 KEY_1，插上 USB，再松开。

### 摇杆

- 用官方 M5Chain（ESP-IDF 移植版，MIT，见 `components/m5chain/`，只编译 ChainCommon 和 ChainJoystick，源码未改）。
- 每 20 ms 读一次 `getJoystickMappedInt8Value`（−127…127）。
- **装配方向修正（0.1.1）**：这台 DualKey 上摇杆转了 90°。用户 2026-11-13 实测（0.1.0 未修正）：物理上推→读成右，下推→左，左推→上，右推→下。
  `dualkey_logic.h` 的 `kMountRotate = 90` 把读数转回物理方向，所以上报的方向就是用户手里推的方向（握法：按键朝上、正常持握）。
- 方向：任一轴过 76（60%）且另一轴不超过它的 0.6 倍（斜推不算），保持 150 ms 发一次；降到 60 以下才算松开（回差）；两轴都回到 30 以内才能再触发。
- 点击：摇杆按键设为上报模式，双击窗口 200 ms（同官方示例）。单击会在松开后约 200 ms 才报出（摇杆自己的固件要等双击窗口结束）。
- **长按 = 按住 1 秒（0.1.2）**：摇杆自己的长按最短 3 s（可设 3–10 s），不到 3 s 松开一律报单击。
  2026-11-13 实测原始包：按住 1429 ms → `AA 55 05 00 01 E0 00 00 E1 55 AA`（单击）；按住 4037 ms → 第 3018 ms 收到 `… E0 02 00 E3 …`（长按）。
  所以 0.1.2 每次循环读按键电平，按住满 1 s 就发 `long`（不用等松手），并丢掉摇杆对同一次按压的单击或 3 s 长按（`dualkey_logic.h` 的 `ClickFuser`）。
- 诊断日志默认关闭：`DUALKEY_DIAG=1`（摇杆设置回读、按键边沿、事件值），`DUALKEY_CHAIN_E0_DUMP`（原始 0xE0 包）。
- 如果以后换了装法，可以用 `joystick_rotate`（0/90/180/270，叠加在内置的 90° 上）修正，不用改固件。

## 按键打分规则

- 两次按压间隔 ≤ 350 ms 算同一组；间隔超过 350 ms，上一组立即结束、开始新的一组。
- 最后一次按压后 500 ms 没有再按，这一组结束并发送。
- 最多计 5 下；更多的按压标 `capped: true`，也会延长这一组。
- 按另一颗键会结束当前这一组。
- 两颗键同时按下：当前这一组作废（黄灯），留给以后的"重置"手势。

## 协议（设备 → 服务器）

WebSocket `<server>/signal`，请求头 `Authorization: Bearer <token>`，JSON 文本帧：

```
{"type":"hello","device":"dualkey-e8ae1c","fw":"dualkey-0.1.2","boot":"<每次开机随机 16 位十六进制>"}
{"type":"signal","seq":1,"age_ms":612,"kind":"rating","value":{"polarity":1,"count":3,"presses_ms":[0,180,370],"capped":false}}
{"type":"signal","seq":2,"age_ms":160,"kind":"mode","value":{"direction":"up"}}
{"type":"signal","seq":3,"age_ms":0,"kind":"click","value":{"type":"single"}}     // 还有 "double"、"long"
{"type":"ping"}                                                                     // 每 15 s
```

- `seq` 每次开机从 1 开始；同一条信号重发时 `seq` 不变，`age_ms` 变大。服务器按 `device + boot + seq` 去重。
- `age_ms`：打分是从第一下按压到发送；模式是从推过阈值到发送；点击是从收到摇杆事件到发送。
- 服务器回 `{"type":"hello","ok":true}` 后设备才开始发信号；收到 `{"type":"ack","seq":N}` 才从队列删除。
- 未确认的信号存在内存队列里（32 条，满了丢最旧的并计数），重连后按顺序重发。
- 断线重连间隔 1 s 起翻倍，最长 30 s；收到服务器 hello 后归零。
- 可选的 `{"type":"mode","current":"<名称>"}` 会让摇杆自己的灯亮一个由名称决定的暗色；`none`、`idle` 或空字符串关灯。

## 灯

| 灯 | 意思 |
|---|---|
| 每按一下：白色短闪 | 收到按键 |
| 绿 0.5 s / 红 0.5 s | 一组"好"/"坏"结束并进入发送队列 |
| 青色短闪（摇杆灯同时亮方向色） | 摇杆方向触发 |
| 黄色 0.3 s | 两键同按，作废 |
| 每秒一下紫色 | 还没配置 Wi-Fi / 服务器 / 令牌 |
| 每 2 秒两下橙色 | 连不上 Wi-Fi |
| 每 2 秒一下蓝色 | 有 Wi-Fi，连不上服务器（或服务器没回 hello） |
| 不亮 | 已连上 |

## 配置网络（USB）

```
cd <repo root>
PY=.tools/espressif/python_env/idf5.5_py3.13_env/bin/python
$PY tools/dualkey_provision.py                 # 问 SSID 和密码；服务器和令牌取自 .env 的 SOUL_EAR_URL / SOUL_EAR_TOKEN
$PY tools/dualkey_provision.py --ssid A --ssid2 B   # 两个网络
$PY tools/dualkey_provision.py --mock          # 连本机模拟服务器（先启动它）
$PY tools/dualkey_provision.py --no-wifi       # 只改服务器和令牌
$PY tools/dualkey_provision.py --status        # 只看状态
$PY tools/dualkey_provision.py --no-wifi --joystick-rotate 90
```

脚本按 USB 序列号 `3C:0F:02:E8:AE:1C` 找 DualKey（`tools/dualkey_usb.py`），不会打开机器人的口。令牌和密码不显示，设备也不回显。
打开 USB 口会让 DualKey 重启一次，这是正常的。

串口协议（一行一个 JSON）：

- `{"wifi":[{"ssid":"…","password":"…"},{…}],"server":"ws://host:port","token":"…","joystick_rotate":0}` → `{"ok":true}`。每一项都可以省略；`wifi` 会整体替换已存的网络（最多 2 个）。保存后马上生效，不用重启。
- `{"status":1}` → `{"ok":true,"status":{…}}`：Wi-Fi、IP、RSSI、服务器地址、是否设置了令牌、是否已握手、队列、摇杆读数、固件版本。
- `{"reboot":1}`、`{"forget":1}`（清掉本固件存的全部设置）。

设置存在 NVS 的 `soulkey` 命名空间。出厂固件自己的 NVS 数据不动；Wi-Fi 驱动用 RAM 存储，不改出厂的 Wi-Fi 记录。

## 本机模拟服务器

```
$PY tools/dualkey_mock_server.py              # 绑定 Mac 的局域网 IP，端口 52790
```

它按上面的协议握手、确认每条信号，在终端里用中文打印事件，并写入 `logs/dualkey-<日期>/mock-events.jsonl`。
浏览器打开 `http://<Mac IP>:52790/` 可以看实时事件页。令牌是一次性的，存在 `.private/dualkey-mock-token`。

## 构建

用仓库固定的 ESP-IDF 5.5.4，不升级：

```
cd <repo root>
export IDF_PATH=$PWD/.tools/esp-idf-v5.5.4 IDF_TOOLS_PATH=$PWD/.tools/espressif
export IDF_PYTHON_ENV_PATH=$PWD/.tools/espressif/python_env/idf5.5_py3.13_env   # Homebrew python 已是 3.14
source $IDF_PATH/export.sh
idf.py -C dualkey build
```

组件：`espressif/led_strip ~3.0.3`（和机器人固件同版本）、`espressif/esp_websocket_client ^1.5`，版本锁在 `dependencies.lock`。
不用 Arduino：官方出厂工程本身就是 ESP-IDF，M5Chain 已有 IDF 版。蓝牙关闭（省电、省内存）。

## 刷机（只写应用分区）

```
python3 tools/dualkey_flash.py identify     # flash_id + read_mac + 安全信息 + 分区表，核对 MAC 3c:0f:02:e8:ae:1c、8 MB
python3 tools/dualkey_flash.py backup       # 整片 8 MB 备份到 backups/，再 verify_flash
python3 tools/dualkey_flash.py write dualkey/build/dualkey.bin   # 只写 factory 0x20000，然后独立 verify_flash
```

每次 esptool 调用前都按 USB 序列号重新找口，对不上就中止。不写 bootloader、分区表、NVS、`fctry`、`esp_secure_cert`、`nvs_key`。

设备上实读的分区表（和官方 `partitions.csv` 一致，本目录的 `partitions.csv` 是它的副本，只用于构建，不刷写）：

| 名称 | 类型 | 偏移 | 大小 |
|---|---|---|---|
| esp_secure_cert | 0x3F | 0xD000 | 0x2000（encrypted 标志；本机未开 Flash 加密） |
| nvs_key | data/nvs_keys | 0xF000 | 0x1000 |
| nvs | data/nvs | 0x10000 | 0x6000 |
| phy_init | data/phy | 0x16000 | 0x1000 |
| factory | app | 0x20000 | 0x380000 |
| fctry | data/nvs | 0x3A0000 | 0x6000 |

出厂时 factory 分区里是 `Luna-Remote`（版本 `698eac4-dirty`，2026-10-26 编译，ESP-IDF v5.5.3），不是 GitHub 上的 M5DualKey-UserDemo。

### 恢复出厂（整片还原）

备份：`backups/dualkey-e8ae1c-factory-20261113.bin`（8,388,608 B，SHA256 `768864ce8a5830a3fe7ad878549346b5a3297da816104637e3ea390ef43750df`）。

```
python3 tools/dualkey_flash.py restore backups/dualkey-e8ae1c-factory-20261113.bin
```

等价的 esptool 命令（先用 `python3 tools/dualkey_usb.py` 确认端口是 DualKey）：

```
.tools/espressif/python_env/idf5.5_py3.13_env/bin/python -m esptool --chip esp32s3 \
  --port "$(python3 tools/dualkey_usb.py)" --baud 921600 write_flash 0x0 backups/dualkey-e8ae1c-factory-20261113.bin
.tools/espressif/python_env/idf5.5_py3.13_env/bin/python -m esptool --chip esp32s3 \
  --port "$(python3 tools/dualkey_usb.py)" verify_flash 0x0 backups/dualkey-e8ae1c-factory-20261113.bin
```

如果 USB 口找不到（固件跑飞），先进下载模式（上面"下载模式"）。

## 测试

```
python3 -m unittest tests.test_dualkey_logic
```

`main/dualkey_logic.h` 是不依赖 ESP-IDF 的纯逻辑（按键去抖、打分分组、摇杆方向、信号队列、退避），
`tests/dualkey_logic_test.cpp` 在 Mac 上用 ASan/UBSan 编译运行。

## 已知限制

- 电池：Wi-Fi 常连 + 摇杆，估计 3.5–5 小时（见设计文档审计"重要 7"），未实测。已开调制解调器睡眠，没开动态调频。
- 令牌存在设备 Flash 里（调试阶段不处理）。
- 摇杆单击固定晚约 200 ms（摇杆固件的双击窗口）。
- 只支持 IPv4；`wss://` 用 IDF 证书包，未实测。
