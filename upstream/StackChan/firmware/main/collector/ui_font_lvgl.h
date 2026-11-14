/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>
#include "ui_font_blob.h"
namespace collector::ui_font {
// Bind a validated HCJK container (ui_font_blob.h validate()) to LVGL's own text-font format, as a font built
// into the image would be: LVGL's lv_font_get_glyph_dsc_fmt_txt / lv_font_get_bitmap_fmt_txt do the drawing.
// Bitmaps and cmap lists are read in place from `blob` (which must outlive the font); `glyphs` and `cmaps` are
// caller-allocated arrays of h.glyph_count and h.cmap_count entries. No kerning (the container has none).
// LVGL-only (no IDF): the robot (ui_font.cpp) and the host preview (tests/display/font_preview.cpp) share it.

#if LV_FONT_FMT_TXT_LARGE
inline constexpr uint64_t kMaxBitmapIndex = UINT32_MAX;
#else
inline constexpr uint64_t kMaxBitmapIndex = (1u << 20) - 1;   // lv_font_fmt_txt_glyph_dsc_t.bitmap_index : 20
#endif

inline void bind(const uint8_t* blob, const Header& h, lv_font_fmt_txt_glyph_dsc_t* glyphs,
                 lv_font_fmt_txt_cmap_t* cmaps, lv_font_fmt_txt_dsc_t& dsc, lv_font_t& font)
{
    for (uint32_t i = 0; i < h.glyph_count; ++i) {
        const Glyph g = glyph(blob, h, i);
        glyphs[i] = {};
        glyphs[i].bitmap_index = g.bitmap_index; glyphs[i].adv_w = g.adv_w;
        glyphs[i].box_w = g.box_w; glyphs[i].box_h = g.box_h; glyphs[i].ofs_x = g.ofs_x; glyphs[i].ofs_y = g.ofs_y;
    }
    for (uint32_t i = 0; i < h.cmap_count; ++i) {
        const Cmap c = cmap(blob, h, i);
        cmaps[i] = {};
        cmaps[i].range_start = c.range_start; cmaps[i].range_length = c.range_length;
        cmaps[i].glyph_id_start = c.glyph_id_start; cmaps[i].list_length = c.list_length;
        cmaps[i].unicode_list = c.unicode_list ? reinterpret_cast<const uint16_t*>(blob + c.unicode_list) : nullptr;
        cmaps[i].glyph_id_ofs_list = c.glyph_id_ofs_list ? blob + c.glyph_id_ofs_list : nullptr;
        cmaps[i].type = static_cast<lv_font_fmt_txt_cmap_type_t>(c.type);
    }
    dsc = {};
    dsc.glyph_bitmap = blob + h.bitmap_offset;
    dsc.glyph_dsc = glyphs;
    dsc.cmaps = cmaps;
    dsc.kern_dsc = nullptr;
    dsc.kern_scale = 0;
    dsc.cmap_num = h.cmap_count;
    dsc.bpp = h.bpp;
    dsc.kern_classes = 0;
    dsc.bitmap_format = LV_FONT_FMT_TXT_PLAIN;
    dsc.stride = 0;
    font = {};
    font.get_glyph_dsc = lv_font_get_glyph_dsc_fmt_txt;
    font.get_glyph_bitmap = lv_font_get_bitmap_fmt_txt;
    font.line_height = h.line_height;
    font.base_line = h.base_line;
    font.subpx = LV_FONT_SUBPX_NONE;
    font.kerning = LV_FONT_KERNING_NONE;
    font.underline_position = h.underline_position;
    font.underline_thickness = static_cast<int8_t>(h.underline_thickness);
    font.dsc = &dsc;
}
}
