/* SPDX-License-Identifier: MIT */
#pragma once
#include "mode_rules.h"
namespace collector::ui_text {
// Chinese screen strings (collector-0.4.95-font). Draw them only through ui_font::choose() with their ASCII
// stand-in: the CJK font lives on SD and may be missing. Every character here must be in the font's set;
// tests/test_ui_font.py checks all string literals under main/collector against config/ui-font-extra.txt + GB2312.

// mode_table.MODES[...]['name'], in modes::rules::kModeIds order (tests/test_ui_font.py compares them; IDLE has
// no Chinese name there).
inline constexpr const char* kModeNames[modes::rules::kModeCount] = {
    "脱口秀", "对话", "设置", "事件驱动", "字幕", "围观", "陪伴", "IDLE"};
inline constexpr const char* kModeWord = "模式";

inline const char* mode_name(modes::rules::Mode m)
{
    const auto i = static_cast<size_t>(m);
    return i < modes::rules::kModeCount ? kModeNames[i] : "";
}
}
