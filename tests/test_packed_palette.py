import hashlib
import pathlib
import re
import subprocess
import tempfile
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class PackedPaletteTest(unittest.TestCase):
    def test_all_original_animation_pixels_preserved(self):
        # The device decoder (packed_palette.h) must return exactly the 5-bit indexes the
        # generator packed (scripts/generate-screen-assets.py: LSB-first, 5 bits per pixel).
        # Sizes come from the generated header, so regenerating the artwork (2026-11-10: 90%
        # diameter) keeps this test meaningful instead of pinning one drawing.
        collector=ROOT/'upstream/StackChan/firmware/main/collector'
        header=(collector/'laughing_man.h').read_text()
        frames=int(re.search(r'COLLECTOR_FACE_FRAME_COUNT = (\d+)',header)[1])
        pixels=int(re.search(r'COLLECTOR_FACE_CHANGED_PIXELS = (\d+)',header)[1])
        frame_bytes=int(re.search(r'COLLECTOR_FACE_FRAME_BYTES = (\d+)',header)[1])
        self.assertEqual(frame_bytes,(pixels*5+7)//8)
        source=(collector/'laughing_man.c').read_text()
        array=re.search(r'const uint8_t collector_laughing_man_frames\[\] = \{(.*?)\};',source,re.S)[1]
        data=bytes(map(int,re.findall(r'\d+',array)))
        self.assertEqual(len(data),frames*frame_bytes)
        palette=re.search(r'const uint16_t collector_laughing_man_palette\[\] = \{(.*?)\};',source,re.S)[1]
        colors=len(re.findall(r'\d+',palette))
        self.assertLessEqual(colors,32)
        expected=bytearray()
        for f in range(frames):
            block=int.from_bytes(data[f*frame_bytes:(f+1)*frame_bytes],'little')
            expected.extend((block>>(5*i))&31 for i in range(pixels))
        self.assertLess(max(expected),colors)
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp);(p/'data').write_bytes(data)
            (p/'check.cpp').write_text(f'''#include "packed_palette.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
int main(int argc,char** argv){{if(argc!=2)return 1;std::ifstream f(argv[1],std::ios::binary);std::vector<uint8_t>d((std::istreambuf_iterator<char>(f)),{{}});
for(unsigned frame=0;frame<{frames};frame++)for(unsigned pixel=0;pixel<{pixels};pixel++)std::cout.put(collector::palette5(d.data()+frame*{frame_bytes},pixel));}}
''')
            subprocess.run(['c++','-std=c++17','-I'+str(collector),str(p/'check.cpp'),'-o',str(p/'check')],check=True)
            decoded=subprocess.check_output([str(p/'check'),str(p/'data')])
            self.assertEqual(decoded,bytes(expected))
