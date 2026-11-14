// The screen's CJK font container and font choice (fw/ui_font_blob.h, collector-0.4.95-font). Run by
// tests/test_ui_font.py with an HCJK file written by scripts/ui_font_tools.py and a list of expected
// "codepoint glyph_id" lines: the C++ reader must agree with the Python writer on every code point, and every
// corruption the robot could see must be refused before LVGL reads out of range.
#include "ui_font_blob.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string_view>
#include <vector>

using namespace collector::ui_font;
static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

static std::vector<uint8_t> read(const char* path)
{
    std::ifstream f(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(f), {}};
}
static void put32(std::vector<uint8_t>& b, size_t at, uint32_t v) { for (int i = 0; i < 4; ++i) b[at + i] = uint8_t(v >> (8 * i)); }
static void put16(std::vector<uint8_t>& b, size_t at, uint16_t v) { b[at] = uint8_t(v); b[at + 1] = uint8_t(v >> 8); }

static void container(const std::vector<uint8_t>& blob, const char* expected_path)
{
    Header h;
    CHECK(validate(blob.data(), blob.size(), h) == nullptr);
    CHECK(h.total_bytes == blob.size());
    // Every code point the Python side mapped resolves to the same glyph here (LVGL's lookup rules).
    std::FILE* f = std::fopen(expected_path, "r");
    CHECK(f != nullptr);
    unsigned cp = 0, gid = 0, count = 0;
    while (f && std::fscanf(f, "%u %u", &cp, &gid) == 2) {
        if (lookup(blob.data(), h, cp) != gid) { std::fprintf(stderr, "U+%04X: %u != %u\n", cp, lookup(blob.data(), h, cp), gid); ++failures; }
        ++count;
    }
    if (f) std::fclose(f);
    CHECK(count > 0);
    CHECK(lookup(blob.data(), h, 0x10FFFF) == 0);

    // Corruptions: each must be refused, none may crash.
    const auto refused = [&](std::vector<uint8_t> b) { Header x; return validate(b.data(), b.size(), x) != nullptr; };
    { auto b = blob; b[0] = 'X'; CHECK(refused(b)); }                       // magic
    { auto b = blob; put16(b, 4, 2); CHECK(refused(b)); }                   // version
    { auto b = blob; b.pop_back(); CHECK(refused(b)); }                     // truncated (total_bytes)
    { auto b = blob; b[14] = 3; CHECK(refused(b)); }                        // bpp
    { auto b = blob; put32(b, 28, h.glyph_offset + 4); CHECK(refused(b)); } // layout
    { auto b = blob; put16(b, 18, 0); CHECK(refused(b)); }                  // no cmaps
    {   // a glyph whose bitmap runs past the end
        auto b = blob; const size_t g = h.glyph_offset + kGlyphBytes * (h.glyph_count - 1);
        put32(b, g, h.bitmap_bytes); b[g + 6] = 40; b[g + 7] = 40; CHECK(refused(b));
    }
    {   // a cmap pointing past the glyph table
        auto b = blob; put16(b, h.cmap_offset + 6, uint16_t(h.glyph_count)); CHECK(refused(b));
    }
    {   // a cmap list outside the list area
        auto b = blob;
        for (uint32_t i = 0; i < h.cmap_count; ++i) {
            const size_t at = h.cmap_offset + kCmapBytes * i;
            if (b[at + 18] == SparseTiny || b[at + 18] == SparseFull) { put32(b, at + 8, h.bitmap_offset + 2); break; }
        }
        CHECK(refused(b));
    }
    {   // LV_FONT_FMT_TXT_LARGE=0 caps bitmap indexes at 1 MB
        Header x; CHECK(validate(blob.data(), blob.size(), x, h.bitmap_bytes - 1) != nullptr);
    }
    CHECK(validate(nullptr, 0, h) != nullptr);
    CHECK(validate(blob.data(), 10, h) != nullptr);
}

static void choice()
{
    const uint8_t px[] = {16, 24};
    const bool none[] = {false, false}, small[] = {true, false}, large[] = {false, true}, both[] = {true, true};
    CHECK(pick_variant(16, px, none, 2) == -1);
    CHECK(pick_variant(16, px, both, 2) == 0);
    CHECK(pick_variant(24, px, both, 2) == 1);
    CHECK(pick_variant(22, px, both, 2) == 1);
    CHECK(pick_variant(16, px, large, 2) == 1);        // the other size rather than no Chinese at all
    CHECK(pick_variant(24, px, small, 2) == 0);
    CHECK(ascii_only("TALK 0.4") && !ascii_only("模式") && ascii_only(nullptr));
    auto c = choose("脱口秀", "TALK", true);
    CHECK(c.cjk && std::string_view(c.text) == "脱口秀");
    c = choose("脱口秀", "TALK", false);               // no font on SD: the ASCII badge text, ASCII font
    CHECK(!c.cjk && std::string_view(c.text) == "TALK");
    c = choose("IDLE", "IDLE", true);
    CHECK(c.cjk && std::string_view(c.text) == "IDLE");
    c = choose("", "SET", true);                       // no Chinese text: the stand-in
    CHECK(!c.cjk && std::string_view(c.text) == "SET");
    c = choose("设置", "设置", false);                  // a non-ASCII stand-in is never drawn in the ASCII font
    CHECK(!c.cjk && std::string_view(c.text).empty());
    c = choose(nullptr, nullptr, false);
    CHECK(!c.cjk && c.text && !*c.text);
}

int main(int argc, char** argv)
{
    choice();
    for (int i = 1; i + 1 < argc; i += 2) container(read(argv[i]), argv[i + 1]);
    if (failures) { std::fprintf(stderr, "%d failure(s)\n", failures); return 1; }
    std::puts("ui_font_test OK");
    return 0;
}
