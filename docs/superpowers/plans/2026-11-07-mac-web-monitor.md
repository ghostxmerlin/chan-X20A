# Mac 网页观察台 implementation plan

> **For agentic workers:** Use the native execution workflow; root implements, then an independent reviewer checks the completed change.

**Goal:** Mac 与 StackChan 在同一 Wi-Fi 时，打开本机网页查看真实状态，复用 Android 的现有接口。

**Architecture:** Node.js 标准库运行 127.0.0.1 本地服务；设备请求经系统 curl，保留随机挑战/HMAC 身份校验，凭据仅 `.private/`。浏览器接收单份缓存的遥测、相机和音频数据，不直接访问配对令牌。无需固件改动。

**Tech Stack:** Node 24 标准库、curl、原生 HTML/CSS/JS/Canvas/Web Audio、node:test。

**Spec:** 用户要求修复 Mac 对 StackChan 的监控，像 Android 观察台一样通过网页查看。首版只读，保留基础版停用范围。

## Global constraints

- 不刷机、不改变舵机/角度/音量/M12 工作模式、不启动情绪或服务器 AI。
- 首页显示机身相机、真实第一音频通道 RMS、四秒六轴曲线、电池/网络/光照/接近/磁场、舵机反馈、ASR/M12 与连接事件。
- 声音监听默认关闭，显式点击才播放，断线/后台/关闭后不自动恢复。无持久录音。
- 连接事件与遥测变化不冒充完整固件串口日志；现有固件没有 Wi-Fi syslog 端点。
- 数据带新鲜度；断线和 reboot 清理旧帧/波形/曲线。无页面访问时停止设备采集，释放音频客户端。
- 绑定文件 0600；浏览器/日志/公共源码无配对令牌；只绑定 loopback，检查 Host/Origin/Fetch-Site。

## Design

- 延续用户既有黑绿观察台：黑 #000000、面板 #060C09、绿 #79FF97、次要 #72A980、线 #244C32、告警 #F5C36B。
- 字体使用本机等宽数据字体与 PingFang SC；参数对齐，正文不小于 14px。
- 机身相机占主视觉；右侧网络/电源/舵机，下面音量/六轴，底部 ASR/M12 与事件。移动端按阅读顺序折叠。
- 只保留有操作意义的按钮：声音监听、暂停/恢复监控。没有营销文案或无关图形。

## Review focus

- 身份挑战失败不得发送 Bearer；重定向不得泄露凭据。
- PCM 分片、序号缺口/回绕、错误格式有界处理，波形只来自第一通道。
- 无人浏览时不占设备；重复开页共享采集，慢浏览器不能堆积音频。
- 断线/重启显示过期，不把请求成功当作有效舵机反馈或 M12 执行成功。
- 首次页面与窄屏可读；听音只由用户启动，事件与完整设备日志边界明确。

### Task 1: 本地数据桥

- [x] 写协议/身份验证/只读本地 API 测试并看到失败。
- [x] `web/monitor/core.mjs` 实现 curl 传输、身份校验、PCM 解码、共享采集与新鲜度。
- [x] `web/monitor/server.mjs` 提供静态页、SSE、缓存帧和音频；不提供设备写接口。
- [x] `web/monitor/service.mjs` 提供 start/stop/status，配置只在 `.private/`。

### Task 2: 网页

- [x] 实现 `web/monitor/public/{index.html,style.css,app.js}` 和真实状态/过期提示。
- [x] 加入手动监听、四秒图表和事件，浏览器切后台自动静音。
- [x] 提供双击入口 `scripts/open-stackchan-monitor.command` 与 README。

### Task 3: 验证与交付

- [x] 所有 Node 测试通过，运行本机服务并连接实际机器人。
- [x] 浏览器检查桌面/窄屏、真实画面/数据、暂停恢复；默认不播放声音。
- [x] 独立审阅及必要修正；更新项目交接、操作说明并本地提交。
- [x] 保留本机观察台运行，交付可打开的本地网址；不安装登录自启。

## Execution ledger

- Task 1: 协议/PCM 七项最初失败，实现后通过；本地 API 首轮失败定位为 Node fetch 重写 Host，改真实 HTTP 发包后边界测试通过。音频/视频取消与历史 ASR 再补回归。
- Task 2: 原生 HTML/CSS/JS 完成；Chrome 桌面和 400px 真实设备画面/曲线通过，暂停/恢复、手动播放和后台自动关闭通过。启动桥空 boot 不再被前端误报成机器人重启。
- Ruling: 内置浏览器宿主不可用，使用 CUA 原生 Chrome Guest — 仍检查实际页面 — 代价是没有 Playwright DOM 自动化报告。
- Ruling: 首版使用已有本机绑定地址，不增加 UDP 自动发现 — 当前同网地址经真实 HMAC/数据验证 — DHCP 改地址需要更新配置，README 明示。
- Task 3: 11 项最终测试通过。独立审阅唯一阻塞为 curl 继承 .curlrc；假凭据回归先观察两次请求失败，首参数 -q 后单次请求/无 trace，通过独立复核。
- 实机 20.128s / 80 样本三路全部新鲜，观测画面约 3.58fps、音频无新增缺包；第二网页订阅不增加设备音频客户端。没有设备控制/刷写/重启。
- 详细结果/验收边界与本机证据路径已写交接和验证记录；服务保持运行、页面已打开、外放关闭，不装登录自启。
