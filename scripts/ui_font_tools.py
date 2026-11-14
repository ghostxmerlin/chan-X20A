"""Shared pieces of the screen's CJK font (collector-0.4.95-font): the character set, a reader for lv_font_conv's
LVGL binary font (lv_font_conv/doc/font_spec.md), the HCJK container the robot maps in PSRAM, and a tiny renderer
for previews. No third-party modules: the tests import this file.

HCJK v1 (little endian; every offset is from the start of the file):
  header, 64 B:  'HCJK', u16 version=1, u16 header_bytes=64, u16 px, u16 line_height, i16 base_line, u8 bpp,
                 u8 flags=0, i8 underline_position, u8 underline_thickness, u16 cmap_count, u32 glyph_count,
                 u32 cmap_offset, u32 glyph_offset, u32 list_offset, u32 list_bytes, u32 bitmap_offset,
                 u32 bitmap_bytes, u32 total_bytes, 12 B zero
  cmaps, 20 B each:  u32 range_start, u16 range_length, u16 glyph_id_start, u32 unicode_list (offset or 0),
                     u32 glyph_id_ofs_list (offset or 0), u16 list_length, u8 type (LVGL cmap type), u8 0
  glyphs, 12 B each: u32 bitmap_index (from bitmap_offset), u16 adv_w (1/16 px), u8 box_w, u8 box_h, i8 ofs_x,
                     i8 ofs_y, u16 0.  Glyph 0 is LVGL's reserved empty glyph.
  lists (u8 / u16 arrays the cmaps point at, 2-aligned), then the bitmaps: each glyph's pixels row after row with
  no row padding (LVGL "plain", stride 0), starting on a byte.
The firmware twin is upstream/StackChan/firmware/main/collector/ui_font_blob.h.
"""
import struct

MAGIC = b'HCJK'
VERSION = 1
HEADER = struct.Struct('<4sHHHHhBBbBHIIIIIIII12x')
CMAP = struct.Struct('<IHHIIHBx')
GLYPH = struct.Struct('<IHBBbbH')
assert HEADER.size == 64 and CMAP.size == 20 and GLYPH.size == 12

# LVGL cmap types (lv_font_fmt_txt_cmap_type_t)
FORMAT0_FULL, SPARSE_FULL, FORMAT0_TINY, SPARSE_TINY = 0, 1, 2, 3


def gb2312_row(lead, cells=range(0xA1, 0xFF)):
    out = []
    for trail in cells:
        try:
            out.append(bytes([lead, trail]).decode('gb2312'))
        except UnicodeDecodeError:
            pass
    return out


def gb2312_level1():
    """The 3,755 level-1 hanzi (rows 0xB0..0xD7, ordered by pinyin)."""
    chars = [c for lead in range(0xB0, 0xD8) for c in gb2312_row(lead)]
    assert len(chars) == 3755, len(chars)
    return chars


def read_extra(path):
    """Characters listed in config/ui-font-extra.txt: everything outside '#' comments and whitespace."""
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0]
        out.extend(ch for ch in line if not ch.isspace())
    return out


def charset(extra=()):
    """ASCII 0x20..0x7E, GB2312 row 1 (CJK punctuation and symbols), row 3 (full-width forms), level-1 hanzi,
    then `extra`. Ordered, no duplicates."""
    seen, out = set(), []
    for ch in [chr(c) for c in range(0x20, 0x7F)] + gb2312_row(0xA1) + gb2312_row(0xA3) + gb2312_level1() + list(extra):
        if ch not in seen:
            seen.add(ch)
            out.append(ch)
    return out


class Bits:
    def __init__(self, data):
        self.value = int.from_bytes(data, 'big')
        self.total = len(data) * 8
        self.pos = 0

    def take(self, n, signed=False):
        if n == 0:
            return 0
        if self.pos + n > self.total:
            raise ValueError('glyph bit stream overrun')
        v = (self.value >> (self.total - self.pos - n)) & ((1 << n) - 1)
        self.pos += n
        if signed and v & (1 << (n - 1)):
            v -= 1 << n
        return v


def parse_lvgl_bin(data):
    """lv_font_conv --format bin (uncompressed, no kerning) -> dict with header fields, cmaps and glyphs."""
    def table(at, label):
        size, name = struct.unpack_from('<I4s', data, at)
        if name != label.encode():
            raise ValueError(f'expected {label} table at {at}')
        return size

    head_len = table(0, 'head')
    (version, tables, px, ascent, descent, _typo_a, _typo_d, _gap, _min_y, _max_y, default_adv, _kscale,
     loc_format, _gid_format, adv_format, bpp, xy_bits, wh_bits, adv_bits, compression, _subpx, _pad,
     ul_pos, ul_thick) = struct.unpack_from('<IHHHhHhHhhHHBBBBBBBBBBhH', data, 8)
    if compression != 0:
        raise ValueError('compressed bitmaps are not supported; run lv_font_conv with --no-compress')
    if tables != 3:
        raise ValueError('kerning tables are not supported; run lv_font_conv with --no-kerning')
    cmap_at = head_len
    cmap_len = table(cmap_at, 'cmap')
    (count,) = struct.unpack_from('<I', data, cmap_at + 8)
    cmaps = []
    for i in range(count):
        off, start, length, gid_start, entries, kind, _ = struct.unpack_from('<IIHHHBB', data, cmap_at + 12 + 16 * i)
        at = cmap_at + off
        unicode_list, ofs_list = None, None
        if kind == FORMAT0_FULL:
            ofs_list = list(data[at:at + entries])
        elif kind in (SPARSE_FULL, SPARSE_TINY):
            unicode_list = list(struct.unpack_from(f'<{entries}H', data, at))
            if kind == SPARSE_FULL:
                ofs_list = list(struct.unpack_from(f'<{entries}H', data, at + 2 * entries))
        elif kind != FORMAT0_TINY:
            raise ValueError(f'unknown cmap type {kind}')
        cmaps.append(dict(range_start=start, range_length=length, glyph_id_start=gid_start, type=kind,
                          unicode_list=unicode_list, glyph_id_ofs_list=ofs_list))
    loca_at = cmap_at + cmap_len
    loca_len = table(loca_at, 'loca')
    (loca_count,) = struct.unpack_from('<I', data, loca_at + 8)
    fmt = '<%d%s' % (loca_count, 'H' if loc_format == 0 else 'I')
    offsets = list(struct.unpack_from(fmt, data, loca_at + 12))
    glyf_at = loca_at + loca_len
    glyf_len = table(glyf_at, 'glyf')
    glyphs = []
    for i in range(loca_count):
        if i == 0:      # reserved; LVGL's loader zeroes it too
            glyphs.append(dict(adv_w=0, box_w=0, box_h=0, ofs_x=0, ofs_y=0, bitmap=b''))
            continue
        # Offsets count from the table start (its 8-byte label included), as in lv_binfont_loader.c.
        start = offsets[i]
        end = offsets[i + 1] if i + 1 < loca_count else glyf_len
        bits = Bits(data[glyf_at + start:glyf_at + end])
        adv = bits.take(adv_bits) if adv_bits else default_adv
        if adv_format == 0:
            adv *= 16
        ofs_x, ofs_y = bits.take(xy_bits, True), bits.take(xy_bits, True)
        box_w, box_h = bits.take(wh_bits), bits.take(wh_bits)
        n = box_w * box_h * bpp
        value = bits.take(n) if n else 0
        pad = (-n) % 8
        bitmap = (value << pad).to_bytes((n + pad) // 8, 'big') if n else b''
        glyphs.append(dict(adv_w=adv, box_w=box_w, box_h=box_h, ofs_x=ofs_x, ofs_y=ofs_y, bitmap=bitmap))
    return dict(px=px, line_height=ascent - descent, base_line=-descent, bpp=bpp, underline_position=ul_pos,
                underline_thickness=ul_thick, cmaps=cmaps, glyphs=glyphs, version=version)


def pack(font):
    """Font dict (parse_lvgl_bin's shape) -> HCJK bytes."""
    glyphs, cmaps = font['glyphs'], font['cmaps']
    if not glyphs or len(glyphs) > 65535 or not cmaps or len(cmaps) > 511:
        raise ValueError('glyph / cmap count out of range')
    cmap_offset = HEADER.size
    glyph_offset = cmap_offset + CMAP.size * len(cmaps)
    list_offset = glyph_offset + GLYPH.size * len(glyphs)
    lists = bytearray()
    records = []
    for c in cmaps:
        def put(values, width):
            if values is None:
                return 0
            at = list_offset + len(lists)
            lists.extend(struct.pack(f'<{len(values)}{"B" if width == 1 else "H"}', *values))
            if len(lists) % 2:
                lists.append(0)
            return at
        uni = put(c['unicode_list'], 2)
        ofs = put(c['glyph_id_ofs_list'], 1 if c['type'] == FORMAT0_FULL else 2)
        length = c['range_length'] if c['type'] == FORMAT0_FULL else len(c['unicode_list'] or [])
        records.append(CMAP.pack(c['range_start'], c['range_length'], c['glyph_id_start'], uni, ofs, length, c['type']))
    while len(lists) % 4:
        lists.append(0)
    bitmap_offset = list_offset + len(lists)
    bitmaps = bytearray()
    glyph_records = []
    for g in glyphs:
        glyph_records.append(GLYPH.pack(len(bitmaps) if g['bitmap'] else 0, g['adv_w'], g['box_w'], g['box_h'],
                                        g['ofs_x'], g['ofs_y'], 0))
        bitmaps.extend(g['bitmap'])
    total = bitmap_offset + len(bitmaps)
    header = HEADER.pack(MAGIC, VERSION, HEADER.size, font['px'], font['line_height'], font['base_line'], font['bpp'],
                         0, font['underline_position'], font['underline_thickness'], len(cmaps), len(glyphs),
                         cmap_offset, glyph_offset, list_offset, len(lists), bitmap_offset, len(bitmaps), total)
    return header + b''.join(records) + b''.join(glyph_records) + bytes(lists) + bytes(bitmaps)


def unpack(blob):
    """HCJK bytes -> font dict with a `codepoints` map (codepoint -> glyph id). Raises on a malformed container."""
    (magic, version, header_bytes, px, line_height, base_line, bpp, _flags, ul_pos, ul_thick, cmap_count,
     glyph_count, cmap_offset, glyph_offset, list_offset, list_bytes, bitmap_offset, bitmap_bytes,
     total) = HEADER.unpack_from(blob, 0)
    if magic != MAGIC or version != VERSION or header_bytes != HEADER.size or total != len(blob):
        raise ValueError('not an HCJK v1 container')
    glyphs = []
    for i in range(glyph_count):
        index, adv, w, h, x, y, _ = GLYPH.unpack_from(blob, glyph_offset + GLYPH.size * i)
        size = (w * h * bpp + 7) // 8
        if index + size > bitmap_bytes:
            raise ValueError(f'glyph {i} bitmap out of range')
        start = bitmap_offset + index
        glyphs.append(dict(adv_w=adv, box_w=w, box_h=h, ofs_x=x, ofs_y=y, bitmap=bytes(blob[start:start + size])))
    codepoints = {}
    for i in range(cmap_count):
        start, length, gid, uni, ofs, list_length, kind = CMAP.unpack_from(blob, cmap_offset + CMAP.size * i)
        def arr(at, n, width):
            return list(struct.unpack_from(f'<{n}{"B" if width == 1 else "H"}', blob, at))
        if kind == FORMAT0_TINY:
            pairs = [(start + r, gid + r) for r in range(length)]
        elif kind == FORMAT0_FULL:   # offset 0 past the first code point = missing (lv_font_fmt_txt.c)
            pairs = [(start + r, gid + o) for r, o in enumerate(arr(ofs, length, 1)) if o or not r]
        elif kind == SPARSE_TINY:
            pairs = [(start + r, gid + k) for k, r in enumerate(arr(uni, list_length, 2))]
        elif kind == SPARSE_FULL:
            pairs = [(start + r, gid + o) for r, o in zip(arr(uni, list_length, 2), arr(ofs, list_length, 2))]
        else:
            raise ValueError(f'unknown cmap type {kind}')
        for cp, g in pairs:
            if not 0 < g < glyph_count:
                raise ValueError(f'cmap {i} glyph id out of range')
            codepoints[cp] = g
    return dict(px=px, line_height=line_height, base_line=base_line, bpp=bpp, underline_position=ul_pos,
                underline_thickness=ul_thick, glyphs=glyphs, codepoints=codepoints)


def glyph_alpha(glyph, bpp):
    """A glyph's coverage, rows of 0..255."""
    bits = Bits(glyph['bitmap']) if glyph['bitmap'] else None
    top = (1 << bpp) - 1
    return [[bits.take(bpp) * 255 // top for _ in range(glyph['box_w'])] for _ in range(glyph['box_h'])]


def render(font, text, width, color=(0x79, 0xFF, 0x97), background=(0, 0, 0), pad=3):
    """Draw one line the way LVGL places glyphs (baseline = line_height - base_line). Returns RGB rows."""
    height = font['line_height'] + 2 * pad
    rows = [[background] * width for _ in range(height)]
    x = pad * 16
    for ch in text:
        g = font['glyphs'][font['codepoints'].get(ord(ch), 0)]
        alpha = glyph_alpha(g, font['bpp'])
        left = x // 16 + g['ofs_x']
        top = pad + font['line_height'] - font['base_line'] - g['ofs_y'] - g['box_h']
        for j, line in enumerate(alpha):
            for i, a in enumerate(line):
                px, py = left + i, top + j
                if a and 0 <= px < width and 0 <= py < height:
                    rows[py][px] = tuple((c * a + b * (255 - a)) // 255 for c, b in zip(color, background))
        x += g['adv_w']
    return rows


def write_png(path, rows, scale=1):
    import zlib
    rows = [[p for p in row for _ in range(scale)] for row in rows for _ in range(scale)]
    raw = b''.join(b'\0' + bytes(c for p in row for c in p) for row in rows)
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xFFFFFFFF)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', len(rows[0]), len(rows), 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    path.write_bytes(png)
