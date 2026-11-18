"""The screen's CJK font (collector-0.4.95-font): container reader vs writer, fallback choice, glyph coverage of
every UI string, and the committed catalog. No device, no font download needed (the generated parts are used when
present under assets/ui-font/sdcard)."""
import importlib.util
import json
import pathlib
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FW = ROOT / 'upstream/StackChan/firmware/main/collector'
sys.path.insert(0, str(ROOT / 'scripts'))
import ui_font_tools as tools  # noqa: E402


def string_literals(text):
    """C/C++ string literal bodies, comments and char literals skipped (enough for this code base)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith('//', i):
            i = text.find('\n', i)
            i = n if i < 0 else i
        elif text.startswith('/*', i):
            i = text.find('*/', i + 2)
            i = n if i < 0 else i + 2
        elif c == 'R' and text.startswith('R"', i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == '_')):
            open_at = text.index('(', i)
            close = ')' + text[i + 2:open_at] + '"'
            end = text.index(close, open_at)
            out.append(text[open_at + 1:end])
            i = end + len(close)
        elif c in '"\'':
            j, body = i + 1, []
            while j < n and text[j] != c:
                if text[j] == '\\':
                    j += 1
                body.append(text[j])
                j += 1
            if c == '"':
                out.append(''.join(body))
            i = j + 1
        else:
            i += 1
    return out


def synthetic_font():
    """A small font using all four LVGL cmap types, with random 4 bpp bitmaps."""
    rnd = random.Random(7)
    glyphs = [dict(adv_w=0, box_w=0, box_h=0, ofs_x=0, ofs_y=0, bitmap=b'')]

    def glyph():
        w, h = rnd.randint(1, 15), rnd.randint(1, 16)
        glyphs.append(dict(adv_w=16 * (w + 1), box_w=w, box_h=h, ofs_x=rnd.randint(-2, 2), ofs_y=rnd.randint(-3, 3),
                           bitmap=bytes(rnd.randrange(256) for _ in range((w * h * 4 + 7) // 8))))
        return len(glyphs) - 1

    cmaps = []
    start = len(glyphs)
    for _ in range(95):                                   # ASCII: format 0 tiny
        glyph()
    cmaps.append(dict(range_start=0x20, range_length=95, glyph_id_start=start, type=tools.FORMAT0_TINY,
                      unicode_list=None, glyph_id_ofs_list=None))
    start = len(glyphs)                                   # 0xFFE0..0xFFE5 with gaps: format 0 full
    present = [0, 1, 3, 5]
    for _ in present:
        glyph()
    ofs = [present.index(r) if r in present else 0 for r in range(6)]
    cmaps.append(dict(range_start=0xFFE0, range_length=6, glyph_id_start=start, type=tools.FORMAT0_FULL,
                      unicode_list=None, glyph_id_ofs_list=ofs))
    start = len(glyphs)                                   # hanzi: sparse tiny
    rel = sorted(rnd.sample(range(0x5000), 300))
    for _ in rel:
        glyph()
    cmaps.append(dict(range_start=0x4E00, range_length=0x5000, glyph_id_start=start, type=tools.SPARSE_TINY,
                      unicode_list=rel, glyph_id_ofs_list=None))
    start = len(glyphs)                                   # punctuation: sparse full (shared glyphs)
    for _ in range(3):
        glyph()
    cmaps.append(dict(range_start=0x3000, range_length=0x20, glyph_id_start=start, type=tools.SPARSE_FULL,
                      unicode_list=[0, 1, 2, 0x10, 0x11], glyph_id_ofs_list=[0, 1, 2, 1, 0]))
    return dict(px=16, line_height=20, base_line=4, bpp=4, underline_position=-2, underline_thickness=1,
                cmaps=cmaps, glyphs=glyphs)


def generated_variants():
    manifest = json.loads((ROOT / 'assets/ui-font/manifest.json').read_text(encoding='utf-8'))
    out = []
    for v in manifest['variants']:
        files = [ROOT / 'assets/ui-font' / p['path'] for p in v['parts']]
        if all(f.exists() for f in files):
            out.append((v, b''.join(f.read_bytes() for f in files)))
    return manifest, out


class UiFontTest(unittest.TestCase):
    def test_reader_matches_writer_and_refuses_corruption(self):
        font = synthetic_font()
        blob = tools.pack(font)
        unpacked = tools.unpack(blob)
        # Python round trip: every glyph and mapping survives.
        self.assertEqual([g['bitmap'] for g in unpacked['glyphs']], [g['bitmap'] for g in font['glyphs']])
        self.assertNotIn(0xFFE2, unpacked['codepoints'])          # format 0 full gap
        self.assertEqual(unpacked['codepoints'][0x3011], unpacked['codepoints'][0x3000])
        blobs = [(blob, unpacked['codepoints'])]
        _, real = generated_variants()
        blobs += [(b, tools.unpack(b)['codepoints']) for _, b in real]
        with tempfile.TemporaryDirectory(prefix='chan-ui-font-') as tmp:
            tmp = pathlib.Path(tmp)
            binary = tmp / 'ui_font_test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I' + str(FW),
                            str(ROOT / 'tests/ui_font_test.cpp'), '-o', str(binary)], check=True)
            args = []
            for k, (b, cps) in enumerate(blobs):
                (tmp / f'{k}.hcjk').write_bytes(b)
                (tmp / f'{k}.txt').write_text(''.join(f'{cp} {g}\n' for cp, g in sorted(cps.items())))
                args += [str(tmp / f'{k}.hcjk'), str(tmp / f'{k}.txt')]
            subprocess.run([str(binary)] + args, check=True)

    def test_charset(self):
        self.assertEqual(len(tools.gb2312_level1()), 3755)
        chars = tools.charset(tools.read_extra(ROOT / 'config/ui-font-extra.txt'))
        self.assertEqual(len(chars), len(set(chars)))
        for ch in '啊座，。！？：；“”…—·0aZ ':
            self.assertIn(ch, chars)

    # Drawn with their own compiled-in font (collector_words_20, scripts/generate-words-font.sh), not the SD CJK
    # font; tests/m12_words_test.cpp checks that every character is in that font (2026-11-18).
    OWN_FONT = ('m12_words.h', 'collector_words_20.c')

    def test_every_ui_string_is_covered(self):
        """Any non-ASCII character in a firmware string literal must be in the font, or the screen shows nothing
        for it. Fix: add it to config/ui-font-extra.txt and regenerate (docs/sd-media.md)."""
        chars = set(tools.charset(tools.read_extra(ROOT / 'config/ui-font-extra.txt')))
        missing = {}
        for path in sorted(FW.rglob('*')):
            if path.suffix not in ('.h', '.hpp', '.cpp', '.cc', '.c'):
                continue
            if path.name in self.OWN_FONT:
                continue
            for literal in string_literals(path.read_text(encoding='utf-8', errors='replace')):
                for ch in literal:
                    if ord(ch) > 0x7E and ch not in chars:
                        missing.setdefault(ch, set()).add(path.name)
        self.assertEqual(missing, {}, 'characters outside the CJK font: ' +
                         ', '.join(f'{ch} (U+{ord(ch):04X}) in {sorted(files)}' for ch, files in missing.items()))
        # The generated font itself carries the whole set (when it has been generated here).
        _, real = generated_variants()
        for v, blob in real:
            cps = tools.unpack(blob)['codepoints']
            self.assertEqual([c for c in chars if ord(c) not in cps], [], v['id'])

    def test_mode_names_match_mind(self):
        """ui_text_zh.h kModeNames = server/mind/mode_table.py MODES[id]['name'] in modes::rules::kModeIds order."""
        spec = importlib.util.spec_from_file_location('ui_font_mode_table', ROOT / 'server/mind/mode_table.py')
        table = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(table)        # no imports of its own
        modes = table.MODES
        rules = (FW / 'mode_rules.h').read_text(encoding='utf-8')
        ids = string_literals(rules[rules.index('kModeIds'):].split(';', 1)[0])
        text = (FW / 'ui_text_zh.h').read_text(encoding='utf-8')
        names = string_literals(text[text.index('kModeNames'):].split(';', 1)[0])
        self.assertEqual(ids, list(modes))
        self.assertEqual(names, [modes[i]['name'] for i in ids])

    def test_catalog_is_current(self):
        subprocess.run([sys.executable, str(ROOT / 'scripts/generate-cjk-font.py'), '--check'], check=True)
        manifest, _ = generated_variants()
        for v in manifest['variants']:
            self.assertTrue(all(p['bytes'] <= 262144 for p in v['parts']))     # the SD installer's per-file bound
            self.assertEqual(sum(p['bytes'] for p in v['parts']), v['bytes'])


if __name__ == '__main__':
    unittest.main()
