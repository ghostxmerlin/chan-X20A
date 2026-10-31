#!/usr/bin/env python3
"""Regenerate committed LVGL artwork/font. See docs/build.md for optional tools."""
from pathlib import Path
import subprocess
from PIL import Image

root = Path(__file__).resolve().parent.parent
tools = root / ".tools/ui-assets"
source = root / "upstream/StackChan/firmware/main/collector"
cache = root / ".cache/screen-assets"
cache.mkdir(parents=True, exist_ok=True)
font = tools / "JetBrainsMono-Regular.ttf"
subprocess.run([str(tools / "node_modules/.bin/lv_font_conv"), "--font", str(font),
                "--size", "15", "--bpp", "4", "--range", "0x20-0x7e", "--format", "lvgl",
                "--no-compress", "--lv-font-name", "collector_mono_15", "--output",
                str(source / "collector_mono_15.c")], check=True)
font_c = source / "collector_mono_15.c"
# Keep generator metadata portable rather than embedding a developer's home path.
font_c.write_text("/* JetBrains Mono v2.304 ASCII subset, SIL OFL 1.1. See artwork/JetBrainsMono-OFL.txt. */\n" +
                  font_c.read_text().replace(str(root) + "/", "").rstrip() + "\n")
subprocess.run(["node", "-e", """
const fs=require('fs');
const {Resvg}=require(process.argv[1]);
const image=new Resvg(fs.readFileSync(process.argv[2]),{
    font:{fontFiles:[process.argv[3]],loadSystemFonts:false,defaultFontFamily:'JetBrains Mono'}
}).render();
fs.writeFileSync(process.argv[4],image.asPng());
""", str(tools / "node_modules/@resvg/resvg-js"), str(source / "artwork/laughing-man.svg"),
                str(font), str(cache / "face.png")], check=True)
pixels = [round(g * a / 255) for r, g, b, a in Image.open(cache / "face.png").convert("RGBA").getdata()]
assert len(pixels) == 216 * 216
text = "/* Generated A8 mask from artwork/laughing-man.svg. See root NOTICE.md. */\n#include <lvgl.h>\n\nstatic const uint8_t pixels[] = {\n"
for offset in range(0, len(pixels), 24):
    text += "    " + ",".join(str(x) for x in pixels[offset:offset + 24]) + ",\n"
text += """};

const lv_image_dsc_t collector_laughing_man = {
    .header = { .magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_A8, .w = 216, .h = 216, .stride = 216 },
    .data_size = sizeof(pixels), .data = pixels,
};
"""
(source / "laughing_man.c").write_text(text)
print("Regenerated LVGL font and 216 x 216 A8 artwork")
