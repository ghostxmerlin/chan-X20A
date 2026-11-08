"""Real interaction policy/coordinator with fake device boundaries, no motion."""
import pathlib
import subprocess
import tempfile
import unittest

class InteractionTest(unittest.TestCase):
    def test_owner_gate_and_mode_policy(self):
        self.compile_run('interaction_policy_test.cpp', False)

    def test_actual_coordinator_ownership_modes_and_commands(self):
        self.compile_run('interaction_service_test.cpp', True)

    def compile_run(self, test, service):
        root = pathlib.Path(__file__).resolve().parents[1]
        main = root/'upstream/StackChan/firmware/main'
        with tempfile.TemporaryDirectory(prefix='chan-interaction-') as directory:
            d = pathlib.Path(directory)
            (d/'esp_err.h').write_text('#pragma once\nusing esp_err_t=int;\nconstexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_INVALID_ARG=0x102,ESP_ERR_INVALID_STATE=0x103;\n')
            (d/'esp_timer.h').write_text('#pragma once\n#include <cstdint>\nint64_t esp_timer_get_time();\n')
            (d/'esp_http_server.h').write_text('''#pragma once
#include "esp_err.h"
#include <string>
struct httpd_req_t {int content_len=0;size_t received=0;std::string body,response,status;};
int httpd_req_recv(httpd_req_t*,char*,size_t);
esp_err_t httpd_resp_set_status(httpd_req_t*,const char*);
esp_err_t httpd_resp_set_type(httpd_req_t*,const char*);
esp_err_t httpd_resp_send(httpd_req_t*,const char*,size_t);
''')
            args=['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(d),'-I'+str(main),'-I'+str(root/'upstream/StackChan/firmware/components/ArduinoJson/src'),str(root/'tests'/test)]
            if service: args.append(str(main/'collector/interaction.cpp'))
            args += ['-o',str(d/'test')]
            subprocess.run(args,check=True)
            subprocess.run([str(d/'test')],check=True)
