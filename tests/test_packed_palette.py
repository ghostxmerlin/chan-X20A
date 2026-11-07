import hashlib
import pathlib
import re
import subprocess
import tempfile
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class PackedPaletteTest(unittest.TestCase):
    def test_all_original_animation_pixels_preserved(self):
        source=(ROOT/'upstream/StackChan/firmware/main/collector/laughing_man.c').read_text()
        array=re.search(r'const uint8_t collector_laughing_man_frames\[\] = \{(.*?)\};',source,re.S)[1]
        data=bytes(map(int,re.findall(r'\d+',array)))
        self.assertEqual(len(data),120*4674)
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp);(p/'data').write_bytes(data)
            (p/'check.cpp').write_text('''#include "packed_palette.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
int main(int argc,char** argv){if(argc!=2)return 1;std::ifstream f(argv[1],std::ios::binary);std::vector<uint8_t>d((std::istreambuf_iterator<char>(f)),{});
for(unsigned frame=0;frame<120;frame++)for(unsigned pixel=0;pixel<7478;pixel++)std::cout.put(collector::palette5(d.data()+frame*4674,pixel));}
''')
            subprocess.run(['c++','-std=c++17','-I'+str(ROOT/'upstream/StackChan/firmware/main/collector'),str(p/'check.cpp'),'-o',str(p/'check')],check=True)
            decoded=subprocess.check_output([str(p/'check'),str(p/'data')])
            self.assertEqual(hashlib.sha256(decoded).hexdigest(),'dc5a34ded70f429603d89acdc717246c91dbf4196bdf3cb90863b6f10ef1523f')
