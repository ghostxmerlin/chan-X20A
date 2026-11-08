from pathlib import Path
import subprocess
import shlex
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EyesTest(unittest.TestCase):
    def test_voice_cue_starts_only_after_successful_media_submission(self):
        with tempfile.TemporaryDirectory() as d:
            binary=Path(d)/'voice-cue'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(ROOT/'tests/eyes_voice_test.cpp'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

    def test_manual_slow_first_frame_and_voice_deadline(self):
        self.run_player_case('deadline')

    def test_natural_cycles_preserve_priority_and_complete_animation_rounds(self):
        self.run_player_case('natural')

    def test_explicit_voice_boot_activation_then_enters_natural_cycle(self):
        self.run_player_case('voice-boot')

    def run_player_case(self, case):
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d)/'eyes'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                            str(ROOT/'tests/eyes_player_test.cpp'),
                            str(ROOT/'upstream/StackChan/firmware/main/collector/eyes/player.cpp'),
                            '-o',str(binary)],check=True)
            subprocess.run([str(binary),case],check=True,timeout=30)
    def test_strict_command_protocol(self):
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d)/'protocol'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-I'+str(ROOT/'upstream/StackChan/firmware/components/ArduinoJson/src'),
                            str(ROOT/'tests/eyes_protocol_test.cpp'),
                            str(ROOT/'upstream/StackChan/firmware/main/collector/eyes/player.cpp'),
                            '-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
    def test_player_request_lifecycle_and_frame_ownership(self):
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d)/'eyes'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                            str(ROOT/'tests/eyes_player_test.cpp'),
                            str(ROOT/'upstream/StackChan/firmware/main/collector/eyes/player.cpp'),
                            '-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

    def test_real_assets_service_and_cancel_during_load(self):
        flags = shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','openssl'],text=True))
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d)/'eyes-service'
            folder = ROOT/'upstream/StackChan/firmware/main/collector/eyes'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                            '-I'+str(ROOT/'assets/soul-eyes/v2-front/decoder'),
                            str(ROOT/'tests/eyes_service_test.cpp'),str(folder/'player.cpp'),str(folder/'eyes.cpp'),
                            *flags,'-o',str(binary)],check=True)
            subprocess.run([str(binary),str(ROOT/'assets/soul-eyes/v2-front/sdcard/soul')],check=True)
            subprocess.run([str(binary),str(ROOT/'assets/soul-eyes/v2-front/sdcard/soul'),'--worker-failure'],check=True)

    def test_visible_transitions_never_reveal_legacy_until_explicitly_hidden(self):
        self.run_service_case('--transitions')

    def test_slow_asset_load_and_natural_failure_backoff(self):
        self.run_service_case('--recovery')

    def test_real_assets_natural_full_rounds_and_style_switch(self):
        self.run_service_case('--natural')

    def run_service_case(self, case):
        flags = shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','openssl'],text=True))
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d)/'eyes-service'
            folder = ROOT/'upstream/StackChan/firmware/main/collector/eyes'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                            '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                            '-I'+str(ROOT/'assets/soul-eyes/v2-front/decoder'),
                            str(ROOT/'tests/eyes_service_test.cpp'),str(folder/'player.cpp'),str(folder/'eyes.cpp'),
                            *flags,'-o',str(binary)],check=True)
            subprocess.run([str(binary),str(ROOT/'assets/soul-eyes/v2-front/sdcard/soul'),case],check=True,timeout=30)
