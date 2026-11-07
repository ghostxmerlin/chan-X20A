# 网页基础控制 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans: root implements, one independent reviewer checks the complete change.

**Goal:** 用户已确认网页加入 M12 开检测/退出待机、舵机左/右转、启停和仰角保存应用。

**Architecture:** 本机 Node 继续持有配对凭据，白名单 POST 转发到鉴权固件接口。M12 共用 ASR 的显式请求和机身页面；左右轴单次 5°，由原 motor task 执行，保留原厂限位/平滑和实体遥控。

**Tech Stack:** Node 24 标准库、原生 HTML/JS/CSS、ESP-IDF 5.5.4，固件 0.4.19。

**Spec:** 用户本轮要求及选项确认；保持当前黑绿终端、温度和实测反馈。打开/恢复/刷新网页不发动作，失败不自动重发。仰角 5–85° / 0.1°，明确保存并应用。

## Constraints / review focus

- 新命令必须来自本机同源 JSON POST 和当前 boot；凭据不进浏览器/argv。只接受确定命令，无任意 URL/SSH 入口。
- 单次发送、忙碌拒绝、过期/暂停禁用；机身重启后不重放，未确认不冒称执行成功。停用允许缺失反馈；启用不自动恢复旧动作。
- 左右步进以一秒内有效实测角为起点，5°、速度100、±128°限位，运动中拒绝。队列一秒过期，停用和实体遥控新输入取消未执行网页步进；不伪造遥控计数。
- M12 使用同一显式请求；HTTP 不直接动 LVGL/UART，ASR和触摸保持生效；识别/自动唤醒保持关闭。
- 仰角沿用原持久化和状态机，不改变当前保存值作为自动测试副作用。反馈不可用不伪造成目标到达。
- 本机工作分支隔离提交，沿用现有构建目录，避免复制工具链；部署核对目标/分区/CRC，备份只写ota_1并独立校验。

## Tasks

- [x] 1. 固件：新增可主机测试的离散步进队列及M12请求，测试过期/停用/限位；新增鉴权M12/yaw接口与boot校验，版本0.4.19。主机测试与构建。
- [x] 2. 网页：先写POST/同源/boot/忙碌/失败不重试测试；扩展curl JSON写入、控制白名单、界面按钮和真实执行状态。Node全套通过。
- [x] 3. 独立审阅；处理阻塞后部署固件和本机服务，真实接口/小幅动作/M12短时开关核验，恢复待机和原控制状态。
- [x] 4. 浏览器可用则桌面/窄屏验收，否则标记未验；更新说明交接、私密值筛查、本地提交，不推送。

## Execution ledger

- Scope confirmed by user. Source baseline 3fe67a0; clean checkout, existing local service. No new dependencies.

- Task 1: Python36 tests pass (4 pre-existing emotion dependency skips), IDF5.5.4 build passes. Initial default Python3.14 env missing; reused existing .tools/py313bin, no toolchain install/change.
- Task 2: Node25/25 pass. Curl real synthetic POST/202/redirect, same-origin/body/boot guards, no auto retry, UI explicit click/stale/dirty input covered.
- Final review: one Important physical/web motion race; added live settled check at consumption. Regression failed on false-settled consumption before fix, passed after fix; queued request discarded without replay. Moving/completion/cancel lifecycle coverage added in same safety regression. No remaining review findings.
- Review did not certify physical direction/fit, stop latency, UART responses, persisted hardware outcome, browser or deployment: these remain separate live checks, not inferred from source review.
- USB first/default and usb_reset attempts returned no serial data, zero flash writes; user replugged USB. Identity/partition/backup preflight resumed.

- Live finding: 0.4.19 successfully deployed, but passive yaw initialization leaves animation.done=false even without a motion command. Web buttons therefore stayed waiting_for_input. Add command-started idle distinction (no motion command vs active unfinished trajectory), test RED→GREEN, deliver distinct0.4.20 artifact. This does not weaken the active physical-trajectory guard.

- Final validation:0.4.20 app-only deployed and independently verified; actual first yaw idle now true. Chrome desktop/400px passed, M12 click→Face Detector/frames4.1results/s, ASR exit→standby. User used servo controls and reported some motion, set pitch30°; stored but feedback-3.7°prevented execution. No claim of measured arrival, no calibration changes or restoration of previous10°. Docs and final UI copy explain immediate attempt plus next-boot persistence.
