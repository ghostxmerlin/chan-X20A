/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace collector::ui_font {
// The screen's CJK font (collector-0.4.95-font): pure logic, no LVGL / IDF, tested on the host
// (tests/ui_font_test.cpp). ui_font.cpp binds a validated container to LVGL's own text-font format.
//
// HCJK v1 is written by scripts/ui_font_tools.py pack() (its docstring is the layout). The robot reads the parts
// from SD into one PSRAM buffer, checks every part's SHA-256 against ui_font_catalog.generated.h, then validate()
// bounds-checks everything LVGL will later index, so a bad container is refused instead of read out of range.
// Glyph bitmaps and cmap lists stay in that buffer (no copy); only the glyph and cmap tables are widened into
// LVGL's structs.

inline constexpr size_t kHeaderBytes = 64, kCmapBytes = 20, kGlyphBytes = 12;
enum CmapType : uint8_t { Format0Full = 0, SparseFull = 1, Format0Tiny = 2, SparseTiny = 3 };

struct Header {
    uint16_t px = 0, line_height = 0;
    int16_t base_line = 0;
    uint8_t bpp = 0;
    int8_t underline_position = 0;
    uint8_t underline_thickness = 0;
    uint16_t cmap_count = 0;
    uint32_t glyph_count = 0, cmap_offset = 0, glyph_offset = 0, list_offset = 0, list_bytes = 0;
    uint32_t bitmap_offset = 0, bitmap_bytes = 0, total_bytes = 0;
};
struct Glyph { uint32_t bitmap_index; uint16_t adv_w; uint8_t box_w, box_h; int8_t ofs_x, ofs_y; };
struct Cmap {
    uint32_t range_start; uint16_t range_length, glyph_id_start;
    uint32_t unicode_list, glyph_id_ofs_list;   // offsets into the container, 0 = none
    uint16_t list_length; uint8_t type;
};

namespace detail {
inline uint16_t u16(const uint8_t* p) { return uint16_t(p[0] | p[1] << 8); }
inline uint32_t u32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
inline bool inside(uint32_t at, uint64_t bytes, uint32_t begin, uint32_t size) { return at >= begin && at + bytes <= uint64_t(begin) + size; }
}

inline Glyph glyph(const uint8_t* blob, const Header& h, uint32_t i)
{
    const uint8_t* p = blob + h.glyph_offset + kGlyphBytes * i;
    return {detail::u32(p), detail::u16(p + 4), p[6], p[7], int8_t(p[8]), int8_t(p[9])};
}
inline Cmap cmap(const uint8_t* blob, const Header& h, uint32_t i)
{
    const uint8_t* p = blob + h.cmap_offset + kCmapBytes * i;
    return {detail::u32(p), detail::u16(p + 4), detail::u16(p + 6), detail::u32(p + 8), detail::u32(p + 12),
            detail::u16(p + 16), p[18]};
}
inline uint16_t list16(const uint8_t* blob, uint32_t offset, uint32_t i) { return detail::u16(blob + offset + 2 * i); }

// nullptr when the container is sound (and `out` is filled), else a short reason. `max_bitmap_index` is the
// largest index LVGL's glyph descriptor can hold (LV_FONT_FMT_TXT_LARGE: 4 GB, else 1 MB).
inline const char* validate(const uint8_t* blob, size_t size, Header& out, uint64_t max_bitmap_index = UINT32_MAX)
{
    using namespace detail;
    if (!blob || size < kHeaderBytes || std::memcmp(blob, "HCJK", 4)) return "not_hcjk";
    if (u16(blob + 4) != 1 || u16(blob + 6) != kHeaderBytes) return "version";
    Header h;
    h.px = u16(blob + 8); h.line_height = u16(blob + 10); h.base_line = int16_t(u16(blob + 12));
    h.bpp = blob[14]; h.underline_position = int8_t(blob[16]); h.underline_thickness = blob[17];
    h.cmap_count = u16(blob + 18); h.glyph_count = u32(blob + 20);
    h.cmap_offset = u32(blob + 24); h.glyph_offset = u32(blob + 28); h.list_offset = u32(blob + 32);
    h.list_bytes = u32(blob + 36); h.bitmap_offset = u32(blob + 40); h.bitmap_bytes = u32(blob + 44);
    h.total_bytes = u32(blob + 48);
    if (h.total_bytes != size) return "size";
    if (h.bpp != 1 && h.bpp != 2 && h.bpp != 4 && h.bpp != 8) return "bpp";
    if (h.px < 8 || h.px > 64 || !h.line_height || h.line_height > 128 || h.base_line < 0 || h.base_line > h.line_height) return "metrics";
    if (!h.cmap_count || h.cmap_count > 511 || h.glyph_count < 2 || h.glyph_count > 65535) return "counts";
    // Sections in order, 4-aligned (LVGL reads u16 lists and the widened tables are built from these).
    if (h.cmap_offset != kHeaderBytes || h.glyph_offset != h.cmap_offset + kCmapBytes * h.cmap_count ||
        h.list_offset != h.glyph_offset + kGlyphBytes * h.glyph_count || h.list_bytes % 4 ||
        h.bitmap_offset != h.list_offset + h.list_bytes || uint64_t(h.bitmap_offset) + h.bitmap_bytes != size)
        return "layout";
    if (h.bitmap_bytes > max_bitmap_index) return "too_large";
    for (uint32_t i = 0; i < h.glyph_count; ++i) {
        const Glyph g = glyph(blob, h, i);
        const uint64_t bytes = (uint64_t(g.box_w) * g.box_h * h.bpp + 7) / 8;
        if (g.bitmap_index + bytes > h.bitmap_bytes) return "glyph";
        if (i == 0 && (g.box_w || g.box_h)) return "glyph";
    }
    for (uint32_t i = 0; i < h.cmap_count; ++i) {
        const Cmap c = cmap(blob, h, i);
        // Every glyph id a cmap can produce (glyph_id_start + an offset) must name a real glyph (not 0).
        const auto gid = [&](uint32_t ofs) { return uint64_t(c.glyph_id_start) + ofs < h.glyph_count; };
        const auto list = [&](uint32_t at, uint32_t width) {
            return at && at % width == 0 && inside(at, uint64_t(width) * c.list_length, h.list_offset, h.list_bytes);
        };
        if (!c.range_length || uint64_t(c.range_start) + c.range_length > 0x110000 || !c.glyph_id_start) return "cmap";
        switch (c.type) {
        case Format0Tiny:
            if (c.unicode_list || c.glyph_id_ofs_list || !gid(c.range_length - 1u)) return "cmap";
            break;
        case Format0Full:
            if (c.unicode_list || c.list_length != c.range_length || !list(c.glyph_id_ofs_list, 1)) return "cmap";
            for (uint32_t k = 0; k < c.list_length; ++k) if (!gid(blob[c.glyph_id_ofs_list + k])) return "cmap";
            break;
        case SparseTiny: case SparseFull: {
            if (!c.list_length || !list(c.unicode_list, 2)) return "cmap";
            if (c.type == SparseTiny ? c.glyph_id_ofs_list != 0 : !list(c.glyph_id_ofs_list, 2)) return "cmap";
            for (uint32_t k = 0; k < c.list_length; ++k) {
                const uint16_t r = list16(blob, c.unicode_list, k);
                // LVGL binary-searches unicode_list: strictly ascending, inside the range.
                if (r >= c.range_length || (k && r <= list16(blob, c.unicode_list, k - 1))) return "cmap";
                if (!gid(c.type == SparseTiny ? k : list16(blob, c.glyph_id_ofs_list, k))) return "cmap";
            }
            break;
        }
        default: return "cmap";
        }
    }
    out = h;
    return nullptr;
}

// The glyph id for a code point (0 = none), the way LVGL's lv_font_fmt_txt resolves it. For tests and tools.
inline uint32_t lookup(const uint8_t* blob, const Header& h, uint32_t cp)
{
    for (uint32_t i = 0; i < h.cmap_count; ++i) {
        const Cmap c = cmap(blob, h, i);
        if (cp < c.range_start || cp - c.range_start >= c.range_length) continue;
        const uint32_t r = cp - c.range_start;
        switch (c.type) {
        case Format0Tiny: return c.glyph_id_start + r;
        case Format0Full:   // offset 0 past the first code point = missing (lv_font_fmt_txt.c)
            return blob[c.glyph_id_ofs_list + r] || !r ? c.glyph_id_start + blob[c.glyph_id_ofs_list + r] : 0;
        default:
            for (uint32_t k = 0; k < c.list_length; ++k)
                if (list16(blob, c.unicode_list, k) == r)
                    return c.glyph_id_start + (c.type == SparseTiny ? k : list16(blob, c.glyph_id_ofs_list, k));
            return 0;
        }
    }
    return 0;
}

// ---- Which font a screen string gets ----
// A request for `px` uses the loaded variant of that size, else the other loaded one, else none (-1): the caller
// then shows its ASCII text in the ASCII font. Variants are indexed as in ui_font_catalog.generated.h.
inline int pick_variant(int px, const uint8_t* variant_px, const bool* loaded, size_t count)
{
    int best = -1, best_gap = 1 << 30;
    for (size_t i = 0; i < count; ++i) {
        if (!loaded[i]) continue;
        const int gap = variant_px[i] > px ? variant_px[i] - px : px - variant_px[i];
        if (gap < best_gap) { best = int(i); best_gap = gap; }
    }
    return best;
}
inline bool ascii_only(const char* s)
{
    for (; s && *s; ++s) if (static_cast<uint8_t>(*s) >= 0x80) return false;
    return true;
}
// A label with a Chinese text and an ASCII stand-in: the Chinese text in the CJK font when one is loaded (it has
// ASCII too), else the stand-in in the ASCII font. Never a Chinese string in the ASCII font: that draws nothing.
// A stand-in that is not ASCII is refused as well ("").
struct Choice { const char* text; bool cjk; };
inline Choice choose(const char* zh, const char* ascii, bool cjk_loaded)
{
    if (cjk_loaded && zh && *zh) return {zh, true};
    return {ascii && ascii_only(ascii) ? ascii : "", false};
}
}
