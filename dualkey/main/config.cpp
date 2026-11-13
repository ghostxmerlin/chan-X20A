// Provisioned settings in NVS (namespace "soulkey"). The factory firmware's own namespaces are left alone.
#include <cstring>

#include "app.h"
#include "cJSON.h"
#include "esp_log.h"
#include "nvs.h"

namespace app {
namespace {
const char* TAG = "config";
SemaphoreHandle_t s_lock;
Config s_cfg;
std::atomic<uint32_t> s_gen{0};
std::atomic<uint32_t> s_wifi_gen{0};

void read_str(nvs_handle_t h, const char* key, char* out, size_t n) {
    size_t len = n;
    if (nvs_get_str(h, key, out, &len) != ESP_OK) out[0] = '\0';
}

bool save(const Config& c) {
    nvs_handle_t h;
    if (nvs_open("soulkey", NVS_READWRITE, &h) != ESP_OK) return false;
    bool ok = true;
    ok &= nvs_set_str(h, "ssid0", c.wifi[0].ssid) == ESP_OK;
    ok &= nvs_set_str(h, "pass0", c.wifi[0].pass) == ESP_OK;
    ok &= nvs_set_str(h, "ssid1", c.wifi[1].ssid) == ESP_OK;
    ok &= nvs_set_str(h, "pass1", c.wifi[1].pass) == ESP_OK;
    ok &= nvs_set_str(h, "server", c.server) == ESP_OK;
    ok &= nvs_set_str(h, "token", c.token) == ESP_OK;
    ok &= nvs_set_i32(h, "joy_rot", c.joy_rotate) == ESP_OK;
    ok &= nvs_commit(h) == ESP_OK;
    nvs_close(h);
    return ok;
}

bool copy_str(const cJSON* item, char* out, size_t n, size_t min_len) {
    if (!cJSON_IsString(item)) return false;
    size_t len = strlen(item->valuestring);
    if (len < min_len || len >= n) return false;
    memcpy(out, item->valuestring, len + 1);
    return true;
}
}  // namespace

void config_init() {
    s_lock = xSemaphoreCreateMutex();
    memset(&s_cfg, 0, sizeof(s_cfg));
    nvs_handle_t h;
    if (nvs_open("soulkey", NVS_READONLY, &h) == ESP_OK) {
        read_str(h, "ssid0", s_cfg.wifi[0].ssid, sizeof(s_cfg.wifi[0].ssid));
        read_str(h, "pass0", s_cfg.wifi[0].pass, sizeof(s_cfg.wifi[0].pass));
        read_str(h, "ssid1", s_cfg.wifi[1].ssid, sizeof(s_cfg.wifi[1].ssid));
        read_str(h, "pass1", s_cfg.wifi[1].pass, sizeof(s_cfg.wifi[1].pass));
        read_str(h, "server", s_cfg.server, sizeof(s_cfg.server));
        read_str(h, "token", s_cfg.token, sizeof(s_cfg.token));
        int32_t rot = 0;
        if (nvs_get_i32(h, "joy_rot", &rot) == ESP_OK) s_cfg.joy_rotate = rot;
        nvs_close(h);
    }
    s_cfg.wifi_count = (s_cfg.wifi[0].ssid[0] ? 1 : 0) + (s_cfg.wifi[1].ssid[0] ? 1 : 0);
    if (!s_cfg.wifi[0].ssid[0] && s_cfg.wifi[1].ssid[0]) {
        s_cfg.wifi[0] = s_cfg.wifi[1];
        memset(&s_cfg.wifi[1], 0, sizeof(s_cfg.wifi[1]));
    }
    ESP_LOGI(TAG, "wifi entries=%d server=%s token=%s joystick_rotate=%d", s_cfg.wifi_count,
             s_cfg.server[0] ? "set" : "unset", s_cfg.token[0] ? "set" : "unset", s_cfg.joy_rotate);
}

Config config_get() {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    Config c = s_cfg;
    xSemaphoreGive(s_lock);
    return c;
}

uint32_t config_generation() { return s_gen.load(); }
uint32_t wifi_generation() { return s_wifi_gen.load(); }

// {"wifi":[{"ssid":..,"password":..},{..}],"server":"ws://host:port","token":"..","joystick_rotate":0}
// Every key is optional; "wifi" replaces the whole list. {"forget":1} clears everything.
bool config_apply_json(const char* line, char* err, size_t err_len) {
    cJSON* root = cJSON_Parse(line);
    if (!root || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        snprintf(err, err_len, "not a JSON object");
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    Config c = s_cfg;
    xSemaphoreGive(s_lock);
    bool wifi_changed = false;
    const char* bad = nullptr;

    const cJSON* forget = cJSON_GetObjectItem(root, "forget");
    if (cJSON_IsTrue(forget) || (cJSON_IsNumber(forget) && forget->valueint != 0)) {
        memset(&c, 0, sizeof(c));
        wifi_changed = true;
    }
    const cJSON* wifi = cJSON_GetObjectItem(root, "wifi");
    if (wifi && !bad) {
        int n = cJSON_IsArray(wifi) ? cJSON_GetArraySize(wifi) : -1;
        if (n < 1 || n > 2) {
            bad = "wifi must be a list of 1 or 2 networks";
        } else {
            WifiEntry list[2] = {};
            for (int i = 0; i < n && !bad; ++i) {
                const cJSON* e = cJSON_GetArrayItem(wifi, i);
                if (!copy_str(cJSON_GetObjectItem(e, "ssid"), list[i].ssid, sizeof(list[i].ssid), 1)) bad = "bad ssid";
                const cJSON* pw = cJSON_GetObjectItem(e, "password");
                if (!bad && pw) {
                    if (!copy_str(pw, list[i].pass, sizeof(list[i].pass), 0)) bad = "bad password";
                    size_t pl = strlen(list[i].pass);
                    if (!bad && pl > 0 && pl < 8) bad = "password must be empty or 8..64 chars";
                }
            }
            if (!bad) {
                memcpy(c.wifi, list, sizeof(list));
                c.wifi_count = n;
                wifi_changed = true;
            }
        }
    }
    const cJSON* server = cJSON_GetObjectItem(root, "server");
    if (server && !bad) {
        char tmp[sizeof(c.server)];
        if (!copy_str(server, tmp, sizeof(tmp), 6) || (strncmp(tmp, "ws://", 5) && strncmp(tmp, "wss://", 6))) {
            bad = "server must be ws://host:port or wss://host:port";
        } else {
            size_t l = strlen(tmp);
            while (l && tmp[l - 1] == '/') tmp[--l] = '\0';
            memcpy(c.server, tmp, l + 1);
        }
    }
    const cJSON* token = cJSON_GetObjectItem(root, "token");
    if (token && !bad && !copy_str(token, c.token, sizeof(c.token), 1)) bad = "bad token";
    const cJSON* rot = cJSON_GetObjectItem(root, "joystick_rotate");
    if (rot && !bad) {
        int v = cJSON_IsNumber(rot) ? rot->valueint : -1;
        if (v != 0 && v != 90 && v != 180 && v != 270) bad = "joystick_rotate must be 0, 90, 180 or 270";
        else c.joy_rotate = v;
    }
    cJSON_Delete(root);
    if (bad) {
        snprintf(err, err_len, "%s", bad);
        return false;
    }
    if (!save(c)) {
        snprintf(err, err_len, "NVS write failed");
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_cfg = c;
    xSemaphoreGive(s_lock);
    if (wifi_changed) s_wifi_gen.fetch_add(1);
    s_gen.fetch_add(1);
    ESP_LOGI(TAG, "saved: wifi entries=%d server=%s token=%s joystick_rotate=%d", c.wifi_count,
             c.server[0] ? "set" : "unset", c.token[0] ? "set" : "unset", c.joy_rotate);
    return true;
}

}  // namespace app
