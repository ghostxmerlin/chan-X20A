// USB Serial/JTAG provisioning console. One JSON object per line:
//   {"wifi":[{"ssid":"..","password":".."}],"server":"ws://host:port","token":".."}  -> {"ok":true}
//   {"status":1}                                                                     -> {"ok":true,"status":{..}}
//   {"reboot":1}                                                                     -> {"ok":true}, then restart
// Replies are single lines starting with {"ok": so the Mac helper can pick them out of the log stream.
// Secrets (password, token) are never echoed or logged.
#include <cstdio>
#include <cstring>

#include "app.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/task.h"
#include "lwip/ip4_addr.h"

namespace app {
namespace {
const char* TAG = "console";

void reply(const char* json) {
    printf("%s\n", json);
    fflush(stdout);
}

void json_escape(const char* in, char* out, size_t n) {
    size_t o = 0;
    for (; *in && o + 7 < n; ++in) {
        unsigned char ch = (unsigned char)*in;
        if (ch == '"' || ch == '\\') {
            out[o++] = '\\';
            out[o++] = ch;
        } else if (ch < 0x20) {
            o += snprintf(out + o, n - o, "\\u%04x", ch);
        } else {
            out[o++] = ch;
        }
    }
    out[o] = '\0';
}

void status_reply() {
    Config c = config_get();
    QueueStats q = queue_stats();
    char ssid[80] = "", url[200] = "", err[100] = "", mode[70] = "", ip[16] = "0.0.0.0";
    int idx = g_status.wifi_index.load();
    if (g_status.wifi_up && idx >= 0 && idx < 2) json_escape(c.wifi[idx].ssid, ssid, sizeof(ssid));
    if (c.server[0]) {
        char full[160];
        snprintf(full, sizeof(full), "%s/signal", c.server);
        json_escape(full, url, sizeof(url));
    }
    json_escape(g_status.last_error, err, sizeof(err));
    json_escape(g_status.mode, mode, sizeof(mode));
    ip4_addr_t a;
    a.addr = g_status.ip.load();
    if (g_status.wifi_up) ip4addr_ntoa_r(&a, ip, sizeof(ip));
    char saved[2][80] = {"", ""};
    for (int i = 0; i < 2; ++i) json_escape(c.wifi[i].ssid, saved[i], sizeof(saved[i]));
    static char out[1100];
    snprintf(out, sizeof(out),
             "{\"ok\":true,\"status\":{\"fw\":\"%s\",\"device\":\"%s\",\"boot\":\"%s\",\"uptime_ms\":%u,"
             "\"wifi\":{\"saved\":[\"%s\",\"%s\"],\"connected\":%s,\"ssid\":\"%s\",\"ip\":\"%s\",\"rssi\":%d},"
             "\"server\":{\"url\":\"%s\",\"token_set\":%s,\"connected\":%s,\"hello_ok\":%s,\"connects\":%u,"
             "\"failures\":%u,\"mode\":\"%s\"},"
             "\"queue\":{\"pending\":%u,\"dropped\":%u,\"last_seq\":%u,\"acked\":%u,\"sent\":%u},"
             "\"joystick\":{\"found\":%s,\"bus\":%d,\"id\":%d,\"x\":%d,\"y\":%d,\"rotate\":%d},"
             "\"heap_free\":%u,\"last_error\":\"%s\"}}",
             DUALKEY_FW, device_name(), boot_id(), (unsigned)now_ms(), saved[0], saved[1],
             g_status.wifi_up ? "true" : "false", ssid, ip, g_status.rssi.load(), url, c.token[0] ? "true" : "false",
             g_status.ws_up ? "true" : "false", g_status.hello_ok ? "true" : "false",
             (unsigned)g_status.ws_connects.load(), (unsigned)g_status.ws_failures.load(), mode, (unsigned)q.pending,
             (unsigned)q.dropped, (unsigned)q.last_seq, (unsigned)q.acked, (unsigned)g_status.signals_sent.load(),
             g_status.joystick_found ? "true" : "false", g_status.joystick_bus.load(), g_status.joystick_id.load(),
             g_status.joy_x.load(), g_status.joy_y.load(), c.joy_rotate,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), err);
    reply(out);
}

void handle_line(char* line) {
    while (*line == ' ' || *line == '\t') ++line;
    if (*line != '{') return;
    if (strstr(line, "\"status\"") && !strstr(line, "\"wifi\"") && !strstr(line, "\"token\"")) {
        status_reply();
        return;
    }
    if (strstr(line, "\"reboot\"") && !strstr(line, "\"wifi\"")) {
        reply("{\"ok\":true}");
        vTaskDelay(pdMS_TO_TICKS(200));
        esp_restart();
    }
    char err[64];
    if (config_apply_json(line, err, sizeof(err))) {
        reply("{\"ok\":true}");
    } else {
        char out[128];
        snprintf(out, sizeof(out), "{\"ok\":false,\"error\":\"%s\"}", err);
        reply(out);
    }
    memset(line, 0, strlen(line));  // do not leave the token or password in the buffer
}

void console_task(void*) {
    static char line[1024];
    size_t len = 0;
    bool overflow = false;
    uint8_t buf[64];
    for (;;) {
        int n = usb_serial_jtag_read_bytes(buf, sizeof(buf), pdMS_TO_TICKS(200));
        for (int i = 0; i < n; ++i) {
            char ch = (char)buf[i];
            if (ch == '\r' || ch == '\n') {
                if (!overflow && len) {
                    line[len] = '\0';
                    handle_line(line);
                } else if (overflow) {
                    reply("{\"ok\":false,\"error\":\"line too long\"}");
                }
                len = 0;
                overflow = false;
            } else if (len + 1 < sizeof(line)) {
                line[len++] = ch;
            } else {
                overflow = true;
            }
        }
    }
}
}  // namespace

void console_start() {
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    cfg.rx_buffer_size = 1024;
    cfg.tx_buffer_size = 1024;
    if (usb_serial_jtag_driver_install(&cfg) != ESP_OK) {
        ESP_LOGE(TAG, "usb_serial_jtag driver install failed");
        return;
    }
    usb_serial_jtag_vfs_use_driver();
    xTaskCreate(console_task, "console", 4096, nullptr, 4, nullptr);
    ESP_LOGI(TAG, "ready: send one JSON line, e.g. {\"status\":1}");
}

}  // namespace app
