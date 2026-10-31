# ESP32 / StackChan 开发辅助工具

核对日期：2026-10-30。当前工程以 M5Stack 出厂固件 1.6.0、ESP-IDF 5.5.4 为基准。

## 已安装的 MCP

根据用户指示，已通过 Codex CLI 将以下两个远程 MCP 加入 `~/.codex/config.toml`，均已启用：

- `espressif-documentation`：`https://mcp.espressif.com/docs`，已完成 OAuth 登录。先前独立客户端发现服务版本 3.3.1；重启后的本会话已直接调用 `search_espressif_sources`，实际文档检索通过。
- `esp-component-registry`：`https://components.espressif.com/mcp`，无需登录。重启后的本会话已直接调用 `search_components` 和 `fetch_component_detailed_information`，搜索及详情读取均通过。

安装时使用 Codex CLI `0.162.0-alpha.20`。这是用户级配置；安装当时的旧会话未加载新工具，重启后的本会话已加载并完成调用，无需重新安装或登录。原配置已备份到 `~/.codex/backups/config.before-espressif-mcp-20261030-224019.toml`，已核对其他设置保持一致。没有安装本地构建 MCP，也没有改动 ESP-IDF 版本。

可使用以下命令查看或重新登录：

```bash
codex mcp list
codex mcp login espressif-documentation
```

配置方式依据 [官方 OpenAI MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)及下列乐鑫官方端点清单。组件搜索验证记录为 `logs/mcp-component-registry-search.json`，文档 MCP 的认证与工具发现记录为 `logs/mcp-documentation-tools.json`，注册状态记录为 `logs/mcp-installed-status.json`。

### 重启后的实际检索验证

2026-10-30 查询：“ESP32-S3，ESP-IDF v5.5.4：使用 ESP-NOW 的两台设备需要满足哪些 Wi-Fi 信道条件？”文档 MCP 返回 14 条资料，包含 ESP32-S3 的 ESP-NOW 文档，调用成功；原始检索结果保存在 `logs/mcp-documentation-query-20261030.json`。

返回链接指向 `latest` / `master`，也混有其他芯片资料，**并未按查询限定到 5.5.4**。已另行打开 [ESP32-S3 / IDF 5.5.4 固定版本文档](https://docs.espressif.com/projects/esp-idf/zh_CN/v5.5.4/esp32s3/api-reference/network/esp_now.html)，并对照本地 SDK 的 `docs/zh_CN/api-reference/network/esp_now.rst:97`、`examples/wifi/espnow/README.md:46`、`components/esp_wifi/include/esp_now.h:53`：收发双方须同信道；peer 的 `channel=0` 表示使用当前信道，非零值必须与本机所用信道一致。以后检索仍需逐条核对芯片和版本。

组件详情查询返回 `espressif/esp-now` 2.5.3，支持 ESP-IDF >=4.4；记录在 `logs/mcp-component-detail-20261030.json`。这是注册表当次返回的版本，不是本项目依赖变更；项目继续使用 `repos.json` 固定的 ESP-NOW 提交和原有锁文件。

## 推荐的官方 MCP

| 工具 | 用途 | 本次验证 | 与当前项目的关系 |
| --- | --- | --- | --- |
| [Espressif Documentation](https://developer.espressif.com/blog/2026/04/doc-mcp-server/) | 查询芯片、ESP-IDF、硬件设计及部分 M5Stack 官方资料 | 已注册并启用，OAuth、工具发现和本会话实际文档检索通过 | 优先用于 API、硬件和版本问题；查询时明确 ESP32-S3、IDF 5.5.4，并核对返回版本 |
| [ESP Component Registry](https://mcp.espressif.com/) | 搜索组件、版本、依赖及例程 | 已注册并启用；初始化、工具发现、组件搜索和详情读取通过；先前发现服务版本 3.4.7 | 可辅助挑选驱动；重现出厂固件时继续保留现有依赖锁文件 |
| [Espressif Engineering](https://ts-mcp.espressif.com/) | 按需加载乐鑫排障、环境和硬件设计相关 skills | 端点可达，未登录返回 HTTP 401 | 可用于后续 Wi-Fi、硬件和工具链排障；尚未验证登录后的工具调用 |

官方端点清单来自 [Espressif MCP Servers](https://mcp.espressif.com/)：

```text
https://mcp.espressif.com/docs
https://components.espressif.com/mcp
https://ts-mcp.espressif.com/mcp
```

组件库 MCP 实际返回两个工具：`search_components`、`fetch_component_detailed_information`。初次未登录探测记录位于 `logs/mcp-discovery.json`；安装后的验证范围见上节。

## 本地构建 MCP 的版本限制

[ESP-IDF Tools Local MCP Server](https://developer.espressif.com/blog/2026/04/esp-idf-tools-mcp-server/) 可执行构建、烧录和清理，并读取项目状态与串口列表，但官方要求 **ESP-IDF 6.0+**。当前安装的 5.5.4 未包含这个入口。

本项目使用 `scripts/idf.sh` 调用固定版本的原生 CLI 完成构建。采用 IDF 6.x 的本地 MCP 前，需要单独验证固件迁移；不能因为接入 MCP 就替换出厂固件要求的工具链。

## 已查阅的社区 skills

| 项目 | 内容 | 评估 |
| --- | --- | --- |
| [magnus919/agent-skills：esp32-development](https://github.com/magnus919/agent-skills/tree/main/esp32-development) | 环境与端口检查、ESP-IDF/Arduino 等框架流程、串口排障、硬件验证模板 | 比较适合开发准备与故障排查；已阅读 SKILL.md，尚未安装或执行其辅助脚本 |
| [ziphercx/ESP32-Agent-Skill](https://github.com/ziphercx/ESP32-Agent-Skill) | ESP32 各芯片差异、FreeRTOS、内存/PSRAM、外设与 PlatformIO 指导 | 可作为后续固件开发参考；已查阅主要说明，尚未安装或验证生成器 |

这些是第三方项目，均不能替代 K151/K151-R 的原理图、M5Stack 源码和固定版本的 IDF 文档。本次查询 OpenAI curated skills 清单，未找到 ESP32 / StackChan 专用条目；插件搜索也未返回对应集成。插件目录并非穷尽列表，可继续查看 [插件目录](https://chatgpt.com/plugins)。

## StackChan 设备控制 MCP

需要区分开发辅助 MCP 和机器人自身的 MCP：

- 出厂固件的 `firmware/main/hal/hal_mcp.cpp` 注册了头部角度、灯光、提醒等工具，供其 AI 协议使用。设备启动日志也确认了这些注册项；这不表示电脑可以直接访问一个通用 HTTP MCP 服务。
- 社区 [stack-chan/stack-chan MCP Server](https://github.com/stack-chan/stack-chan/tree/develop/firmware/host/modules/connectivity/mcp-server) 提供 HTTP `/mcp`、Bearer 认证和 MOD 示例，但属于 **Moddable/JavaScript 固件体系**。其主项目明确说明刷入后会替换 M5Stack 出厂固件，适合未来另开实验分支。

当前最有用的组合是：固定的官方固件源码与 IDF CLI，加官方文档、组件库 MCP；机器人远程控制 MCP 根据后续玩法单独设计。
