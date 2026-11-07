#!/usr/bin/env python3
"""Regenerate committed LVGL artwork/font. See docs/build.md for optional tools."""
from pathlib import Path
import subprocess
import copy
import array
import xml.etree.ElementTree as ET
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
def render(svg, destination):
    subprocess.run(["node", "-e", """
const fs=require('fs');
const {Resvg}=require(process.argv[1]);
const image=new Resvg(fs.readFileSync(process.argv[2]),{
    font:{fontFiles:[process.argv[3]],loadSystemFonts:false,defaultFontFamily:'JetBrains Mono'}
}).render();
fs.writeFileSync(process.argv[4],image.asPng());
""", str(tools / "node_modules/@resvg/resvg-js"), str(svg), str(font), str(destination)], check=True)

ET.register_namespace("", "http://www.w3.org/2000/svg")
svg = ET.parse(source / "artwork/laughing-man.svg").getroot()
frame_count = 120
frames = []
for number in range(frame_count):
    frame = copy.deepcopy(svg)
    for child in frame:
        if child.get("id") == "lettering":
            child.set("transform", f"rotate({-number * 360 / frame_count:g} 13 13.5)")
    path = cache / f"frame-{number:03d}"
    ET.ElementTree(frame).write(path.with_suffix(".svg"), encoding="unicode")
    render(path.with_suffix(".svg"), path.with_suffix(".png"))
    rgba = Image.open(path.with_suffix(".png")).convert("RGBA")
    assert rgba.size == (260, 240)
    rgb = Image.alpha_composite(Image.new("RGBA", rgba.size, "#000000"), rgba).convert("RGB")
    frames.append(array.array("H", (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                                   for r, g, b in rgb.getdata())))

def image_c(name, pixels, width, height, stride, color_format):
    text = "static const uint8_t " + name + "_pixels[] = {\n"
    for offset in range(0, len(pixels), 24):
        text += "    " + ",".join(str(x) for x in pixels[offset:offset + 24]) + ",\n"
    return text + "};\n\nconst lv_image_dsc_t " + name + " = {\n" + \
        f"    .header = {{ .magic = LV_IMAGE_HEADER_MAGIC, .cf = {color_format}, .w = {width}, .h = {height}, .stride = {stride} }},\n" + \
        f"    .data_size = sizeof({name}_pixels), .data = {name}_pixels,\n}};\n"

base = frames[0]
# Store only pixels that change anywhere in the loop. At runtime the device
# copies palette entries into one opaque RGB565 image: no rotation, alpha
# blending, JPEG decoding, or full animation-frame allocations on the S3.
offsets = [i for i, value in enumerate(base) if any(frame[i] != value for frame in frames[1:])]
# Keep redraws below LVGL's 32 invalid-area slots and off the fixed center.
# Whole-image invalidation wastes SPI transfers even with precomputed frames.
regions = []
for y in range(0, 240, 16):
    for left, right in [(0, 130), (130, 260)]:
        points = [(i % 260, i // 260) for i in offsets if y <= i // 260 < y + 16 and left <= i % 260 < right]
        if points:
            regions.append((min(x for x, y in points), min(y for x, y in points),
                            max(x for x, y in points), max(y for x, y in points)))
assert len(regions) <= 28
palette = sorted({frame[i] for frame in frames for i in offsets})
assert len(palette) <= 32, f"RGB565 palette exceeds five bits: {len(palette)}"
palette_index = {value: i for i, value in enumerate(palette)}
frame_bytes = (len(offsets)*5+7)//8
changes = bytearray(frame_count*frame_bytes)
for f, frame in enumerate(frames):
    for i, offset in enumerate(offsets):
        value = palette_index[frame[offset]]
        byte, shift = divmod(i*5, 8)
        changes[f*frame_bytes+byte] |= (value << shift) & 255
        if shift > 3: changes[f*frame_bytes+byte+1] |= value >> (8-shift)
base_pixels = bytes(byte for value in base for byte in (value & 255, value >> 8))
def values_c(name, values, value_type):
    lines = [f"const {value_type} {name}[] = {{"]
    lines += ["    " + ",".join(str(x) for x in values[i:i + 24]) + ","
              for i in range(0, len(values), 24)]
    return "\n".join(lines) + "\n};\n"
text = '/* Generated from artwork/laughing-man.svg. See root NOTICE.md. */\n#include "laughing_man.h"\n\n'
text += image_c("collector_laughing_man", base_pixels, 260, 240, 520, "LV_COLOR_FORMAT_RGB565")
text += "\n" + values_c("collector_laughing_man_offsets", offsets, "uint16_t")
text += "\n" + values_c("collector_laughing_man_palette", palette, "uint16_t")
text += "\n" + values_c("collector_laughing_man_frames", changes, "uint8_t")
text += "\nconst lv_area_t collector_laughing_man_regions[] = {\n" + \
        "".join("    {" + ",".join(map(str, area)) + "},\n" for area in regions) + "};\n"
(source / "laughing_man.c").write_text(text)
(source / "laughing_man.h").write_text(f'''/* Generated animation metadata; see scripts/generate-screen-assets.py. */
#pragma once
#include <lvgl.h>
#ifdef __cplusplus
extern "C" {{
#endif
enum {{ COLLECTOR_FACE_FRAME_COUNT = {frame_count}, COLLECTOR_FACE_CHANGED_PIXELS = {len(offsets)}, COLLECTOR_FACE_FRAME_BYTES = {frame_bytes}, COLLECTOR_FACE_REGION_COUNT = {len(regions)} }};
LV_IMAGE_DECLARE(collector_laughing_man);
extern const uint16_t collector_laughing_man_offsets[];
extern const uint16_t collector_laughing_man_palette[];
extern const uint8_t collector_laughing_man_frames[];
extern const lv_area_t collector_laughing_man_regions[];
#ifdef __cplusplus
}}
#endif
''')
print(f"Regenerated 260 x 240 RGB565 animation: {frame_count} frames, {len(offsets)} changing pixels, "
      f"{len(palette)} colors, {len(changes)} frame bytes")
