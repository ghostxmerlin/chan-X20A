# StackChan 资源与接口清单

盘点日期：2026-11-08。基线：`9659664`、`collector-0.4.30`、ESP-IDF **5.5.4**。
已部署应用：3,486,976 字节，SHA256 `06b6cd100f19b23386b6077d9f716caad8fac7d524709a7bc970ddd680dcc08e`。
本次只读取源码、构建产物、已有设备记录及网页状态，没有刷机、重启、改接线或驱动舵机。

## 开发前应先知道的结论

- **内部 RAM 是首要约束。** 短时测试的内部能力堆低水位指标为10,595字节；同一次开机的更晚记录为**1,447字节**。该API累加各heap区域自己的历史最小空闲值，**不代表曾在同一时刻总共只剩1,447字节**。
- PSRAM还有约5.5–5.9 MiB的记录余量；应继续把大缓存和允许使用外部内存的任务栈放在这里，但不能用它替代所有内部DMA内存。
- 当前应用槽余**1,690,368字节 / 1.612 MiB**。整块Flash剩余分区空间不等于这个应用可以直接增长的空间。
- **UART0/1/2全部占用**，分别连接M12、舵机和Unit ASR。Port A 的GPIO1/2是优先评估的扩展口；是否接新设备仍要核对电压、地址和带宽。
- DMA要分别看“内存”和“通道”。当前主要路径按源码推算占3个TX、4个RX GDMA通道，余量约2 TX / 1 RX；不是5组都可供新设备使用。
- 较晚记录还出现听觉累计80次重连、观察台音频累计7,325块跳过。不能把这些数字归因于内存，也不能沿用一分钟无错误窗口推断长期稳定。

## 计量和证据口径

本文KiB=1,024字节，MiB=1,048,576字节。硬件为512 KiB片内SRAM、8 MiB Quad PSRAM、16 MiB板载SPI Flash。用户习惯称“内部Flash”的存储，实际是板载非易失SPI Flash，不属于SRAM。[芯片规格](https://documentation.espressif.com/esp32_s3_datasheet_en.pdf)、[StackChan规格](https://docs.m5stack.com/en/StackChan)。

静态链接数、动态分配请求、运行时空闲数不能直接相加。DMA能力是一种内存属性，不是额外的一块RAM；本机既有内部DMA缓冲，也有外部PSRAM DMA缓冲。任务声明的栈大小也不等于实际已用栈。[IDF 5.5.4内存说明](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-reference/system/mem_alloc.html)。

本次直接读取设备超时，网页显示离线。因此以下运行时数值都有明确记录时间，**不是当前在线实时值**。最后网页缓存是2026-11-08 **14:23:03（Asia/Singapore）**，与12:56验收窗口属于同一次开机，设备当时已运行约89.9分钟；离线原因没有确定。

## RAM / PSRAM

### 静态链接结果

现有build的bin/ELF与已部署归档一致。固定工具链读取map得到：

| 项目 | 字节 | KiB | 解释 |
|---|---:|---:|---|
| D/IRAM静态总占用 | 178,947 | 174.75 | 以下三行之和 |
| 初始化数据 `.data` | 24,048 | 23.48 | 启动复制到RAM |
| 零初始化数据 `.bss` | 56,024 | 54.71 | 常驻RAM |
| IRAM代码与vectors | 98,875 | 96.56 | 内部指令/向量 |
| 链接器显示的剩余 | 162,813 | 159.00 | **不是运行时空闲堆** |
| Flash映射代码/只读数据等 | 3,363,908 | 3,285.07 | 与应用bin大小口径不同 |

启动日志普通内部heap区域原始长度合计275,052字节，另有8,168字节RTC heap；这些是启动阶段区域描述，也不是驱动/网络启动后的空闲值。不能用512 KiB减去上表静态值来计算运行时预算。

### 实机记录

| 指标 | 12:55–12:56并发验收 | 14:23:03最后网页缓存 |
|---|---:|---:|
| 内部空闲堆 | 24,291–38,055 B；结束30,443 B | **46,675 B / 45.58 KiB** |
| 最大连续内部空闲块 | 结束18,432 B / 18 KiB | **17,408 B / 17 KiB** |
| 内部能力堆历史低水位指标（各区域之和） | 10,595 B / 10.35 KiB | **1,447 B / 1.41 KiB** |
| PSRAM空闲 | 结束6,013,892 B / 5.735 MiB | **6,154,572 B / 5.869 MiB** |
| 音频HTTP订阅数 | 2 | 1 |
| 听觉状态 | 连续监听→网页停止待机 | listening |
| 听觉累计包 / 丢块 / 重连 | 最后采样972 / 0 / 0；停止尾段总976包 | 54,794 / 0 / **80** |
| 观察台音频累计跳块 | 0 | **7,325** |
| SD累计读取 / 失败 | 11 / 0 | 29 / 0 |

两种“丢块”分别属于后端听觉和HTTP音频订阅，不能混为同一指标。后一个快照只保留累计值，没有足够信息确定每次重连/跳块的原因、时间和负载。各区域的最低值可以发生在不同时刻，不能把1,447字节解释成一次同步采样，也不能单凭该值判断泄漏或断连根因。最大连续块只有约13.5–18 KiB的实测，仍说明大块内部申请需要谨慎。快照中相机/音频采集错误均为0，SD保持已挂载。

另外，第1/40段SD台词实播采样中PSRAM最少空闲5,795,224 B / 5.527 MiB，内部最大连续空闲块最少13,824 B / 13.5 KiB。不是所有40段、所有负载组合的最坏值。

当前遥测的`heap_free/heap_largest_free/heap_min_free`使用`MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT`；RTC可分配内存也可能在内，而RTC不是普通DMA池。**没有独立的DMA total/free/largest/min遥测，不能用内部heap值冒充精确DMA余量。** API口径见固定IDF `components/heap/heap_caps.c:289`与`esp_heap_caps.h`，代码：[collector.cpp](../upstream/StackChan/firmware/main/collector/collector.cpp#L913)。

### 主要缓冲区

下列为分配请求大小，不包含分配器元数据、驱动对象、JSON、Opus内部状态、网络缓存等开销；临时项目不一定同时存在。

| 功能 | 请求大小 | 存放/生命周期 |
|---|---:|---|
| 眼睛双压缩资产、双RGB565帧、scratch | **835,584 B / 816 KiB** | PSRAM常驻 |
| 音频采集环64块 | 123,904 B / 121 KiB | PSRAM常驻 |
| 听觉队列32块 | 31,488 B / 30.75 KiB | PSRAM常驻 |
| 原首页动画工作帧 | 124,800 B / 121.875 KiB | PSRAM |
| 爱心/叉号缓存 | 103,488 B / 101.06 KiB | PSRAM |
| 相机capture、driver backup、frame副本 | 各153,600 B / 各150 KiB | PSRAM；前两者参与DMA |
| JPEG转换输入临时缓冲 | 153,600 B / 150 KiB | PSRAM临时 |
| JPEG输出容量 | 每份180,736 B / 176.5 KiB | 未按实际JPEG长度缩容；慢消费者可能持有旧帧 |
| 最长台词PCM（clip29） | 364,800 B / 356.25 KiB | PSRAM播放期间 |
| 最长台词编码+PCM加载峰值 | 426,496 B / 416.5 KiB | 不含worker/对象 |
| HTTP任意PCM上传上限 | 384,000 B / 375 KiB | PSRAM播放期间 |
| 单文件资产安装上限 | 262,144 B / 256 KiB | PSRAM上传期间 |

LVGL日志中的“2MB image cache”是**容量上限**，不是启动就实占2 MiB。眼睛816 KiB是独立分配；2 MiB仅是LVGL缓存上限，不计入已确认实占。

主要源码：[eyes.cpp](../upstream/StackChan/firmware/main/collector/eyes/eyes.cpp#L54)、[collector.cpp](../upstream/StackChan/firmware/main/collector/collector.cpp#L453)、[hearing.cpp](../upstream/StackChan/firmware/main/collector/hearing.cpp#L266)、[playback.cpp](../upstream/StackChan/firmware/main/collector/playback.cpp#L374)、[screen.cpp](../upstream/StackChan/firmware/main/collector/screen.cpp)、[stackchan_camera.cc](../upstream/StackChan/firmware/main/hal/board/stackchan_camera.cc#L879)。

### 任务栈

| 任务组 | 内部RAM栈预算 | PSRAM栈预算 |
|---|---:|---:|
| collector sensors/environment/audio/camera/serial/discovery | 32 KiB | — |
| motor / ASR / M12 / temperature | 19 KiB | — |
| 两个HTTP server | 16 KiB | — |
| main / LVGL | 8 + 7 KiB | — |
| 音频订阅worker | 每路8 KiB，最多两路 | — |
| eyes / asset_install / playback | — | 各16 KiB |
| hearing | — | 32 KiB |

显式collector内部常驻栈约67 KiB，加main/LVGL为82 KiB；两路音频订阅再加16 KiB。尚未包含Wi-Fi、TCP/IP、事件、timer、idle、IPC、TCB等。四个媒体worker的PSRAM栈合计80 KiB。实测播放/听觉剩余栈为12,488 / 8,032字节；其他任务没有完整运行时栈余量清单。

当前PSRAM为Quad/80 MHz；普通malloc允许使用PSRAM，小于等于512 B的申请优先内部；为内部/DMA能力分配保留64 KiB。**64 KiB是分配策略，不保证始终空闲64 KiB。** BSS/noinit仍不外移，允许显式外部任务栈；Wi-Fi/LwIP未设置优先外部内存，socket上限16。配置见本机`sdkconfig`及`build/config/sdkconfig.h`；保留版本与现有依赖。

## DMA

### 内存

| 使用者 | 可确定的请求大小 | 内存类型 |
|---|---:|---|
| LCD绘制单缓冲，320×20×2 | 12,800 B | 内部DMA |
| I2S TX，6×240×2slot×2 | 5,760 B | 内部DMA |
| I2S RX，6×240×4slot×2 | 11,520 B | 内部DMA |
| I2S描述符与指针表 | 240 B | 内部；不含对象/队列/分配器 |
| DVP相机描述符，38×12 | 456 B | 内部DMA |
| DVP capture与backup | 各153,600 B | 当前驱动使用PSRAM DMA |
| SPI读写不满足对齐/能力时的临时缓冲 | 随交易变化 | 仍可能申请内部DMA |

LCD+I2S上述确定的内部请求已约30 KiB，另有描述符、SPI临时缓冲及其他驱动开销。不能把这个表当所有DMA实际占用的总计。通用PSRAM指针不保证任意外设可直接DMA；SPI的外部DMA需要标志、对齐与带宽条件。[IDF SPI说明](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-reference/peripherals/spi_master.html#transactions-with-data-on-psram)。

本轮修过的SPI RX临时分配失败清理崩溃，正说明这类内部内存峰值要单独预算；错误路径保护并不等于增加了可用内存。LCD DC / SD MISO共享GPIO35，必须保留LVGL锁、LCD DMA排空屏障和文件互斥。见[SD媒体](sd-media.md)。

### GDMA通道

ESP32-S3有5 TX + 5 RX GDMA通道。按当前已启用路径推算：

| 使用者 | TX | RX | 说明 |
|---|---:|---:|---|
| SPI3，LCD与SD共用 | 1 | 1 | 初始化总线时申请，两个设备不各占一对 |
| I2S0全双工 | 1 | 1 | 麦克风和扬声器；功放复位不释放TX通道 |
| LCD_CAM / DVP相机 | 0 | 1 | 正在使用esp_video + esp_cam_ctlr，不是旧esp32-camera实现 |
| SHA/AES共享加密DMA | 1 | 1 | 大块媒体SHA校验触发惰性初始化；SHA也保留RX；当前代码没有释放调用 |
| 合计 | **3** | **4** | 推算保留量 |
| 硬件方向余额 | **2** | **1** | 非实时通道注册表转储，不锁定具体编号 |

依据为固定IDF的`esp_driver_spi/src/gpspi/spi_common.c:244`、`esp_driver_i2s/i2s_common.c:837`、`esp_driver_cam/dvp/src/esp_cam_ctlr_dvp_gdma.c:83`、`mbedtls/port/crypto_shared_gdma/esp_crypto_shared_gdma.c:184`。两个HTTP音频订阅共用同一I2S采集，不新增I2S DMA通道。Wi-Fi自身工作也不应按音频订阅数重复计入上表。

## 板载Flash与SD

最新刷机前的实读分区表与构建表一致。16 MiB板载SPI Flash分配如下：

| 分区 | 起始地址 | 大小 | 本次可确认的使用情况 |
|---|---|---:|---|
| NVS | `0x9000` | 16 KiB | 已有配置；没有读取占用条目数 |
| otadata | `0xD000` | 8 KiB | 双OTA选择信息，不能当普通存储 |
| phy_init | `0xF000` | 4 KiB | PHY保留 |
| ota_0 | `0x20000` | 5,056 KiB | 备用应用槽；本次未读取其镜像占用 |
| **ota_1（当前）** | `0x510000` | **5,056 KiB** | 镜像3,405.25 KiB，占67.35%；余1,650.75 KiB |
| assets | `0xA00000` | 4,096 KiB | 历史资产分区；已分配不等于空闲，本次未盘读内容 |
| coredump | `0xE00000` | 64 KiB | 保留分区；不代表每次崩溃都有有效转储 |
| 尾部未分区 | `0xE10000` | 1,984 KiB | 需明确迁移/分区方案才能使用 |

其余低地址含bootloader、分区表及对齐保留区。当前只写ota_1；不为了利用尾部空间改分区，也不把ota_0或assets当已授权清理对象。[分区源码](../upstream/StackChan/firmware/partitions.csv)。

SD已有40条台词1,402,880 B和90个眼睛文件10,384,688 B，合计11,787,568 B / 11.24 MiB。固件保留目录/哈希，播放前读入PSRAM。没有读取SD的总容量、剩余簇或其他用户文件占用；上述仅是本项目已安装资源大小。

## GPIO与接口

方向均以ESP32为参照。仅统计`main/main.cpp → collector::run()`可达路径；factory固件、其他板型和未启用驱动不作为当前初始化证据。物理已连接但软件未用的脚仍单独保留。

### 控制器与物理引脚

| 接口 | ESP32 GPIO | 控制器 / 参数 | 当前用途与约束 |
|---|---|---|---|
| 舵机TX / RX | **6 / 7** | **UART1，1,000,000 baud，8N1** | yaw ID1 / pitch ID2共用串口总线；遥控俯仰仍屏蔽，串口资源不释放 |
| M12 TX / RX | **17 / 18** | **UART0，115200，8N1** | Port C蓝口；待机也占用串口 |
| Unit ASR RX | **8** | **UART2，115200，8N1，RX-only** | Port B黑口；GPIO9接模组RX但当前不配置TX，不能当空闲脚 |
| 内部I2C SDA / SCL | **12 / 11** | **I2C1**，设备各用100/400kHz | 音频控制、摄像头SCCB、触屏、IMU、PMIC和扩展器共享 |
| LCD+SD MOSI / SCLK | **37 / 36** | **SPI3** | 两设备共享一条SPI总线 |
| LCD CS / DC | **3 / 35** | SPI mode2，40MHz | DC与SD MISO共享35；reset经AW9523 |
| SD CS / MISO | **4 / 35** | SDSPI，上限20MHz | 必须经现有文件互斥与LCD交接屏障 |
| 音频MCLK / WS / BCLK | **0 / 33 / 34** | **I2S0**，24kHz | ES7210 / AW88298共享时钟；GPIO0也是启动相关脚 |
| 音频DIN / DOUT | **14 / 13** | I2S0 RX / TX | 麦克风和扬声器；输出静音不释放I2S TX/DMA |
| 相机D0…D7 | **39,40,41,42,15,16,48,47** | **LCD_CAM / DVP controller0** | GC0308 8bit并行数据 |
| 相机VSYNC / HREF / PCLK | **46 / 38 / 45** | DVP | 相机输入；45/46也是启动相关脚 |
| 相机XCLK | 无ESP32 GPIO | 外部20MHz晶振 | 当前不申请LEDC；Kconfig中的LEDC选项不代表实际分配 |
| USB D− / D+ | **19 / 20** | USB Serial/JTAG | 串口日志/JSON配置；不占UART0/1/2 |
| 扩展器IRQ | **21** | AW9523 INTN | 硬件已连；目前无该GPIO ISR，触屏20ms轮询 |
| 红外发送 / 接收 | **5 / 10** | IR_SEND / IR_REC | 当前collector未接入，但硬件已连接 |
| **Port A SDA / SCL** | **2 / 1** | Grove红口；可评估I2C0 | **当前未初始化，优先扩展候选**；恢复factory GPIO2 laser代码会冲突 |
| M5-Bus TXD0 / RXD0标签脚 | **43 / 44** | 物理引出，当前无业务绑定 | UART0已重映射17/18；这不是额外UART，整机接线可达性未验 |
| Flash / PSRAM | **26–32** | MSPI / SPI0/1 | 板载存储专用，不能作为GPIO扩展 |

Flash/Quad PSRAM详细映射：26=PSRAM CS1，29=Flash CS0，30=CLK，31=Q/IO1，32=D/IO0，27=HD/IO3，28=WP/IO2；后五个数据/时钟信号由两存储器共用。GPIO22–25不是ESP32-S3可用GPIO编号，不能按连续0–48列表补成空闲脚。

以上依据当前板级[config.h](../upstream/StackChan/firmware/main/hal/board/config.h)、[stackchan.cc](../upstream/StackChan/firmware/main/hal/board/stackchan.cc#L521)、[M12](../upstream/StackChan/firmware/main/collector/m12_unit.cpp#L19)、[ASR](../upstream/StackChan/firmware/main/collector/asr_unit.cpp#L19)、[舵机初始化](../upstream/StackChan/firmware/main/hal/hal_servo.cpp#L361)、[音频初始化](../upstream/StackChan/firmware/main/hal/board/cores3_audio_codec.cc#L105)。物理连接交叉核对[StackChan PinMap](https://docs.m5stack.com/en/StackChan)、[CoreS3原理图第3/5页](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/490/Sch_M5_CoreS3_v1.0.pdf)。

### I2C地址（全部为7bit）

| 地址 | 设备 | 当前软件状态 |
|---|---|---|
| `0x21` | GC0308 SCCB | 100kHz，复用I2C1 |
| `0x23` | LTR553光照/接近 | 400kHz，周期采集 |
| `0x34` | AXP2101电源管理 | 400kHz，初始化/电量轮询 |
| `0x36` | AW88298功放 | 100kHz，按播放请求启用codec；启动时硬件复位 |
| `0x38` | FT6336U触屏 | 400kHz，20ms轮询 |
| `0x40` | ES7210麦克风ADC | 100kHz，音频任务初始化 |
| `0x58` | AW9523B板内扩展器 | 400kHz，复位/电源路径控制 |
| `0x68` | Si12T头部触摸 | 100kHz，约100ms读取 |
| `0x69` | BMI270 | 400kHz，50Hz传感器采集 |
| `0x6F` | 底座PY32L020扩展器 | 100kHz；collector初始化舵机VM_EN |
| `0x10` | BMM150 | **在BMI270 AUX子总线**，不是ESP32另一组I2C GPIO |
| `0x51` | BM8563 RTC | 硬件存在，当前collector不调用旧rtc_init |
| `0x41` / `0x50` | INA226 / ST25R3916 NFC | 硬件已配置地址；当前collector无专用初始化，不应复用这些地址 |

ES7210/AW88298部分头文件写的是8bit地址`0x80/0x6C`，codec控制层会右移一位；对外清单必须用`0x40/0x36`。初始化配置不保证每个器件当前数据有效，本次未重新扫描I2C或验证器件响应。

依据：[传感器采集](../upstream/StackChan/firmware/main/collector/collector.cpp#L296)、[音频控制](../upstream/StackChan/firmware/main/hal/board/cores3_audio_codec.cc#L36)、[板内初始化](../upstream/StackChan/firmware/main/hal/board/stackchan.cc#L562)、[底座扩展器](../upstream/StackChan/firmware/main/hal/hal_io_expander.cpp#L17)。

### 扩展器及其他资源

| 扩展引脚 | 已接用途 | 当前状态 |
|---|---|---|
| AW P0.0 / P0.1 | TOUCH_RST / BUS_OUT_EN | 输出高 |
| AW P0.2 | AW_RST功放复位 | 启动输出低，播放流程控制 |
| AW P0.3 / P0.4 | ES_INT / TF_SW卡检测 | 输入；没有对应中断消费/SD热插拔管理 |
| AW P0.5 | USB_OTG_EN | 输出低；当前不启用USB Host |
| AW P1.0 / P1.1 | CAM_RST / LCD_RST | 相机输出高；LCD初始化低→高脉冲 |
| AW P1.2 / P1.3 | TOUCH_INT / AW_INT | 输入；触屏目前轮询 |
| AW P1.7 | BOOST_EN | 输出高 |
| PY32索引0（官方IO1） | VM_EN | 舵机电源开启；与禁止遥控俯仰是不同层次 |
| PY32索引13（官方IO14） | WS2812 RGB灯链 | factory代码存在，collector提前return，当前未初始化灯控制 |

这些是扩展芯片的引脚，不能混作ESP32 GPIO；未使用的扩展位也不等于有可直接接出的接口。AW方向配置P0=`0x18`、P1=`0x0C`；collector P0输出=`0x03`，LCD reset结束后P1输出=`0x83`。背光由AXP2101 **DLDO1** 控制，不占LEDC；串口舵机的内部PWM模式也不占ESP32 LEDC。

| 控制器/功能 | 当前余量结论 |
|---|---|
| UART | **0个未用控制器**；ASR未用TX不等于多一个串口 |
| I2C0 / SPI2 / I2S1 | 当前collector未创建；仍需分配实际GPIO、DMA/内存和供电预算 |
| LEDC | 当前可达硬件链无分配；相机外部晶振、背光PMIC、舵机串口 |
| Wi-Fi / ESP-NOW | 共用2.4GHz无线资源；遥控不额外占UART/GPIO，网络并发仍消耗内部内存 |
| 原ESP-DL人脸检测/旧云业务 | 不属于当前collector启用功能；不要把遗留源码缓冲重复计入 |

## 后续开发预算

1. 先补测长时间并发与同步内存峰值：当前1.41 KiB为多个heap区域历史最低值之和，不是同步总空闲；结合约13.5–18 KiB的连续块实测，尚不能承诺新增内部常驻大缓冲。80次重连另查会话日志，不能先当作内存泄漏结论。
2. 大数据优先SD保存、PSRAM按需缓存；继续避免逐次创建内部栈任务，以及每帧/每块反复申请内部DMA缓冲。
3. 新功能同时提交“新增内部常驻、内部峰值、PSRAM常驻/峰值、任务栈、DMA方向通道、GPIO/地址”预算；当前没有可信的统一安全字节额度。
4. 建议下一轮诊断增加DMA能力堆的total/free/largest/min、PSRAM最大连续块/低水位、所有任务栈余量和任务数。现有内部heap数据不能替代这些指标。
5. 优先评估Port A / I2C0扩展；新增UART设备需明确替换、复用或外部扩展方案。SPI2/I2S1等控制器即使未初始化，也不等于有可直接使用的接头和GPIO。

## 复核依据

- 本地证据：`logs/resource-inventory-20261108/`，包含linker-size、短测汇总和网页最后缓存；原始设备配置/标识不提交。
- 实机测试源：`logs/sd-v0430-dma/{live-check,voice-smoke,eyes-smoke}.json`；最新分区读取为`.private/sd-v0430-dma/partitions.bin`。
- [实机验证记录](collector-validation.md)、[当前交接](session-handoff.md)、[SD媒体机制](sd-media.md)。
- 本次运行时快照缺少当前在线数据、精确DMA余量、全任务高水位、NVS条目占用和SD空闲空间；这些缺项明确保留，未用静态估算冒充实测。
