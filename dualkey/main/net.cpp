// Wi-Fi STA (up to two saved networks) and the WebSocket to the ear server's /signal endpoint.
//
// Session: connect with "Authorization: Bearer <token>", send hello, wait for {"type":"hello","ok":true},
// then send every unacknowledged signal (oldest first) and new ones as they come; {"type":"ack","seq":N}
// removes N from the queue. {"type":"ping"} every 15 s. Any failure closes the socket and retries after
// 1 s, 2 s, 4 s ... 30 s; the delay resets once a server hello arrives. Everything still unacknowledged is
// sent again on the next session (the server dedupes by device + boot + seq).
#include <cstring>

#include "app.h"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"

namespace app {
namespace {
const char* TAG = "net";

constexpr EventBits_t kGotIp = BIT0;
constexpr EventBits_t kWifiDown = BIT1;
EventGroupHandle_t s_wifi_events;
esp_netif_t* s_netif;

// ---------------------------------------------------------------- Wi-Fi
void on_wifi(void*, esp_event_base_t base, int32_t id, void* data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        auto* d = (wifi_event_sta_disconnected_t*)data;
        if (g_status.wifi_up) ESP_LOGW(TAG, "wifi disconnected, reason %d", d ? d->reason : -1);
        g_status.wifi_up = false;
        g_status.ip = 0;
        xEventGroupSetBits(s_wifi_events, kWifiDown);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        auto* e = (ip_event_got_ip_t*)data;
        g_status.ip = e->ip_info.ip.addr;
        g_status.wifi_up = true;
        ESP_LOGI(TAG, "got ip " IPSTR, IP2STR(&e->ip_info.ip));
        xEventGroupSetBits(s_wifi_events, kGotIp);
    }
}

void wifi_init() {
    s_wifi_events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    s_netif = esp_netif_create_default_wifi_sta();
    esp_netif_set_hostname(s_netif, device_name());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    // Keep our networks in our own NVS namespace; do not touch the factory firmware's Wi-Fi record.
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, on_wifi, nullptr);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi, nullptr);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
}

bool wifi_try(const WifiEntry& w, int index) {
    wifi_config_t wc = {};
    memcpy(wc.sta.ssid, w.ssid, strlen(w.ssid));
    memcpy(wc.sta.password, w.pass, strlen(w.pass));
    wc.sta.threshold.authmode = w.pass[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    wc.sta.pmf_cfg.capable = true;
    wc.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    wc.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wc.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    esp_wifi_disconnect();
    xEventGroupClearBits(s_wifi_events, kGotIp | kWifiDown);
    esp_wifi_set_config(WIFI_IF_STA, &wc);
    ESP_LOGI(TAG, "joining wifi #%d", index);
    if (esp_wifi_connect() != ESP_OK) return false;
    // A failed attempt reports a disconnect; a good one an IP. 20 s covers a slow DHCP.
    EventBits_t bits = xEventGroupWaitBits(s_wifi_events, kGotIp | kWifiDown, pdTRUE, pdFALSE, pdMS_TO_TICKS(20000));
    if (bits & kGotIp) {
        g_status.wifi_index = index;
        return true;
    }
    ESP_LOGW(TAG, "wifi #%d failed", index);
    return false;
}

bool wifi_round(const Config& c) {
    int first = g_status.wifi_index.load();
    if (first < 0 || first >= c.wifi_count) first = 0;
    for (int k = 0; k < c.wifi_count; ++k) {
        int i = (first + k) % c.wifi_count;
        if (wifi_try(c.wifi[i], i)) return true;
    }
    set_last_error("wifi join failed");
    return false;
}

// ---------------------------------------------------------------- WebSocket events -> net task
enum class WsKind : uint8_t { Connected, Closed, Text };
struct WsEvent {
    WsKind kind;
    uint16_t len;
    char text[384];
};
QueueHandle_t s_ws_events;
char s_frag[384];
size_t s_frag_len;

void on_ws(void*, esp_event_base_t, int32_t id, void* data) {
    auto* d = (esp_websocket_event_data_t*)data;
    WsEvent ev{};
    switch (id) {
        case WEBSOCKET_EVENT_CONNECTED:
            ev.kind = WsKind::Connected;
            xQueueSend(s_ws_events, &ev, 0);
            break;
        case WEBSOCKET_EVENT_ERROR:
            if (d && d->error_handle.esp_ws_handshake_status_code) {
                char e[48];
                snprintf(e, sizeof(e), "handshake HTTP %d", d->error_handle.esp_ws_handshake_status_code);
                set_last_error(e);
            } else {
                set_last_error("websocket error");
            }
            ev.kind = WsKind::Closed;
            xQueueSend(s_ws_events, &ev, 0);
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
        case WEBSOCKET_EVENT_CLOSED:
            ev.kind = WsKind::Closed;
            xQueueSend(s_ws_events, &ev, 0);
            break;
        case WEBSOCKET_EVENT_DATA:
            if (!d || (d->op_code != 0x1 && d->op_code != 0x0)) break;  // text frames (and continuations) only
            if (d->payload_offset == 0) s_frag_len = 0;
            if (s_frag_len + d->data_len < sizeof(s_frag)) {
                memcpy(s_frag + s_frag_len, d->data_ptr, d->data_len);
                s_frag_len += d->data_len;
            }
            if (d->payload_offset + d->data_len >= d->payload_len) {
                ev.kind = WsKind::Text;
                ev.len = (uint16_t)(s_frag_len < sizeof(ev.text) - 1 ? s_frag_len : sizeof(ev.text) - 1);
                memcpy(ev.text, s_frag, ev.len);
                ev.text[ev.len] = '\0';
                s_frag_len = 0;
                xQueueSend(s_ws_events, &ev, 0);
            }
            break;
        default:
            break;
    }
}

uint32_t mode_colour(const char* name) {
    if (!name[0] || !strcmp(name, "none") || !strcmp(name, "idle")) return 0;
    static const uint32_t palette[] = {0x301800, 0x000030, 0x003000, 0x200030, 0x302000, 0x002828};
    uint32_t h = 2166136261u;
    for (const char* p = name; *p; ++p) h = (h ^ (uint8_t)*p) * 16777619u;
    return palette[h % (sizeof(palette) / sizeof(palette[0]))];
}

// Handles one server message. Returns false if the session should end.
void handle_server(const char* text, bool& hello_ok, dualkey::Backoff& backoff) {
    cJSON* root = cJSON_Parse(text);
    if (!root) return;
    const cJSON* type = cJSON_GetObjectItem(root, "type");
    const char* t = cJSON_IsString(type) ? type->valuestring : "";
    if (!strcmp(t, "hello")) {
        if (cJSON_IsTrue(cJSON_GetObjectItem(root, "ok"))) {
            hello_ok = true;
            g_status.hello_ok = true;
            backoff.reset();
            with_queue([](dualkey::SignalQueue& q) { q.mark_unsent(); });
            ESP_LOGI(TAG, "server hello ok");
            set_last_error("");
        } else {
            set_last_error("server refused hello");
        }
    } else if (!strcmp(t, "ack")) {
        const cJSON* seq = cJSON_GetObjectItem(root, "seq");
        if (cJSON_IsNumber(seq)) {
            bool found = false;
            uint32_t s = (uint32_t)seq->valuedouble;
            with_queue([&](dualkey::SignalQueue& q) { found = q.ack(s); });
            if (found) g_acked.fetch_add(1);
            ESP_LOGI(TAG, "ack seq=%u%s", (unsigned)s, found ? "" : " (not pending)");
        }
    } else if (!strcmp(t, "mode")) {
        const cJSON* cur = cJSON_GetObjectItem(root, "current");
        const char* name = cJSON_IsString(cur) ? cur->valuestring : "";
        snprintf(g_status.mode, sizeof(g_status.mode), "%s", name);
        g_joy_led_rgb = mode_colour(g_status.mode);
        ESP_LOGI(TAG, "server mode: %s", g_status.mode);
    }
    cJSON_Delete(root);
}

bool wait_config_or(uint32_t ms, uint32_t gen) {
    // Sleeps up to ms; returns early (true) if the configuration changed.
    for (uint32_t waited = 0; waited < ms; waited += 100) {
        if (config_generation() != gen) return true;
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    return false;
}

// One WebSocket session; returns when it ends.
void ws_session(const Config& c, dualkey::Backoff& backoff) {
    uint32_t gen = config_generation();
    char uri[160], headers[160];
    snprintf(uri, sizeof(uri), "%s/signal", c.server);
    snprintf(headers, sizeof(headers), "Authorization: Bearer %s\r\n", c.token);
    esp_websocket_client_config_t wc = {};
    wc.uri = uri;
    wc.headers = headers;
    wc.disable_auto_reconnect = true;
    wc.network_timeout_ms = 8000;
    wc.ping_interval_sec = 10;
    wc.pingpong_timeout_sec = 30;
    wc.buffer_size = 1024;
    wc.task_stack = 4608;
    wc.task_prio = 5;
    if (!strncmp(c.server, "wss://", 6)) wc.crt_bundle_attach = esp_crt_bundle_attach;

    xQueueReset(s_ws_events);
    s_frag_len = 0;
    esp_websocket_client_handle_t client = esp_websocket_client_init(&wc);
    memset(headers, 0, sizeof(headers));
    if (!client) {
        set_last_error("websocket init failed");
        return;
    }
    esp_websocket_register_events(client, WEBSOCKET_EVENT_ANY, on_ws, nullptr);
    ESP_LOGI(TAG, "connecting %s", uri);
    esp_websocket_client_start(client);

    bool connected = false, hello_ok = false;
    uint32_t t_connect = now_ms(), last_ping = now_ms();
    char frame[dualkey::kFrameMax];
    for (;;) {
        WsEvent ev;
        if (xQueueReceive(s_ws_events, &ev, pdMS_TO_TICKS(30)) == pdTRUE) {
            if (ev.kind == WsKind::Closed) break;
            if (ev.kind == WsKind::Connected) {
                connected = true;
                g_status.ws_up = true;
                g_status.ws_connects.fetch_add(1);
                t_connect = last_ping = now_ms();
                int n = snprintf(frame, sizeof(frame), "{\"type\":\"hello\",\"device\":\"%s\",\"fw\":\"%s\",\"boot\":\"%s\"}",
                                 device_name(), DUALKEY_FW, boot_id());
                if (esp_websocket_client_send_text(client, frame, n, pdMS_TO_TICKS(3000)) < 0) break;
                ESP_LOGI(TAG, "connected, hello sent");
            } else if (ev.kind == WsKind::Text) {
                handle_server(ev.text, hello_ok, backoff);
            }
        }
        uint32_t now = now_ms();
        if (!g_status.wifi_up || config_generation() != gen) break;
        if (!connected) {
            if (now - t_connect > 12000) {
                set_last_error("connect timeout");
                break;
            }
            continue;
        }
        if (!hello_ok) {
            if (now - t_connect > 8000) {
                set_last_error("no hello from server");
                break;
            }
            continue;
        }
        // Drain unsent signals, oldest first.
        bool send_failed = false;
        for (int burst = 0; burst < 8; ++burst) {
            uint32_t seq = 0;
            int n = -1;
            with_queue([&](dualkey::SignalQueue& q) {
                const dualkey::Signal* s = q.next_unsent();
                if (s) {
                    seq = s->seq;
                    n = dualkey::render_signal(*s, now_ms(), frame, sizeof(frame));
                }
            });
            if (!seq) break;
            if (n < 0 || esp_websocket_client_send_text(client, frame, n, pdMS_TO_TICKS(3000)) < 0) {
                send_failed = true;
                break;
            }
            with_queue([&](dualkey::SignalQueue& q) { q.mark_sent(seq); });
            g_status.signals_sent.fetch_add(1);
            ESP_LOGI(TAG, "sent %s", frame);
        }
        if (send_failed) {
            set_last_error("send failed");
            break;
        }
        if (now - last_ping >= 15000) {
            last_ping = now;
            if (esp_websocket_client_send_text(client, "{\"type\":\"ping\"}", 15, pdMS_TO_TICKS(3000)) < 0) {
                set_last_error("ping send failed");
                break;
            }
            wifi_ap_record_t ap;
            if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) g_status.rssi = ap.rssi;
        }
    }
    if (!hello_ok) g_status.ws_failures.fetch_add(1);
    g_status.ws_up = false;
    g_status.hello_ok = false;
    esp_websocket_client_close(client, pdMS_TO_TICKS(500));
    esp_websocket_client_destroy(client);
    ESP_LOGI(TAG, "session ended");
}

void net_task(void*) {
    wifi_init();
    dualkey::Backoff wifi_backoff, ws_backoff;
    uint32_t joined_wifi_gen = wifi_generation();
    for (;;) {
        Config c = config_get();
        uint32_t gen = config_generation();
        if (c.wifi_count == 0) {
            wait_config_or(1000, gen);
            continue;
        }
        if (wifi_generation() != joined_wifi_gen) {
            joined_wifi_gen = wifi_generation();
            g_status.wifi_index = -1;
            esp_wifi_disconnect();
            g_status.wifi_up = false;
            wifi_backoff.reset();
        }
        if (!g_status.wifi_up) {
            if (!wifi_round(c)) {
                uint32_t d = wifi_backoff.next();
                ESP_LOGW(TAG, "no wifi; retry in %u ms", (unsigned)d);
                wait_config_or(d, gen);
                continue;
            }
            wifi_backoff.reset();
            ws_backoff.reset();
        }
        if (!c.server[0] || !c.token[0]) {
            wait_config_or(1000, gen);
            continue;
        }
        ws_session(c, ws_backoff);
        if (config_generation() != gen || !g_status.wifi_up) continue;
        uint32_t d = ws_backoff.next();
        ESP_LOGW(TAG, "server unreachable (%s); retry in %u ms", g_status.last_error, (unsigned)d);
        wait_config_or(d, gen);
    }
}
}  // namespace

void net_start() {
    s_ws_events = xQueueCreate(8, sizeof(WsEvent));
    xTaskCreate(net_task, "net", 6144, nullptr, 5, nullptr);
}

}  // namespace app
