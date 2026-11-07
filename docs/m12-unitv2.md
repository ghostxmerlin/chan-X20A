# M12 摄像头（UnitV2 M12）探索记录

状态：2026-11-06～07 的探索结果。[Mac 监控面板](../m12/panel/README.md)已实测可用；第 7 节的常驻视觉程序还在讨论方向，没有设计文档，也没有开始实现。

标注约定：**实测**指在开发设备上做过；**文档**指来自 M5Stack 官方文档；**源码**指读 UnitV2Framework 得到；**推测**指尚未验证。

## 1. 在系统中的位置

- 硬件：M5Stack UnitV2 M12 版，SigmaStar SSD202D（双核 Cortex-A7 1.2GHz，128MB DDR3，官方规格），运行 Linux。零售商页面显示该型号已停产。
- 目标连接：一根 USB 给 StackChan 供电；StackChan 通过 Grove 给 M12 供 5V，同一根线上走 UART 115200 8N1。
- 供电方向（实测）：StackChan → M12 正常。反过来让 M12 通过 Grove 给 StackChan 供电，M12 开机约 8 分钟就触发了 95°C 过热保护，不要这样接。
- 分工：Mac 通过 WiFi 分别监控 M12（本面板）和 StackChan；手机只控制 StackChan，不直接操作 M12。
- 数据：M12 的识别结果始终写到串口，交给 StackChan。HTTP 结果队列是另一路拷贝，给 Mac 面板调试对照用（文档说两路内容相同，具体格式差异未核对）。

## 2. 连接与登录（实测）

- **USB**：Mac 上会出现一块 USB 网卡，Mac 分到 `10.254.239.2`，设备是 `10.254.239.1`（出厂固定地址）。网页服务是 Werkzeug，响应慢，开机约 30 秒后才能访问。
- **热点**：`M5UV2_xxxx`（WPA2，`wlan1`），和 USB 网卡桥接在 `br0` 上，地址同样是 `10.254.239.1`。M12 装进机身后，Mac 连上热点应该也能用同一套工具（推测）。
- **WiFi 客户端**：`wlan0`，出厂没有配置任何网络，用 `m12/panel/wifi_setup.py` 配置，只支持 2.4GHz。官方网页里的 WiFi 表单提交到 `/wifi_connect`，但服务端没有这个接口。
- **SSH**：使用 M5Stack 公开的出厂账号，sudo 用同一个密码。登录 shell 和 `/bin/sh` 都是 zsh。`m5stack` 用户不能写 `/dev/null`，所以脚本里不要用 `2>/dev/null`。远程脚本用 base64 编码后经管道交给 `sh` 执行。`$(...)` 里的 `| head` 管道不稳定，改用单个 awk。
- **notebook（Jupyter，80 端口）模式**不会保存，断电重启后回到功能模式。

## 3. 设备服务 `server_core.py`

- `POST /func`，参数 `{type_id, type_name, args}`，用来切换功能（实测）。已用到的编号：`camera_stream`=3、`face_recognition`=9、`face_detector`=10、`audio_fft`=13。这个调用会阻塞约 4 秒，其中 3 秒是固定等待。固件没有"停止"功能。
- `/video_feed`（MJPEG）只有在某个功能启动后才会出画面（实测）。
- `POST /func/result` 是长连接，结果之间用 `|` 分隔（实测）：
  - 必须先 `GET /` 一次（服务端借此把 `client_is_connected` 置为真），结果才会开始入队；
  - 只能有一个消费者，多个客户端同时拉会互相分走结果。
- `POST /data_to_device` 用于人脸登记，参数 `{operation: train|stoptrain|saverun|reset, face_id, name}`；`POST /data_from_device` 读取已登记的列表（实测）。
- 结果总是写到串口 `/dev/ttyS1`，115200（实测）。`audio_fft` 只输出 `{"meta":[...]}`，不会进串口，所以面板拿它当待机。
- 串口输入（文档）：
  - 切换功能：`{"function":"Face Detector","args":""}`，成功时回 `{"msg":"function switched to ..."}`；
  - 人脸登记：`{"config":"Face Recognition","operation":"train","face_id":1,"name":"tom"}`，另有 `stoptrain`、`saverun`、`reset`；
  - 一条命令中间不能有换行，只能在结尾换行。
  - 因此 StackChan 也能自己切换 M12 的功能。

## 4. 散热、风扇、麦克风、镜头（实测）

- **过热保护**：`/etc/thermal/check_thermal.py`（root 运行）。`temp_out` 达到 95°C 时，它会把调速器切到 powersave（400MHz）、结束视觉服务，并把 80 端口换成"过热"页面。之后不会自行恢复，需要断电重启。
- **温度读数**：`/sys/class/mstar/msys/TEMP_R`。
- **CPU 和温度**（2026-11-06）：

  | 运行的功能 | CPU | 温度 |
  | --- | --- | --- |
  | `camera_stream`（不看画面） | 约 70% | — |
  | `face_detector` | 约 96% | — |
  | `audio_fft` | 约 15% | 降到约 78°C |
  | 人脸识别 | — | 约 92–93°C，离保护线只有 2–3°C |

- **风扇**：没有软件控制，一直在转。GPIO 只有 0（白灯）、1（红灯）、2（输入）、14（WiFi 电源），没有 PWM。外壳压到风扇时会摩擦出声，留出一点缝隙后就不再摩擦。
- **麦克风**：用的是摄像头模组自带的 USB 音频（ALSA card 0 "USB Camera"），紧挨着风扇，录音会混进风扇噪声。
- **镜头**：M12 出厂时严重失焦，要把镜头往外拧出较多才能对上焦。

## 5. 帧率（用户实测）

- 人脸检测经常能到 7 帧/秒。
- 官方人脸识别只有 2–3 帧/秒，满足不了追踪。
- 切换功能时大约 4 秒没有任何输出。

## 6. 官方源码 UnitV2Framework（源码）

[m5stack/UnitV2Framework](https://github.com/m5stack/UnitV2Framework) 用 Apache-2.0 许可，包含全部内置功能的 C++ 源码，推理库是 ncnn。

- **编译**：工具链用 gcc-arm 10.2-2020.11 `arm-none-linux-gnueabihf`，依赖 OpenCV 4.4.0 加 contrib、ncnn（关闭 Vulkan）、ZBar。仓库里没有编译好的 `lib/`，需要自己编。
- **程序结构**：每个功能是一个独立程序，通过管道和 `server_core` 收发数据：收到 `config` 等于本功能名的 JSON 才处理，处理结果再发回去。
- **部署**：社区教程的做法是替换 `/home/m5stack/payload/bin/<功能名>`，原文件先备份。
- **人脸检测与人脸识别**：
  - 两者用同一个 RetinaFace mnet.25 模型，都把 640×480 缩到 320×240 再检测，置信度阈值 0.8，NMS 阈值 0.4。
  - 识别额外对每一帧里的每张脸跑一次 MobileFaceNet（112×112 输入，输出 128 维特征），再用余弦相似度和已登记的人比对，阈值 0.5。两者帧率差距完全来自这一步。
- **物体识别**：
  - 模型由所在文件夹里的 `model.json` 指定，支持 yolov3（Yolo-Fastest，320×320）和 nanodet（320）两种。
  - 内置 `yolo_20class`（20 类，含人、猫、狗、椅子、沙发、电视等）和 `nanodet_80class`（80 类，另含床、冰箱、烤箱、水槽、马桶等）。
  - 可以放入自定义模型。官方 V-Training 训练服务用的是 NanoDet，这个服务现在是否还能用没有确认。
- **在线分类**：用 ShuffleNetV2 x0.5 从画面中央 300×300 区域提取 1024 维特征，每一类保存平均特征，靠余弦相似度匹配，可以在设备上现场训练。
- **其他**：
  - 运动追踪：可以设定背景，背景缓慢变化时会自适应（文档）；
  - 目标追踪：MOSSE 算法，跟住给定的框；
  - 二维码识别：先用 MobileNet-SSD 找到码的位置，再交给 ZBar 解码。
- **编译坑**：社区报告用较新的编译器编出来的程序，在设备上会报 `GLIBC_2.33` 缺失，所以要严格用官方指定的编译器版本。开发用的 Mac 是 Apple Silicon，装了 Docker，可以在 Linux 容器里编译（推测，未试）。
- **版本未核对**：这份源码和设备固件里的程序、模型是不是同一版，还没有上设备核对。

## 7. 规划方向（讨论中，未定）

- **目标**：M12 只保留"待机"和"人脸"两个状态。人脸状态以检测为主，保持约 7 帧/秒用于追踪；识别按需进行，并且用同一路视频流。
- **思路**：在 `face_recognition.cpp` 的基础上做一个常驻程序。摄像头一直开着，模型常驻内存，模式切换在程序内部完成，不再重启进程。推测切换在下一帧就能生效，约 0.15 秒；待机时暂停推理。
  - 识别触发（草案，用户尚未确认）：新出现的脸自动识别一次，之后名字随追踪保留；StackChan 通过串口、Mac 通过面板都可以要求重新识别。
  - 保持官方功能名 "Face Recognition"、输出格式和登记命令不变，只新增一条操作，例如 `operation: recognize`。
  - 每识别一次，推测会让那一帧多花约 0.25 秒（由 7 帧/秒和 2–3 帧/秒估算）。是让这一帧停一下，还是把识别放到后台，还没定。
  - 之后可以加物体识别（认猫和常见物体），隔一段时间或按需跑一帧。
- **待确认**：
  - 设备剩余内存（总共 128MB）能不能同时装下多个模型；
  - 设备上的程序和模型是否与源码一致；
  - glibc 和 OpenCV 的版本；
  - 新程序实际的帧率和温度。
- **未决问题**：第一版要不要包含物体识别。
- **判断在哪个屋子**：没有现成功能。候选做法：
  - 从 80 类物体推断（床→卧室，冰箱、烤箱→厨房，马桶、水槽→卫生间，沙发、电视→客厅）；
  - 用在线分类对每个屋子现场训练；
  - 每个屋子贴一个二维码。

## 来源

- [UnitV2 内置功能文档](https://docs.m5stack.com/en/compute/unitv2/base_functions)
- [m5stack/UnitV2Framework](https://github.com/m5stack/UnitV2Framework)
- [M5UnitV2 交叉编译环境搭建（Qiita）](https://qiita.com/nnn112358/items/9e15450265b689300216)
- [CodeforKarlsruhe/kikaefer unitv2.md](https://github.com/CodeforKarlsruhe/kikaefer/blob/main/docs/unitv2.md)
- [V-Training 文档](https://docs.m5stack.com/ja/compute/unitv2/v_training)
