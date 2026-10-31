/* SPDX-License-Identifier: MIT */
#include "collector.h"
#include "remote.h"
#include "remote_protocol.h"
#include <ArduinoJson.hpp>
#include <board.h>
#include <audio/audio_codec.h>
#include <ssid_manager.h>
#include <hal/board/hal_bridge.h>
#include <hal/board/stackchan_display.h>
#include <hal/drivers/bmi270/bmi270.h>
#include <hal/drivers/Si12T/Si12T.h>
#include <esp_app_desc.h>
#include <esp_event.h>
#include <esp_heap_caps.h>
#include <esp_http_server.h>
#include <esp_mac.h>
#include <esp_netif.h>
#include <esp_random.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#include <esp_log.h>
#include <nvs_flash.h>
#include <driver/usb_serial_jtag.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <lvgl.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace collector {
namespace {
using namespace ArduinoJson;
constexpr char kTag[] = "Collector";
constexpr uint32_t kSampleRate = 24000;
constexpr uint16_t kChannels = 2;
constexpr uint32_t kFrames = 480;
constexpr size_t kPcmBytes = kFrames * kChannels * sizeof(int16_t);
constexpr size_t kRingBlocks = 64;
constexpr char kVersion[] = FIRMWARE_VERSION;
// End-to-end target, below the GC0308's configured 20 fps sensor mode.
// Audio retains higher task priority; a slow camera never accumulates a queue.
constexpr uint32_t kCameraTargetFps = 10;
constexpr int64_t kCameraPeriodUs = 1000000 / kCameraTargetFps;
std::string token, device_id, boot_id;
std::atomic_bool wifi_connected{false}, audio_client{false}, audio_ready{false};
std::atomic<uint32_t> audio_errors{0}, camera_errors{0}, audio_sent{0}, audio_skipped{0};
std::atomic<uint32_t> frame_seq{0};
std::atomic<int64_t> last_audio_us{0};
std::atomic<uint32_t> camera_processing_us{0}, camera_jpeg_us{0}, camera_jpeg_bytes{0};
std::mutex audio_mutex, frame_mutex, sensor_mutex;

struct AudioBlock {
    uint32_t seq;
    int64_t timestamp_us;
    std::array<int16_t, kFrames * kChannels> samples;
};
AudioBlock* audio_ring = nullptr;
uint32_t audio_sequence = 0;
struct Frame {
    uint8_t* bytes = nullptr;
    size_t length = 0;
    uint32_t seq = 0;
    int64_t timestamp_us = 0;
    int width = 0, height = 0;
    ~Frame() { free(bytes); }
};
std::shared_ptr<Frame> latest_frame;
struct Sensors {
    uint32_t seq = 0;
    int64_t timestamp_us = 0, imu_us = 0, head_us = 0;
    bool imu_valid = false, head_valid = false;
    BMI270_Data imu{};
    uint8_t head[3]{};
};
Sensors sensors;
constexpr size_t kImuHistory = 100; // 2 seconds at 50 Hz; loss after this is reported.
std::array<Sensors, kImuHistory> imu_history;
std::vector<SsidItem> networks;
esp_netif_t* station = nullptr;
lv_obj_t* status_label = nullptr;

std::string random_hex(size_t bytes)
{
    std::array<uint8_t, 16> data{};
    esp_fill_random(data.data(), bytes);
    std::string result;
    constexpr char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < bytes; ++i) {
        result += hex[data[i] >> 4];
        result += hex[data[i] & 15];
    }
    return result;
}

std::string read_nvs_string(nvs_handle_t handle, const char* key)
{
    size_t size = 0;
    if (nvs_get_str(handle, key, nullptr, &size) != ESP_OK || size == 0 || size > 256) return {};
    std::string value(size, '\0');
    if (nvs_get_str(handle, key, value.data(), &size) != ESP_OK) return {};
    value.resize(size - 1);
    return value;
}

void load_identity()
{
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_efuse_mac_get_default(mac));
    char id[13];
    snprintf(id, sizeof(id), "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    device_id = id;
    boot_id = random_hex(8);
    nvs_handle_t handle;
    ESP_ERROR_CHECK(nvs_open("collector", NVS_READWRITE, &handle));
    token = read_nvs_string(handle, "token");
    if (token.size() != 32 || token.find_first_not_of("0123456789abcdef") != std::string::npos) {
        token = random_hex(16);
        ESP_ERROR_CHECK(nvs_set_str(handle, "token", token.c_str()));
        ESP_ERROR_CHECK(nvs_commit(handle));
    }
    nvs_close(handle);
}

void wifi_event(void*, esp_event_base_t base, int32_t id, void*)
{
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) wifi_connected = true;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) wifi_connected = false;
}

void init_wifi()
{
    // Read factory Wi-Fi credentials, never rewrite that namespace or calibration.
    nvs_handle_t handle;
    if (nvs_open("collector", NVS_READONLY, &handle) == ESP_OK) {
        auto ssid = read_nvs_string(handle, "wifi_ssid");
        auto password = read_nvs_string(handle, "wifi_password");
        if (!ssid.empty()) networks.push_back({ssid, password});
        nvs_close(handle);
    }
    if (networks.empty()) networks = SsidManager::GetInstance().GetSsidList();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    station = esp_netif_create_default_wifi_sta();
    wifi_init_config_t config = WIFI_INIT_CONFIG_DEFAULT();
    config.nvs_enable = false;
    ESP_ERROR_CHECK(esp_wifi_init(&config));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, nullptr));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

void connect_network(size_t index)
{
    if (networks.empty()) return;
    const auto& saved = networks[index % networks.size()];
    wifi_config_t config{};
    memcpy(config.sta.ssid, saved.ssid.data(), std::min(saved.ssid.size(), sizeof(config.sta.ssid)));
    memcpy(config.sta.password, saved.password.data(), std::min(saved.password.size(), sizeof(config.sta.password)));
    config.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    const auto result = esp_wifi_connect();
    if (result != ESP_OK) ESP_LOGW(kTag, "Wi-Fi connect request: %s", esp_err_to_name(result));
}

void serial_config_task(void*)
{
    // USB-only fallback. One JSON line: {"cmd":"wifi","ssid":"...","password":"..."}.
    // Do not echo credentials; only the independent collector namespace is written.
    usb_serial_jtag_driver_config_t config = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    if (!usb_serial_jtag_is_driver_installed() && usb_serial_jtag_driver_install(&config) != ESP_OK) {
        ESP_LOGW(kTag, "USB serial configuration unavailable");
        vTaskDelete(nullptr);
    }
    std::string line;
    bool overflow = false;
    while (true) {
        char ch;
        if (usb_serial_jtag_read_bytes(&ch, 1, pdMS_TO_TICKS(100)) <= 0) continue;
        if (ch == '\r') continue;
        if (ch != '\n') {
            if (line.size() < 512 && !overflow) line += ch;
            else overflow = true;
            continue;
        }
        JsonDocument request;
        bool valid = !overflow && !deserializeJson(request, line);
        line.clear();
        overflow = false;
        if (!valid || std::string(request["cmd"] | "") != "wifi") {
            ESP_LOGW(kTag, "Expected USB JSON command: wifi");
            continue;
        }
        std::string ssid = request["ssid"] | "";
        std::string password = request["password"] | "";
        if (ssid.empty() || ssid.size() > 32 || password.size() > 64 ||
            (!password.empty() && password.size() < 8)) {
            ESP_LOGW(kTag, "Invalid Wi-Fi credential lengths");
            continue;
        }
        nvs_handle_t handle;
        esp_err_t result = nvs_open("collector", NVS_READWRITE, &handle);
        if (result == ESP_OK) {
            result = nvs_set_str(handle, "wifi_ssid", ssid.c_str());
            if (result == ESP_OK) result = nvs_set_str(handle, "wifi_password", password.c_str());
            if (result == ESP_OK) result = nvs_commit(handle);
            nvs_close(handle);
        }
        if (result == ESP_OK) {
            ESP_LOGI(kTag, "Collector Wi-Fi configuration saved; rebooting silently");
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_restart();
        } else ESP_LOGE(kTag, "Wi-Fi configuration save failed: %s", esp_err_to_name(result));
    }
}

void sensor_task(void*)
{
    auto bus = hal_bridge::board_get_i2c_bus();
    BMI270 imu(bus, 0x69);
    const bool imu_available = imu.begin(8); // Shoulder motion needs more headroom than the default +/-2g.
    si12t_handle_t head = nullptr;
    si12t_config_t config{.i2c_bus = bus, .dev_addr = SI12T_GND_ADDRESS};
    const bool head_available = si12t_init(&config, &head) == ESP_OK &&
        si12t_setup(head, SI12T_TYPE_LOW, SI12T_SENSITIVITY_LEVEL_3) == ESP_OK;
    Sensors sample;
    TickType_t wake = xTaskGetTickCount();
    while (true) {
        sample.imu_valid = imu_available && imu.update();
        if (sample.imu_valid) {
            sample.imu = imu.getData();
            sample.imu_us = esp_timer_get_time();
        }
        if (sample.seq % 5 == 0) {
            uint8_t value = 0;
            sample.head_valid = head_available && si12t_read_touch_result(head, &value) == ESP_OK;
            if (sample.head_valid) {
                si12t_parse_touch_result_to(value, sample.head);
                sample.head_us = esp_timer_get_time();
            }
        }
        sample.timestamp_us = esp_timer_get_time();
        ++sample.seq;
        {
            std::lock_guard<std::mutex> lock(sensor_mutex);
            sensors = sample;
            imu_history[sample.seq % kImuHistory] = sample;
        }
        xTaskDelayUntil(&wake, pdMS_TO_TICKS(20));
    }
}

void camera_task(void*)
{
    auto camera = hal_bridge::board_get_camera();
    while (true) {
        const int64_t start = esp_timer_get_time();
        auto frame = std::make_shared<Frame>();
        const uint32_t seq = ++frame_seq;
        if (camera && camera->StreamCaptures()) {
            frame->timestamp_us = esp_timer_get_time();
            frame->width = camera->GetFrameWidth();
            frame->height = camera->GetFrameHeight();
            frame->seq = seq;
            const int64_t encode_start = esp_timer_get_time();
            if (image_to_jpeg(const_cast<uint8_t*>(camera->GetFrameData()), camera->GetFrameSize(),
                              frame->width, frame->height, (v4l2_pix_fmt_t)camera->GetFrameFormat(),
                              55, &frame->bytes, &frame->length) && frame->bytes && frame->length) {
                camera_jpeg_us = esp_timer_get_time() - encode_start;
                camera_jpeg_bytes = frame->length;
                std::lock_guard<std::mutex> lock(frame_mutex);
                latest_frame = std::move(frame);
            } else ++camera_errors;
        } else ++camera_errors;
        camera_processing_us = esp_timer_get_time() - start;
        const int64_t remaining_ms = (kCameraPeriodUs - (esp_timer_get_time() - start)) / 1000;
        vTaskDelay(pdMS_TO_TICKS(std::max<int64_t>(remaining_ms, 10)));
    }
}

void audio_task(void*)
{
    auto codec = Board::GetInstance().GetAudioCodec();
    codec->EnableOutput(false);
    if (codec->input_sample_rate() != kSampleRate || codec->input_channels() != kChannels) {
        ESP_LOGE(kTag, "Unexpected audio format; refusing mislabeled PCM");
        vTaskDelete(nullptr);
    }
    audio_ring = static_cast<AudioBlock*>(heap_caps_calloc(kRingBlocks, sizeof(AudioBlock), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!audio_ring) {
        ESP_LOGE(kTag, "Audio ring allocation failed");
        vTaskDelete(nullptr);
    }
    std::vector<int16_t> input(kFrames * kChannels);
    codec->EnableInput(true);
    audio_ready = true;
    while (true) {
        const bool ok = codec->InputData(input);
        const int64_t completed = esp_timer_get_time();
        {
            std::lock_guard<std::mutex> lock(audio_mutex);
            const uint32_t seq = ++audio_sequence; // Failed reads remain observable as sequence gaps.
            auto& block = audio_ring[seq % kRingBlocks];
            block.seq = seq;
            block.timestamp_us = ok ? completed - (1000000LL * kFrames / kSampleRate) : 0;
            if (ok) memcpy(block.samples.data(), input.data(), kPcmBytes);
        }
        if (ok) last_audio_us = completed;
        else {
            ++audio_errors;
            vTaskDelay(pdMS_TO_TICKS(20));
        }
    }
}

bool authorize(httpd_req_t* request)
{
    char value[64];
    const std::string expected = "Bearer " + token;
    const size_t length = httpd_req_get_hdr_value_len(request, "Authorization");
    bool equal = length == expected.size() && length < sizeof(value) &&
        httpd_req_get_hdr_value_str(request, "Authorization", value, sizeof(value)) == ESP_OK;
    unsigned diff = 0;
    if (equal) for (size_t i = 0; i < length; ++i) diff |= value[i] ^ expected[i];
    if (equal && diff == 0) return true;
    httpd_resp_set_hdr(request, "WWW-Authenticate", "Bearer");
    httpd_resp_send_err(request, HTTPD_401_UNAUTHORIZED, "Pairing token required");
    return false;
}

esp_err_t send_json(httpd_req_t* request, const JsonDocument& document)
{
    std::string text;
    serializeJson(document, text);
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    return httpd_resp_send(request, text.data(), text.size());
}

esp_err_t unavailable(httpd_req_t* request, const char* message)
{
    httpd_resp_set_status(request, "503 Service Unavailable");
    httpd_resp_set_type(request, "text/plain");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    return httpd_resp_sendstr(request, message);
}

esp_err_t info_handler(httpd_req_t* request)
{
    if (!authorize(request)) return ESP_OK;
    JsonDocument doc;
    doc["protocol_version"] = 1;
    doc["device_id"] = device_id;
    doc["boot_id"] = boot_id;
    doc["firmware"] = kVersion;
    doc["upstream_commit"] = "f901cc2969189f3f0e96af1442c0f96e77e1e2c1";
    doc["sdk"] = esp_get_idf_version();
    doc["timestamp_clock"] = "esp_timer_monotonic_us_since_boot";
    doc["audio_port"] = 8081;
    auto capabilities = doc["capabilities"].to<JsonObject>();
    for (const char* name : {"camera", "audio", "imu", "head_touch", "touch", "power", "remote", "motion", "imu_batch"}) capabilities[name] = "supported";
    for (const char* name : {"light", "proximity", "nfc", "infrared", "micro_sd", "playback"}) capabilities[name] = "unsupported";
    doc["imu"]["target_hz"] = 50;
    doc["imu"]["sensor_odr_hz"] = 100;
    doc["imu"]["accel_range_g"] = 8;
    doc["imu"]["gyro_range_dps"] = 2000;
    doc["imu"]["includes_gravity"] = true;
    doc["imu"]["coordinate_frame"] = "BMI270 sensor axes on moving head; body mounting not calibrated";
    doc["remote"]["protocol"] = "M5Stack ESP-NOW component DATA / official 8-byte payload";
    doc["remote"]["receiver_id"] = 1;
    doc["remote"]["channel"] = "follows_connected_wifi";
    doc["remote"]["motion_default"] = "enabled; waits for fresh remote input";
    doc["remote"]["feedback_required_for_control"] = false;
    doc["remote"]["control_mode"] = "standalone";
    doc["remote"]["requires_observer"] = false;
    doc["remote"]["speed_limit"] = kRemoteSpeedLimit;
    auto audio = doc["audio"].to<JsonObject>();
    audio["sample_rate"] = kSampleRate;
    audio["channels"] = kChannels;
    audio["bits"] = 16;
    audio["encoding"] = "pcm_s16le";
    audio["frames_per_block"] = kFrames;
    audio["channel_roles"].to<JsonArray>().add("mic");
    audio["channel_roles"].as<JsonArray>().add("reference");
    audio["timestamp_semantics"] = "read_completion_minus_block_duration; DMA latency not calibrated";
    audio["channel_mapping_verified"] = false;
    auto camera = doc["camera"].to<JsonObject>();
    camera["width"] = 320;
    camera["height"] = 240;
    camera["format"] = "jpeg";
    camera["target_fps"] = kCameraTargetFps;
    camera["timestamp_semantics"] = "capture_completion";
    doc["capture_active"] = true;
    doc["transport"] = "http_lan_unencrypted";
    return send_json(request, doc);
}

bool query_counter(httpd_req_t* request, const char* key, uint32_t& value, bool& present)
{
    value = 0;
    present = false;
    if (httpd_req_get_url_query_len(request) == 0) return true;
    char query[128], field[24]{};
    if (httpd_req_get_url_query_str(request, query, sizeof(query)) != ESP_OK) return false;
    const auto result = httpd_query_key_value(query, key, field, sizeof(field));
    if (result == ESP_ERR_NOT_FOUND) return true;
    if (result != ESP_OK) return false;
    if (!field[0]) return false;
    uint64_t parsed = 0;
    for (char* ch = field; *ch; ++ch) { if (*ch < '0' || *ch > '9') return false; parsed = parsed * 10 + (*ch - '0'); if (parsed > UINT32_MAX - 1) return false; }
    value = parsed;
    present = true;
    return true;
}

esp_err_t telemetry_handler(httpd_req_t* request)
{
    if (!authorize(request)) return ESP_OK;
    uint32_t imu_after = 0, remote_after = 0;
    bool imu_requested = false, remote_requested = false;
    if (!query_counter(request, "imu_after", imu_after, imu_requested) ||
        !query_counter(request, "remote_after", remote_after, remote_requested)) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid sample cursor"); return ESP_OK;
    }
    Sensors sample;
    std::vector<Sensors> batch;
    if (imu_requested) batch.reserve(kImuHistory);
    uint32_t imu_lost = 0;
    {
        std::lock_guard<std::mutex> lock(sensor_mutex);
        sample = sensors;
        if (imu_requested) {
            const uint32_t first = sample.seq >= kImuHistory ? sample.seq - kImuHistory + 1 : 1;
            if (imu_after && imu_after + 1 < first) imu_lost = first - imu_after - 1;
            for (uint32_t i = std::max(first, imu_after + 1); i <= sample.seq; ++i) batch.push_back(imu_history[i % kImuHistory]);
        }
    }
    JsonDocument doc;
    doc["boot_id"] = boot_id;
    doc["seq"] = sample.seq;
    doc["timestamp_us"] = sample.timestamp_us;
    auto imu = doc["imu"].to<JsonObject>();
    imu["valid"] = sample.imu_valid;
    imu["timestamp_us"] = sample.imu_us;
    auto accel = imu["accel"].to<JsonArray>();
    accel.add(sample.imu.accel_x); accel.add(sample.imu.accel_y); accel.add(sample.imu.accel_z);
    auto gyro = imu["gyro"].to<JsonArray>();
    gyro.add(sample.imu.gyro_x); gyro.add(sample.imu.gyro_y); gyro.add(sample.imu.gyro_z);
    imu["accel_unit"] = "m/s^2";
    imu["gyro_unit"] = "deg/s";
    imu["includes_gravity"] = true;
    imu["accel_range_g"] = 8;
    imu["target_hz"] = 50;
    // Legacy clients request only the current snapshot. Re-serializing all 100
    // historical readings on every old-client poll needlessly delays JPEG work.
    JsonArray samples;
    if (imu_requested) {
        doc["imu_history_lost"] = imu_lost;
        samples = doc["imu_samples"].to<JsonArray>();
    }
    for (const auto& entry : batch) {
        auto item = samples.add<JsonObject>();
        item["seq"] = entry.seq;
        item["timestamp_us"] = entry.imu_valid ? entry.imu_us : entry.timestamp_us;
        item["valid"] = entry.imu_valid;
        if (entry.imu_valid) {
            auto a = item["accel"].to<JsonArray>(); a.add(entry.imu.accel_x); a.add(entry.imu.accel_y); a.add(entry.imu.accel_z);
            auto g = item["gyro"].to<JsonArray>(); g.add(entry.imu.gyro_x); g.add(entry.imu.gyro_y); g.add(entry.imu.gyro_z);
        }
    }
    remote_json(doc["remote"].to<JsonObject>(), remote_after, remote_requested);
    auto head = doc["head_touch"].to<JsonObject>();
    head["valid"] = sample.head_valid;
    head["timestamp_us"] = sample.head_us;
    auto values = head["values"].to<JsonArray>();
    for (auto value : sample.head) values.add(value);
    const auto point = hal_bridge::get_touch_point();
    auto touch = doc["touch"].to<JsonObject>();
    touch["valid"] = point.valid && esp_timer_get_time() - point.timestampUs < 1000000;
    touch["timestamp_us"] = point.timestampUs;
    touch["count"] = point.num;
    touch["x"] = point.x;
    touch["y"] = point.y;
    int level = 0;
    bool charging = false;
    int64_t power_us = 0;
    const bool power_valid = hal_bridge::board_get_power_snapshot(level, charging, power_us);
    auto power = doc["power"].to<JsonObject>();
    power["valid"] = power_valid;
    power["battery_percent"] = level;
    power["charging"] = charging;
    power["timestamp_us"] = power_us;
    auto health = doc["health"].to<JsonObject>();
    health["heap_free"] = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    health["psram_free"] = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    health["heap_min_free"] = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    health["camera_errors"] = camera_errors.load();
    health["camera_capture_seq"] = frame_seq.load();
    health["camera_processing_us"] = camera_processing_us.load();
    health["camera_jpeg_us"] = camera_jpeg_us.load();
    health["camera_jpeg_bytes"] = camera_jpeg_bytes.load();
    health["audio_errors"] = audio_errors.load();
    health["audio_client"] = audio_client.load();
    health["audio_ready"] = audio_ready.load();
    health["last_audio_timestamp_us"] = last_audio_us.load();
    health["audio_sent_blocks"] = audio_sent.load();
    health["audio_skipped_blocks"] = audio_skipped.load();
    health["audio_ring_capacity_blocks"] = kRingBlocks;
    {
        std::lock_guard<std::mutex> lock(audio_mutex);
        health["audio_capture_seq"] = audio_sequence;
    }
    health["speaker_enabled"] = false;
    health["speaker_hardware_reset_held"] = true;
    health["servo_commands_enabled"] = remote_enabled();
    wifi_ap_record_t ap{};
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) { health["wifi_rssi_dbm"] = ap.rssi; health["wifi_channel"] = ap.primary; }
    return send_json(request, doc);
}

esp_err_t remote_handler(httpd_req_t* request)
{
    if (!authorize(request)) return ESP_OK;
    if (request->content_len < 1 || request->content_len > 128) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected enabled boolean"); return ESP_OK;
    }
    char body[129]{};
    int total = 0;
    while (total < request->content_len) {
        const int count = httpd_req_recv(request, body + total, request->content_len - total);
        if (count <= 0) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Incomplete request"); return ESP_OK; }
        total += count;
    }
    JsonDocument doc;
    if (deserializeJson(doc, body) || !doc["enabled"].is<bool>()) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected enabled boolean"); return ESP_OK;
    }
    if (!remote_arm(doc["enabled"].as<bool>())) return unavailable(request, "Remote or servo feedback unavailable");
    JsonDocument result; result["accepted"] = true; result["lease_ms"] = 0;
    return send_json(request, result);
}

esp_err_t frame_handler(httpd_req_t* request)
{
    if (!authorize(request)) return ESP_OK;
    std::shared_ptr<Frame> frame;
    {
        std::lock_guard<std::mutex> lock(frame_mutex);
        frame = latest_frame;
    }
    if (!frame || esp_timer_get_time() - frame->timestamp_us > 3000000)
        return unavailable(request, "No recent frame");
    const auto seq = std::to_string(frame->seq), timestamp = std::to_string(frame->timestamp_us);
    const auto width = std::to_string(frame->width), height = std::to_string(frame->height);
    httpd_resp_set_type(request, "image/jpeg");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "X-Boot-Id", boot_id.c_str());
    httpd_resp_set_hdr(request, "X-Seq", seq.c_str());
    httpd_resp_set_hdr(request, "X-Timestamp-Us", timestamp.c_str());
    httpd_resp_set_hdr(request, "X-Width", width.c_str());
    httpd_resp_set_hdr(request, "X-Height", height.c_str());
    return httpd_resp_send(request, reinterpret_cast<const char*>(frame->bytes), frame->length);
}

void put_le(uint8_t* dest, uint64_t value, size_t bytes)
{
    for (size_t i = 0; i < bytes; ++i) dest[i] = static_cast<uint8_t>(value >> (8 * i));
}

void audio_stream_task(void* parameter)
{
    auto request = static_cast<httpd_req_t*>(parameter);
    httpd_resp_set_type(request, "application/octet-stream");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    httpd_resp_set_hdr(request, "X-Boot-Id", boot_id.c_str());
    uint32_t next;
    {
        std::lock_guard<std::mutex> lock(audio_mutex);
        next = audio_sequence + 1;
    }
    auto block = std::make_unique<AudioBlock>();
    std::array<uint8_t, 32 + kPcmBytes> wire{};
    memcpy(wire.data(), "SCA1", 4);
    bool send_failed = false;
    while (wifi_connected && audio_ready) {
        bool available = false;
        {
            std::lock_guard<std::mutex> lock(audio_mutex);
            if (audio_sequence >= next) {
                if (audio_sequence - next >= kRingBlocks) {
                    const uint32_t resumed = audio_sequence - kRingBlocks + 1;
                    audio_skipped += resumed - next;
                    next = resumed;
                }
                *block = audio_ring[next % kRingBlocks];
                ++next;
                available = block->timestamp_us != 0;
                if (!available) ++audio_skipped;
            }
        }
        if (!available) {
            if (esp_timer_get_time() - last_audio_us.load() > 3000000) break;
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }
        put_le(wire.data() + 4, block->seq, 4);
        put_le(wire.data() + 8, block->timestamp_us, 8);
        put_le(wire.data() + 16, kSampleRate, 4);
        put_le(wire.data() + 20, kFrames, 4);
        put_le(wire.data() + 24, kChannels, 2);
        put_le(wire.data() + 26, 16, 2);
        put_le(wire.data() + 28, kPcmBytes, 4);
        memcpy(wire.data() + 32, block->samples.data(), kPcmBytes); // ESP32-S3 is little endian.
        if (httpd_resp_send_chunk(request, reinterpret_cast<const char*>(wire.data()), wire.size()) != ESP_OK) {
            send_failed = true;
            break;
        }
        ++audio_sent;
    }
    // Do not spend another socket timeout on a connection which already failed.
    if (!send_failed) httpd_resp_send_chunk(request, nullptr, 0);
    httpd_req_async_handler_complete(request);
    audio_client = false;
    block.reset(); // FreeRTOS task deletion does not unwind C++ stack objects.
    vTaskDelete(nullptr);
}

esp_err_t audio_handler(httpd_req_t* request)
{
    if (!authorize(request)) return ESP_OK;
    if (!audio_ready || audio_client.exchange(true))
        return unavailable(request, "Audio unavailable or busy");
    httpd_req_t* async_request = nullptr;
    if (httpd_req_async_handler_begin(request, &async_request) != ESP_OK) {
        audio_client = false;
        return ESP_FAIL;
    }
    if (xTaskCreate(audio_stream_task, "audio_stream", 8192, async_request, 4, nullptr) != pdPASS) {
        unavailable(async_request, "Audio task unavailable");
        httpd_req_async_handler_complete(async_request);
        audio_client = false;
    }
    return ESP_OK;
}

void start_server(int port, int control_port, bool audio_only)
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = port;
    config.ctrl_port = control_port;
    config.stack_size = 8192;
    config.max_open_sockets = audio_only ? 2 : 3;
    config.max_uri_handlers = 4;
    config.max_resp_headers = 10;
    config.recv_wait_timeout = 3;
    config.send_wait_timeout = 3;
    config.lru_purge_enable = !audio_only;
    httpd_handle_t server = nullptr;
    ESP_ERROR_CHECK(httpd_start(&server, &config));
    auto register_get = [server](const char* uri, esp_err_t (*handler)(httpd_req_t*)) {
        httpd_uri_t endpoint{};
        endpoint.uri = uri;
        endpoint.method = HTTP_GET;
        endpoint.handler = handler;
        ESP_ERROR_CHECK(httpd_register_uri_handler(server, &endpoint));
    };
    if (audio_only) register_get("/v1/audio", audio_handler);
    else {
        register_get("/v1/info", info_handler);
        register_get("/v1/telemetry", telemetry_handler);
        register_get("/v1/frame", frame_handler);
        httpd_uri_t control{};
        control.uri = "/v1/remote"; control.method = HTTP_POST; control.handler = remote_handler;
        ESP_ERROR_CHECK(httpd_register_uri_handler(server, &control));
    }
}

void update_screen(const std::string& ip)
{
    auto display = static_cast<StackChanAvatarDisplay*>(Board::GetInstance().GetDisplay());
    display->LvglLock();
    if (!status_label) {
        auto screen = lv_screen_active();
        lv_obj_set_style_bg_color(screen, lv_color_hex(0x101922), 0);
        status_label = lv_label_create(screen);
        lv_obj_set_width(status_label, 306);
        lv_obj_align(status_label, LV_ALIGN_TOP_LEFT, 7, 8);
        lv_obj_set_style_text_color(status_label, lv_color_hex(0xE8F3F4), 0);
        lv_obj_set_style_text_font(status_label, &lv_font_montserrat_14, 0);
    }
    char text[512];
    uint8_t channel = 0; wifi_second_chan_t secondary; esp_wifi_get_channel(&channel, &secondary);
    snprintf(text, sizeof(text), "COLLECTOR 0.3 | MIC + CAM ON\nRemote CH %u / ID 1 | Motor %s\n\n%s:8080\nAudio :8081 | %s\n\nPAIRING TOKEN (local HTTP)\n%.16s\n%s\n\nCamera #%lu Audio %s\nWi-Fi fallback: USB JSON command",
             channel, remote_display_state(),
             ip.empty() ? "Wi-Fi waiting" : ip.c_str(), audio_client ? "STREAMING" : "READY",
             token.c_str(), token.c_str() + 16, static_cast<unsigned long>(frame_seq.load()),
             audio_ready ? "capturing" : "starting");
    lv_label_set_text(status_label, text);
    display->LvglUnlock();
}
} // namespace

void run()
{
    // Never erase NVS on errors: factory Wi-Fi/calibration remain intact.
    ESP_ERROR_CHECK(nvs_flash_init());
    hal_bridge::set_collector_mode(true);
    init_wifi(); // Radio is active before generating pairing entropy.
    load_identity();
    ESP_LOGI(kTag, "FIRMWARE=%s DEVICE_ID=%s BOOT_ID=%s", kVersion, device_id.c_str(), boot_id.c_str());
    ESP_LOGI(kTag, "LOCAL_PAIRING_TOKEN=%s", token.c_str());
    ESP_LOGI(kTag, "MIC/CAMERA CAPTURE ON; speaker held in reset; standalone remote receiver enabled");
    hal_bridge::xiaozhi_board_init(); // Hardware only; does not initialize Application.
    remote_init();
    update_screen({});
    ESP_ERROR_CHECK(xTaskCreate(sensor_task, "collector_sensors", 6144, nullptr, 3, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(audio_task, "collector_audio", 6144, nullptr, 7, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    // Keep JPEG work off Wi-Fi's core 0, still below sensor and audio priorities.
    ESP_ERROR_CHECK(xTaskCreatePinnedToCore(camera_task, "collector_camera", 8192, nullptr, 2, nullptr, 1) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(serial_config_task, "collector_serial", 4096, nullptr, 2, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    start_server(8080, 32768, false);
    start_server(8081, 32769, true);
    ESP_LOGI(kTag, "HTTP ready :8080 /v1/info /v1/telemetry /v1/frame; :8081 /v1/audio");
    ESP_LOGI(kTag, "USB fallback: one JSON line with cmd=wifi, ssid and password; saved only in collector NVS");
    size_t attempt = 0;
    int64_t last_attempt = -10000000;
    std::string previous_ip;
    while (true) {
        std::string ip;
        if (wifi_connected) {
            esp_netif_ip_info_t info{};
            if (esp_netif_get_ip_info(station, &info) == ESP_OK) {
                char buffer[16];
                snprintf(buffer, sizeof(buffer), IPSTR, IP2STR(&info.ip));
                ip = buffer;
            }
            if (ip != previous_ip) ESP_LOGI(kTag, "PAIRING_URL=http://%s:8080 BOOT_ID=%s", ip.c_str(), boot_id.c_str());
        } else if (esp_timer_get_time() - last_attempt >= 10000000) {
            if (attempt != 0) esp_wifi_disconnect();
            connect_network(attempt++ / 3);
            last_attempt = esp_timer_get_time();
        }
        previous_ip = ip;
        update_screen(ip);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
} // namespace collector
