import json
import pathlib
import subprocess
import tempfile
import unittest
import shlex


class VoiceLibraryTest(unittest.TestCase):
    def test_sd_decode_metadata_errors_and_no_flash_fallback(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        assets = root / '.private/voice-library'
        if not (assets/'voice_assets.h').exists():
            self.skipTest('Prepare local voice assets first')
        with tempfile.TemporaryDirectory() as directory:
            d = pathlib.Path(directory)
            (d/'esp_err.h').write_text('using esp_err_t=int;\n')
            (d/'esp_lcd_panel_io.h').write_text('using esp_lcd_panel_io_handle_t=void*;\n')
            (d/'esp_heap_caps.h').write_text('''#include <cstdlib>
extern bool fail_voice_allocation;extern int voice_allocations;
constexpr int MALLOC_CAP_SPIRAM=1,MALLOC_CAP_8BIT=2;
inline void* heap_caps_malloc(size_t n,int){if(fail_voice_allocation)return nullptr;void* p=malloc(n);if(p)++voice_allocations;return p;}
inline void heap_caps_free(void* p){if(p)--voice_allocations;free(p);}
''')
            main = root/'upstream/StackChan/firmware/main'
            flags = shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','openssl'],text=True))
            binary = d/'voice-sd-test'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                            '-I'+str(d),'-I'+str(main),'-I'+str(assets),
                            '-I'+str(root/'upstream/StackChan/firmware/components/ArduinoJson/src'),
                            str(root/'tests/voice_sd_test.cpp'),str(main/'collector/voice_library.cpp'),
                            *flags,'-o',str(binary)],check=True)
            subprocess.run([str(binary),str(assets)],check=True)

    def test_decoder_policy_and_local_library(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        assets = root / '.private/voice-library'
        with tempfile.TemporaryDirectory() as directory:
            binary = pathlib.Path(directory) / 'voice-test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(root/'tests/voice_codec_test.cpp'), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
            if not (assets/'manifest.json').exists():
                return  # Pure malformed-input/policy regressions always run on clean checkouts.
            manifest = json.loads((assets/'manifest.json').read_text())
            self.assertEqual(len(manifest['clips']),40)
            for clip in manifest['clips']:
                decoded = pathlib.Path(directory) / 'decoded.pcm'
                subprocess.run([str(binary), str(assets/(clip['source_id']+'.adpcm')),
                                str(clip['frames']), str(decoded)], check=True)
                self.assertEqual(decoded.read_bytes(),
                                 (assets/(clip['source_id']+'.reference.pcm')).read_bytes()[:clip['frames']*2])
