#!/bin/sh
# collector_words_20 (collector-0.5.03): only the glyphs of the M12 words under the eyes (m12_words.h kGlyphs), from
# the PuHui common font the xiaozhi-fonts component already ships, 20 px, 4 bpp, compiled into the firmware.
# Rerun after changing the words: the glyph list is read from m12_words.h.
set -eu
cd "$(dirname "$0")/.."
H=upstream/StackChan/firmware/main/collector/m12_words.h
G=$(grep -o 'kGlyphs = "[^"]*"' "$H" | sed 's/kGlyphs = "//;s/"$//')
.tools/ui-assets/node_modules/.bin/lv_font_conv --font upstream/StackChan/firmware/managed_components/78__xiaozhi-fonts/ttf/puhui-common.ttf \
  --size 20 --bpp 4 --symbols "$G" --format lvgl --no-compress --lv-font-name collector_words_20 \
  --output upstream/StackChan/firmware/main/collector/collector_words_20.c
