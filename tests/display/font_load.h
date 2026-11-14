// Host loading of the screen's CJK font (collector-0.4.95): the generated SD parts are joined, validated and bound
// exactly as the robot does (ui_font_blob.h / ui_font_lvgl.h). Shared by font_preview and ui_pages_preview.
#pragma once
#include <collector/ui_font_catalog.generated.h>
#include <collector/ui_font_lvgl.h>
#include <cassert>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

struct LoadedFont {
    std::vector<uint8_t> blob;
    std::vector<lv_font_fmt_txt_glyph_dsc_t> glyphs;
    std::vector<lv_font_fmt_txt_cmap_t> cmaps;
    lv_font_fmt_txt_dsc_t dsc{};
    lv_font_t font{};
};

// `dir` holds the parts by file name (assets/ui-font/sdcard/soul/font or an artifact's sdcard-font/).
inline bool load_font(const std::string& dir, const collector::ui_font::Variant& v, LoadedFont& out)
{
    namespace uf = collector::ui_font;
    for (uint8_t k = 0; k < v.part_count; ++k) {
        const std::string path = v.parts[k].path;
        std::ifstream f(dir + path.substr(path.rfind('/')), std::ios::binary);
        if (!f) return false;
        out.blob.insert(out.blob.end(), std::istreambuf_iterator<char>(f), {});
    }
    uf::Header h;
    if (const char* e = uf::validate(out.blob.data(), out.blob.size(), h, uf::kMaxBitmapIndex)) {
        std::fprintf(stderr, "%s: %s\n", v.id, e);
        return false;
    }
    assert(h.px == v.px && h.bpp == v.bpp && out.blob.size() == v.bytes);
    out.glyphs.resize(h.glyph_count);
    out.cmaps.resize(h.cmap_count);
    uf::bind(out.blob.data(), h, out.glyphs.data(), out.cmaps.data(), out.dsc, out.font);
    return true;
}
