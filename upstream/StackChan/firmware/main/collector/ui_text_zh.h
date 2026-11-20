/* SPDX-License-Identifier: MIT */
#pragma once
#include "mode_rules.h"
namespace collector::ui_text {
// Chinese screen strings (collector-0.4.95-font). Draw them only through ui_font::choose() with their ASCII
// stand-in: the CJK font lives on SD and may be missing. Every character here must be in the font's set;
// tests/test_ui_font.py checks all string literals under main/collector against config/ui-font-extra.txt + GB2312.

// mode_table.MODES[...]['name'], in modes::rules::kModeIds order (tests/test_ui_font.py compares them; IDLE has
// no Chinese name there).
inline constexpr const char* kModeNames[modes::rules::kModeCount] = {"Docking"};   // crowd = Docking, the only mode (2026-11-20)
inline constexpr const char* kModeWord = "模式";

inline const char* mode_name(modes::rules::Mode m)
{
    const auto i = static_cast<size_t>(m);
    return i < modes::rules::kModeCount ? kModeNames[i] : "";
}
// Subtitle with its direction, by modes::rules::Dir (the picker's two subtitle tiles; ASCII kSubtitleLabels).
inline constexpr const char* kSubtitleNames[] = {"字幕", "字幕 中→英", "字幕 英→中"};

// collector-0.4.95 global pages: each the Chinese of the ui::text word with the same name (ui_pages.h), which stays
// the ASCII stand-in. Sized for the 16 px variant (a picker tile is 96 x 60: four characters fit with room).
inline constexpr const char* kBack = "< 返回";
inline constexpr const char* kQuickTitle = "快捷设置";
inline constexpr const char* kPickerTitle = "选择模式";
inline constexpr const char* kSummaryTitle = "状态摘要";
inline constexpr const char* kVolume = "音量";
inline constexpr const char* kWifiButton = "改网络";
inline constexpr const char* kWifiNamed = "网络 %s";       // the SSID as it is
inline constexpr const char* kWifiHidden = "(隐藏)";
inline constexpr const char* kWifiOffline = "网络 离线";
inline constexpr const char* kNotConnected = "未连接";
inline constexpr const char* kBilling = "计费";
inline constexpr const char* kAllOn = "全开";
inline constexpr const char* kAllOff = "全关";
inline constexpr const char* kConfirm = "再点确认";
inline constexpr const char* kLastFormat = "上次：%s%s%.18s %s前";   // "上次：全开 2 分钟前" (ui::billing_line)
inline constexpr const char* kAgeSeconds = "%d 秒";
inline constexpr const char* kAgeMinutes = "%d 分钟";
inline constexpr const char* kAgeHours = "%d 小时";
inline constexpr const char* kHearing = "听觉";
inline constexpr const char* kOn = "开";
inline constexpr const char* kOff = "关";
inline constexpr const char* kSummaryHint = "点按/右滑 主页  左滑 日志  长按 配对";
}
