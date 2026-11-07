/* SPDX-License-Identifier: MIT */
#include "collector.h"
#include "asr_unit.h"
#include "m12_unit.h"
#include "temperature.h"
#include "remote.h"
#include "screen.h"
#include "runtime_log.h"
#include "playback.h"
#include "voice_library.h"
#include "wifi_profiles.h"
#include "face_detection.h"
#include "face_pixels.h"
#include "remote_protocol.h"
#include "remote_actuation.h"
#include "connection_protocol.h"
#include <ArduinoJson.hpp>
#include <board.h>
#include <audio/audio_codec.h>
#include <ssid_manager.h>
#include <hal/board/hal_bridge.h>
#include <hal/board/stackchan_display.h>
#include <hal/drivers/bmi270/bmi270.h>
#include <hal/drivers/environment/ltr553.h>
#include <hal/drivers/environment/optical_runtime.h>
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
#include <esp_intr_alloc.h>
#include <esp_wifi.h>
#include <esp_log.h>
#include <nvs_flash.h>
#include <mbedtls/md.h>
#include <lwip/sockets.h>
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
constexpr int kMainHttpSessions = 3, kAudioHttpSessions = 2;
// IDF 5.5.4 httpd owns listen, control and message sockets in each instance.
// A free HTTP session slot cannot trigger LRU eviction when lwIP accept itself
// is out of sockets, so budget both servers and discovery together.
static_assert(CONFIG_LWIP_MAX_SOCKETS >= 2 * 3 + kMainHttpSessions + kAudioHttpSessions + 1,
              "Collector requires at least 12 sockets; use the shared collector build config");
// End-to-end target, below the GC0308's configured 20 fps sensor mode.
// Audio retains higher task priority; a slow camera never accumulates a queue.
constexpr uint32_t kCameraTargetFps = 10;
constexpr int64_t kCameraPeriodUs = 1000000 / kCameraTargetFps;
std::string token, device_id, boot_id;
std::atomic_bool wifi_connected{false}, audio_ready{false};
// Two independent bounded cursors share the existing capture ring: phone + Mac.
std::atomic<unsigned> audio_clients{0};
std::atomic<uint32_t> audio_errors{0}, camera_errors{0}, audio_sent{0}, audio_skipped{0};
std::atomic<uint32_t> frame_seq{0};
std::atomic<uint32_t> published_frames{0};
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
    std::string faces;
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
// Separate snapshots, not replicated in every 50 Hz IMU history entry.
environment::MagneticSample magnetic_sample;
environment::LightSample light_sample;
environment::ProximitySample proximity_sample;
struct OpticalInitDiagnostic {
    unsigned attempts = 0;
    int last_i2c_error = 0, last_i2c_register = -1;
    bool last_i2c_write = false;
};
OpticalInitDiagnostic optical_init;
environment::OpticalRuntimeDiagnostic optical_runtime;
// Built-in device registration has process lifetime, including a failed init.
// Removing it concurrently with unrelated board I2C traffic is not supported by
// IDF and can fail with "Wrong I2C status". Do not hot-remove this fixed device.
i2c_master_dev_handle_t optical_device = nullptr;
constexpr size_t kImuHistory = 100; // 2 seconds at 50 Hz; loss after this is reported.
std::array<Sensors, kImuHistory> imu_history;
std::vector<SsidItem> networks;
bool preferred_network_configured = false;
esp_netif_t* station = nullptr;
bool screen_initialized = false;
// The LVGL task publishes a coherent software state for HTTP telemetry.
// This confirms application of the display command, not panel luminance.
struct DisplayState {
    bool enabled = true;
    uint32_t applied_center_event_seq = 0;
    int64_t changed_us = 0;
};
std::mutex display_state_mutex;
DisplayState display_state;

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

std::string read_nvs_string(nvs_handle_t handle, const char* key, size_t limit = 256)
{
    size_t size = 0;
    if (nvs_get_str(handle, key, nullptr, &size) != ESP_OK || size == 0 || size > limit) return {};
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
        JsonDocument preferred;
        const auto encoded = read_nvs_string(handle, "wifi_primary", 768);
        if (!encoded.empty() && !deserializeJson(preferred, encoded))
            preferred_network_configured = append_wifi_profile(networks, SsidItem{
                preferred["ssid"] | "", preferred["password"] | ""});
        auto ssid = read_nvs_string(handle, "wifi_ssid");
        auto password = read_nvs_string(handle, "wifi_password");
        append_wifi_profile(networks, SsidItem{ssid, password});
        nvs_close(handle);
    }
    for (const auto& saved : SsidManager::GetInstance().GetSsidList()) append_wifi_profile(networks, saved);
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
    // USB-only fallback. priority=true adds a preferred profile while preserving
    // legacy/factory fallback networks. The two secrets commit as one NVS value.
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
        if (!wifi_credentials_valid(ssid, password)) {
            ESP_LOGW(kTag, "Invalid Wi-Fi credential lengths");
            continue;
        }
        nvs_handle_t handle;
        esp_err_t result = nvs_open("collector", NVS_READWRITE, &handle);
        if (result == ESP_OK) {
            if (request["priority"] | false) {
                JsonDocument profile;
                profile["ssid"] = ssid; profile["password"] = password;
                std::string encoded; serializeJson(profile, encoded);
                result = nvs_set_str(handle, "wifi_primary", encoded.c_str());
            } else {
                result = nvs_set_str(handle, "wifi_ssid", ssid.c_str());
                if (result == ESP_OK) result = nvs_set_str(handle, "wifi_password", password.c_str());
            }
            if (result == ESP_OK) result = nvs_commit(handle);
            nvs_close(handle);
        }
        if (result == ESP_OK) {
            ESP_LOGI(kTag, "Collector Wi-Fi configuration saved%s; rebooting silently", (request["priority"] | false) ? " (preferred; fallback retained)" : "");
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
    if (imu_available) imu.beginMagnetometer(); // Optional AUX initialization only; no extra runtime bus reads.
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
            magnetic_sample = imu.getMagnetometer();
            imu_history[sample.seq % kImuHistory] = sample;
        }
        xTaskDelayUntil(&wake, pdMS_TO_TICKS(20));
    }
}

void environment_task(void*)
{
    const auto bus = hal_bridge::board_get_i2c_bus();
    i2c_master_dev_handle_t device = nullptr;
    const i2c_device_config_t config{
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = 0x23,
        .scl_speed_hz = 400000,
    };
    if (!bus || i2c_master_bus_add_device(bus, &config, &device) != ESP_OK) {
        std::lock_guard<std::mutex> lock(sensor_mutex);
        environment::fail(light_sample, environment::Error::I2c);
        environment::fail(proximity_sample, environment::Error::I2c);
    } else {
        optical_device = device;
        struct OpticalBus {
            i2c_master_dev_handle_t device;
            bool initializing = true;
            OpticalInitDiagnostic init{};
            environment::OpticalRuntimeDiagnostic runtime{};
            bool result(esp_err_t error, uint8_t reg, bool write) {
                if (!initializing) return runtime.observe(error, reg, write, esp_timer_get_time());
                if (error != ESP_OK && initializing) {
                    init.last_i2c_error = error;
                    init.last_i2c_register = reg;
                    init.last_i2c_write = write;
                }
                return error == ESP_OK;
            }
        } bus_io{device};
        const environment::RegisterIo io{
            .context = &bus_io,
            .read = [](void* context, uint8_t reg, uint8_t* data, size_t length) {
                auto& bus = *static_cast<OpticalBus*>(context);
                return bus.result(i2c_master_transmit_receive(bus.device,
                    &reg, 1, data, length, environment::kOpticalTransactionTimeoutMs), reg, false);
            },
            .write = [](void* context, uint8_t reg, uint8_t value) {
                auto& bus = *static_cast<OpticalBus*>(context);
                const uint8_t data[]{reg, value};
                return bus.result(i2c_master_transmit(bus.device, data, sizeof(data),
                    environment::kOpticalTransactionTimeoutMs), reg, true);
            },
            .now_us = [](void*) -> int64_t { return esp_timer_get_time(); },
            .wait_ms = [](void*, uint32_t ms) { vTaskDelay(std::max<TickType_t>(1, pdMS_TO_TICKS(ms))); },
        };
        environment::Ltr553 optical(io);
        // Lite-On requires at least 100 ms after power-on. Other acquisition
        // tasks run while this task waits; never busy-wait on a sensor settling.
        vTaskDelay(std::max<TickType_t>(1, pdMS_TO_TICKS(100)));
        // A one-tick startup bus-lock timeout is not proof that this chip is
        // absent. The same bounded 50 ms API wait now applies at runtime;
        // failures remain visible, with no retry loop in the 50 Hz IMU task.
        const bool available = optical.initialize();
        bus_io.init.attempts = optical.initialization_attempts();
        bus_io.initializing = false;
        {
            std::lock_guard<std::mutex> lock(sensor_mutex);
            light_sample = optical.light(); proximity_sample = optical.proximity();
            optical_init = bus_io.init;
        }
        if (available) {
            TickType_t wake = xTaskGetTickCount();
            while (true) {
                environment::wait_for_optical_poll(wake, std::max<TickType_t>(1, pdMS_TO_TICKS(100)),
                    [](TickType_t* previous, TickType_t period) { return xTaskDelayUntil(previous, period) == pdTRUE; },
                    [](TickType_t ticks) { vTaskDelay(ticks); }, [] { return xTaskGetTickCount(); });
                optical.poll();
                std::lock_guard<std::mutex> lock(sensor_mutex);
                light_sample = optical.light(); proximity_sample = optical.proximity();
                optical_runtime = bus_io.runtime;
            }
        }
    }
    // No hot-plug support on the built-in chip. A missing/unknown part is
    // reported once in metadata instead of repeated retries/log flooding.
    vTaskDelete(nullptr);
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
            frame->faces = face_detect_frame(camera->GetFrameData(), camera->GetFrameSize(),
                frame->width, frame->height, camera->GetFrameFormat(), seq, frame->timestamp_us);
            const int64_t encode_start = esp_timer_get_time();
            if (image_to_jpeg(const_cast<uint8_t*>(camera->GetFrameData()), camera->GetFrameSize(),
                              frame->width, frame->height, (v4l2_pix_fmt_t)camera->GetFrameFormat(),
                              55, &frame->bytes, &frame->length) && frame->bytes && frame->length) {
                camera_jpeg_us = esp_timer_get_time() - encode_start;
                camera_jpeg_bytes = frame->length;
                std::lock_guard<std::mutex> lock(frame_mutex);
                latest_frame = std::move(frame);
                ++published_frames;
            } else ++camera_errors;
        } else ++camera_errors;
        camera_processing_us = esp_timer_get_time() - start;
        const int64_t period = face_detection_enabled() ? kFacePeriodUs : kCameraPeriodUs;
        const int64_t remaining_ms = (period - (esp_timer_get_time() - start)) / 1000;
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
    playback_set_audio_ready();
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

bool discovery_reply(const std::string& nonce, JsonDocument& doc)
{
    if (!discovery_nonce_valid(nonce)) return false;
    const auto message = discovery_proof_message(device_id, nonce);
    const auto* algorithm = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    unsigned char digest[32];
    if (!algorithm || mbedtls_md_hmac(algorithm,
            reinterpret_cast<const unsigned char*>(token.data()), token.size(),
            reinterpret_cast<const unsigned char*>(message.data()), message.size(), digest) != 0) return false;
    char proof[65];
    constexpr char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < sizeof(digest); ++i) {
        proof[i * 2] = hex[digest[i] >> 4];
        proof[i * 2 + 1] = hex[digest[i] & 15];
    }
    proof[64] = '\0';
    doc["protocol"] = 1;
    doc["device_id"] = device_id;
    doc["nonce"] = nonce;
    doc["proof"] = proof;
    doc["port"] = 8080;
    return true;
}

// Public proof of the existing binding, never a way to retrieve its credential.
esp_err_t discovery_handler(httpd_req_t* request)
{
    if (request->content_len != 0) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Discovery does not accept a body");
        return ESP_FAIL; // Close instead of purging an untrusted request body.
    }
    char query[39]{};
    if (httpd_req_get_url_query_len(request) != 38 ||
            httpd_req_get_url_query_str(request, query, sizeof(query)) != ESP_OK ||
            strncmp(query, "nonce=", 6) != 0 || !discovery_nonce_valid(query + 6)) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected nonce=32 lowercase hex characters");
        return ESP_OK;
    }
    JsonDocument doc;
    if (!discovery_reply(query + 6, doc)) return unavailable(request, "Discovery proof unavailable");
    return send_json(request, doc);
}

void discovery_task(void*)
{
    const int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    sockaddr_in local{};
    local.sin_family = AF_INET;
    local.sin_port = htons(8082);
    local.sin_addr.s_addr = htonl(INADDR_ANY);
    if (fd < 0 || bind(fd, reinterpret_cast<sockaddr*>(&local), sizeof(local)) != 0) {
        if (fd >= 0) close(fd);
        ESP_LOGW(kTag, "UDP discovery unavailable; saved address remains usable");
        vTaskDelete(nullptr);
        return;
    }
    int64_t last_reply_us = -200000;
    while (true) {
        char buffer[513];
        sockaddr_in peer{};
        socklen_t peer_size = sizeof(peer);
        const int count = recvfrom(fd, buffer, sizeof(buffer), 0,
                                  reinterpret_cast<sockaddr*>(&peer), &peer_size);
        if (count > 0 && count <= 512 && esp_timer_get_time() - last_reply_us >= 200000) {
            JsonDocument query;
            if (!deserializeJson(query, buffer, count) && query.is<JsonObject>() &&
                    query["protocol"].is<int>() && query["protocol"].as<int>() == 1 &&
                    query["device_id"].is<const char*>() && query["device_id"].as<std::string>() == device_id &&
                    query["nonce"].is<const char*>()) {
                JsonDocument reply;
                if (discovery_reply(query["nonce"].as<std::string>(), reply)) {
                    std::string body;
                    serializeJson(reply, body);
                    last_reply_us = esp_timer_get_time();
                    sendto(fd, body.data(), body.size(), 0, reinterpret_cast<sockaddr*>(&peer), peer_size);
                }
            }
        }
        // Prevent a datagram flood or repeated socket errors from starving other tasks.
        vTaskDelay(1);
    }
}

void environment_json(JsonDocument& doc, bool metadata)
{
    environment::MagneticSample magnetic;
    environment::LightSample light;
    environment::ProximitySample proximity;
    OpticalInitDiagnostic init;
    environment::OpticalRuntimeDiagnostic runtime;
    {
        std::lock_guard<std::mutex> lock(sensor_mutex);
        magnetic = magnetic_sample; light = light_sample; proximity = proximity_sample;
        init = optical_init;
        runtime = optical_runtime;
    }
    const int64_t now = esp_timer_get_time();
    environment::expire(magnetic, now); environment::expire(light, now); environment::expire(proximity, now);
    auto common = [metadata](JsonObject object, const environment::Sample& sample,
                             const char* sensor, const char* source, const char* unit, int chip_id) {
        object["available"] = sample.available;
        object["valid"] = sample.valid;
        object["seq"] = sample.seq;
        object["timestamp_us"] = sample.timestamp_us;
        object["error"] = environment::error_name(sample.error);
        object["unit"] = unit;
        if (metadata) {
            object["sensor"] = sensor; object["source"] = source;
            object["status"] = environment::error_name(sample.error);
            object["target_hz"] = 10; object["stale_after_ms"] = 1000;
            object["timestamp_semantics"] = "new_measurement_read_completion";
            if (chip_id < 0) object["chip_id"] = nullptr; else object["chip_id"] = chip_id;
        }
    };
    auto mag = doc["magnetometer"].to<JsonObject>();
    common(mag, magnetic, "BMM150", "BMI270_AUX_auto", "uT", magnetic.chip_id);
    mag["overflow"] = magnetic.overflow;
    if (magnetic.valid) { auto field = mag["field_ut"].to<JsonArray>(); for (float value : magnetic.field_ut) field.add(value); }
    else mag["field_ut"] = nullptr;
    if (magnetic.has_raw) {
        auto raw = mag["raw"].to<JsonArray>(); for (int16_t value : magnetic.raw) raw.add(value);
        mag["rhall"] = magnetic.rhall;
    } else { mag["raw"] = nullptr; mag["rhall"] = nullptr; }
    if (metadata) {
        mag["aux_poll_hz"] = 25;
        mag["coordinate_frame"] = "BMM150 sensor axes on moving head; body mounting not calibrated";
        mag["compensation"] = "Bosch factory trim and RHALL; no robot hard/soft iron calibration";
        mag["heading_supported"] = false;
    }
    auto als = doc["light"].to<JsonObject>();
    common(als, light, "LTR553", "LTR553_registers", "lx", light.chip_id);
    als["saturated"] = light.saturated;
    als["integration_ms"] = 100;
    if (light.valid) als["lux"] = light.lux; else als["lux"] = nullptr;
    if (light.has_raw) {
        als["raw_ch0"] = light.raw_ch0; als["raw_ch1"] = light.raw_ch1;
        if (light.gain) als["gain"] = light.gain; else als["gain"] = nullptr;
    } else { als["raw_ch0"] = nullptr; als["raw_ch1"] = nullptr; als["gain"] = nullptr; }
    // Shared ALS+PS bus statistics, copied once under sensor_mutex. Reading the
    // HTTP snapshot never creates another sensor transaction.
    auto runtime_io = als["runtime"].to<JsonObject>();
    runtime_io["scope"] = "LTR553_ALS_PS";
    runtime_io["transaction_timeout_ms"] = environment::kOpticalTransactionTimeoutMs;
    runtime_io["transactions"] = runtime.transactions;
    runtime_io["errors"] = runtime.errors;
    runtime_io["last_i2c_error"] = runtime.last_i2c_error;
    runtime_io["last_error_timestamp_us"] = runtime.last_error_timestamp_us;
    if (runtime.last_i2c_register < 0) {
        runtime_io["last_i2c_register"] = nullptr;
        runtime_io["last_i2c_operation"] = nullptr;
    } else {
        runtime_io["last_i2c_register"] = runtime.last_i2c_register;
        runtime_io["last_i2c_operation"] = runtime.last_i2c_write ? "write" : "read";
    }
    if (metadata) {
        if (light.manufacturer_id < 0) als["manufacturer_id"] = nullptr; else als["manufacturer_id"] = light.manufacturer_id;
        auto initialization = als["initialization"].to<JsonObject>();
        initialization["attempts"] = init.attempts;
        initialization["max_attempts"] = 3;
        initialization["transaction_timeout_ms"] = environment::kOpticalTransactionTimeoutMs;
        initialization["retry_backoff_ms"] = 100;
        initialization["last_i2c_error"] = init.last_i2c_error;
        if (init.last_i2c_register < 0) {
            initialization["last_i2c_register"] = nullptr;
            initialization["last_i2c_operation"] = nullptr;
        } else {
            initialization["last_i2c_register"] = init.last_i2c_register;
            initialization["last_i2c_operation"] = init.last_i2c_write ? "write" : "read";
        }
        als["conversion"] = "LTR553 piecewise lux coefficients (Zephyr ltr55x); no cover/window calibration";
        als["raw_unit"] = "count";
    }
    auto ps = doc["proximity"].to<JsonObject>();
    common(ps, proximity, "LTR553", "LTR553_registers", "count", proximity.chip_id);
    ps["saturated"] = proximity.saturated;
    if (proximity.has_raw) ps["raw"] = proximity.raw; else ps["raw"] = nullptr;
    if (metadata) {
        if (proximity.manufacturer_id < 0) ps["manufacturer_id"] = nullptr; else ps["manufacturer_id"] = proximity.manufacturer_id;
        ps["saturation_semantics"] = "hardware ambient_light_saturation_indicator";
        ps["distance_supported"] = false;
        ps["led_peak_ma"] = 20; ps["led_pulses"] = 4; ps["gain"] = 16;
    }
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
    doc["audio_max_clients"] = 2;
    doc["profile"] = "basic";
    doc["emotion"]["supported"] = false;
    doc["emotion"]["enabled"] = false;
    auto capabilities = doc["capabilities"].to<JsonObject>();
    for (const char* name : {"camera", "audio", "imu", "magnetometer", "light", "proximity", "head_touch", "touch", "power", "remote", "motion", "imu_batch", "playback", "display_toggle", "pitch_setting", "bound_discovery", "asr_unit", "m12_serial", "chip_temperature", "web_control", "m12_recognition", "voice_library"}) capabilities[name] = "supported";
    capabilities["face_detection"] = "unsupported";
    capabilities["remote_pitch"] = "unsupported";
    voice_library_json(doc["voice_library"].to<JsonObject>());
    capabilities["emotion_display"] = "unsupported";
    environment_json(doc, true);
    temperature_json(doc["temperature"].to<JsonObject>());
    remote_pitch_setting_json(doc["pitch_setting"].to<JsonObject>());
    face_detection_json(doc["face_detection"].to<JsonObject>());
    doc["pitch_setting"]["endpoint"] = "/v1/settings/pitch";
    doc["pitch_setting"]["min_deg"] = 5;
    doc["pitch_setting"]["max_deg"] = 85;
    doc["pitch_setting"]["step_deg"] = 0.1;
    doc["discovery"]["udp_port"] = 8082;
    doc["discovery"]["endpoint"] = "/v1/discovery";
    doc["discovery"]["requires_existing_binding"] = true;
    for (const char* name : {"nfc", "infrared", "micro_sd"}) capabilities[name] = "unsupported";
    doc["playback"]["endpoint"] = "/v1/playback";
    doc["playback"]["method"] = "POST";
    doc["playback"]["format"] = "s16le";
    doc["playback"]["sample_rate"] = 24000;
    doc["playback"]["channels"] = 1;
    doc["playback"]["bits"] = 16;
    doc["playback"]["max_bytes"] = 384000;
    doc["playback"]["max_seconds"] = 8;
    doc["playback"]["volume"] = kPlaybackVolume;
    doc["playback"]["automatic"] = true;
    doc["playback"]["remote_cues"] = "heart,reject";
    doc["playback"]["persistent"] = false;
    doc["playback"]["concurrent_jobs"] = 1;
    doc["imu"]["target_hz"] = 50;
    doc["imu"]["sensor_odr_hz"] = 100;
    doc["imu"]["accel_range_g"] = 8;
    doc["imu"]["gyro_range_dps"] = 2000;
    doc["imu"]["includes_gravity"] = true;
    doc["imu"]["coordinate_frame"] = "BMI270 sensor axes on moving head; body mounting not calibrated";
    doc["remote"]["protocol"] = "M5Stack ESP-NOW DATA / official 8-byte or center-v1 16-byte payload";
    doc["remote"]["receiver_id"] = 1;
    doc["remote"]["channel"] = "follows_connected_wifi";
    doc["remote"]["motion_default"] = "enabled; waits for fresh remote input";
    doc["remote"]["feedback_required_for_control"] = false;
    doc["remote"]["control_mode"] = "standalone";
    doc["remote"]["yaw_control_enabled"] = true;
    doc["remote"]["yaw_input_to_target_sign"] = -1;
    doc["remote"]["pitch_control_enabled"] = kRemotePitchControlEnabled;
    doc["remote"]["pitch_input_role"] = "disabled";
    doc["remote"]["reaction_duration_ms"] = kReactionDurationMs;
    doc["remote"]["center_input_role"] = "display_toggle";
    doc["remote"]["nod_trigger"] = "none";
    doc["remote"]["center_press_supported"] = true;
    asr_info_json(doc["asr"].to<JsonObject>());
    m12_info_json(doc["m12"].to<JsonObject>());
    doc["display"]["toggle_trigger"] = "joystick_center_v1";
    doc["display"]["default_enabled"] = true;
    doc["display"]["persistent"] = false;
    doc["display"]["touch_wakes"] = false;
    doc["display"]["requires_motor_enabled"] = false;
    doc["display"]["state_semantics"] = "software visibility and backlight command; not measured luminance";
    doc["remote"]["nod_pitch_range_deg"] = nullptr;
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
    camera["target_fps"] = face_detection_enabled() ? kFaceTargetFps : kCameraTargetFps;
    camera["base_target_fps"] = kCameraTargetFps;
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
    uint32_t imu_after = 0, remote_after = 0, servo_diag = 0;
    bool imu_requested = false, remote_requested = false, servo_diag_requested = false;
    if (!query_counter(request, "imu_after", imu_after, imu_requested) ||
        !query_counter(request, "remote_after", remote_after, remote_requested) ||
        !query_counter(request, "servo_diag", servo_diag, servo_diag_requested) || servo_diag > 1) {
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
    environment_json(doc, false);
    temperature_json(doc["temperature"].to<JsonObject>());
    remote_json(doc["remote"].to<JsonObject>(), remote_after, remote_requested, servo_diag_requested && servo_diag == 1);
    remote_pitch_setting_json(doc["pitch_setting"].to<JsonObject>());
    face_detection_json(doc["face_detection"].to<JsonObject>());
    {
        std::lock_guard<std::mutex> lock(display_state_mutex);
        auto display = doc["display"].to<JsonObject>();
        display["enabled"] = display_state.enabled;
        display["applied_center_event_seq"] = display_state.applied_center_event_seq;
        display["changed_us"] = display_state.changed_us;
        display["state_semantics"] = "software_visibility_and_backlight_command";
    }
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
    health["audio_client"] = audio_clients.load() > 0;
    health["audio_clients"] = audio_clients.load();
    health["audio_ready"] = audio_ready.load();
    health["last_audio_timestamp_us"] = last_audio_us.load();
    health["audio_sent_blocks"] = audio_sent.load();
    health["audio_skipped_blocks"] = audio_skipped.load();
    health["audio_ring_capacity_blocks"] = kRingBlocks;
    {
        std::lock_guard<std::mutex> lock(audio_mutex);
        health["audio_capture_seq"] = audio_sequence;
    }
    health["speaker_enabled"] = playback_output_enabled();
    const int speaker_reset = hal_bridge::board_speaker_reset_state();
    if (speaker_reset < 0) health["speaker_hardware_reset_held"] = nullptr;
    else health["speaker_hardware_reset_held"] = speaker_reset != 0;
    playback_json(doc["playback"].to<JsonObject>());
    asr_json(doc["asr"].to<JsonObject>());
    m12_json(doc["m12"].to<JsonObject>());
    health["servo_commands_enabled"] = remote_enabled();
    wifi_ap_record_t ap{};
    health["wifi_profile_count"] = networks.size();
    health["wifi_preferred_configured"] = preferred_network_configured;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        health["wifi_rssi_dbm"] = ap.rssi; health["wifi_channel"] = ap.primary;
        const std::string connected(reinterpret_cast<const char*>(ap.ssid), strnlen(reinterpret_cast<const char*>(ap.ssid), sizeof(ap.ssid)));
        health["wifi_on_preferred"] = preferred_network_configured && !networks.empty() && connected == networks.front().ssid;
    }
    return send_json(request, doc);
}

bool control_boot_matches(httpd_req_t* request, bool required = false)
{
    const size_t length = httpd_req_get_hdr_value_len(request, "X-Boot-Id");
    if (!length && !required) return true;
    char value[80]{};
    if (!length || length >= sizeof(value) || httpd_req_get_hdr_value_str(request, "X-Boot-Id", value, sizeof(value)) != ESP_OK || boot_id != value) {
        httpd_resp_set_status(request, "409 Conflict");
        httpd_resp_sendstr(request, "Robot restarted; refresh state before control"); return false;
    }
    return true;
}

esp_err_t web_control_handler(httpd_req_t* request)
{
    if (!authorize(request) || !control_boot_matches(request, true)) return ESP_FAIL;
    if (request->content_len < 1 || request->content_len > 128) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid control body"); return ESP_FAIL;
    }
    char body[129]{}; int total = 0;
    const int64_t deadline = esp_timer_get_time() + 3000000;
    while (total < request->content_len) {
        const int n = httpd_req_recv(request, body + total, request->content_len - total);
        if (n <= 0 || esp_timer_get_time() > deadline) {
            httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Incomplete control body"); return ESP_FAIL;
        }
        total += n;
    }
    JsonDocument doc, result;
    if (deserializeJson(doc, body, total) || !doc.is<JsonObject>() || doc.size() != 1) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected one control field"); return ESP_OK;
    }
    if (strcmp(request->uri, "/v1/m12/view") == 0) {
        if (!doc["enabled"].is<bool>()) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected enabled boolean"); return ESP_OK; }
        if (doc["enabled"].as<bool>() && !m12_snapshot().valid) return unavailable(request, "M12 serial unavailable");
        m12_set_view(doc["enabled"].as<bool>());
        result["request_seq"] = m12_view_request() >> 2;
    } else if (strcmp(request->uri, "/v1/m12/mode") == 0) {
        const char* mode = doc["mode"] | "";
        M12Function function = strcmp(mode, "face_detector") == 0 ? M12Function::detect :
            strcmp(mode, "face_recognition") == 0 ? M12Function::recognize :
            strcmp(mode, "standby") == 0 ? M12Function::standby : M12Function::none;
        if (function == M12Function::none) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid M12 mode"); return ESP_OK; }
        if (function != M12Function::standby && !m12_snapshot().valid) return unavailable(request, "M12 serial unavailable");
        m12_set_mode(function);
        result["request_seq"] = m12_view_request() >> 2;
    } else if (strcmp(request->uri, "/v1/voice/play") == 0) {
        if (!doc["clip_id"].is<int>() || doc["clip_id"].as<int>()<0 || doc["clip_id"].as<int>()>20) {
            httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected clip_id 0..20"); return ESP_OK;
        }
        uint32_t job=0; int selected=0;
        const auto error=playback_voice(doc["clip_id"].as<int>(),false,&job,&selected);
        if (error==ESP_ERR_INVALID_STATE) {
            httpd_resp_set_status(request,"409 Conflict");
            return httpd_resp_sendstr(request,"Playback busy or audio not ready");
        }
        if (error!=ESP_OK) return unavailable(request,"Voice playback could not start");
        result["job_id"]=job; result["clip_id"]=selected;
    } else {
        if (!doc["delta_deg"].is<int>() || (doc["delta_deg"].as<int>() != -5 && doc["delta_deg"].as<int>() != 5)) {
            httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected delta_deg -5 or 5"); return ESP_OK;
        }
        uint32_t seq = 0;
        if (remote_step_yaw(doc["delta_deg"].as<int>() * 10, &seq) != ESP_OK) {
            httpd_resp_set_status(request, "409 Conflict");
            return httpd_resp_sendstr(request, "Yaw unavailable: enable control, wait for valid feedback and settled motion");
        }
        result["request_seq"] = seq;
    }
    result["accepted"] = true;
    httpd_resp_set_status(request, "202 Accepted"); return send_json(request, result);
}

esp_err_t remote_handler(httpd_req_t* request)
{
    if (!authorize(request) || !control_boot_matches(request)) return ESP_FAIL;
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

esp_err_t pitch_handler(httpd_req_t* request)
{
    if (!authorize(request) || !control_boot_matches(request)) return ESP_FAIL;
    if (request->content_len < 1 || request->content_len > 128) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected angle_deg number from 5 to 85");
        return ESP_FAIL; // Do not leave oversized/unknown body bytes to HTTP purge.
    }
    char body[129]{};
    int total = 0;
    const int64_t deadline_us = esp_timer_get_time() + 3000000;
    while (total < request->content_len) {
        if (esp_timer_get_time() >= deadline_us) {
            httpd_resp_send_err(request, HTTPD_408_REQ_TIMEOUT, "Pitch request deadline exceeded");
            return ESP_FAIL;
        }
        const int count = httpd_req_recv(request, body + total, request->content_len - total);
        if (count <= 0) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Incomplete request"); return ESP_FAIL; }
        total += count;
    }
    if (esp_timer_get_time() >= deadline_us) {
        httpd_resp_send_err(request, HTTPD_408_REQ_TIMEOUT, "Pitch request deadline exceeded");
        return ESP_FAIL;
    }
    JsonDocument doc;
    int angle_tenths = 0;
    if (deserializeJson(doc, body, total) || !doc.is<JsonObject>() || doc.size() != 1 ||
            !doc["angle_deg"].is<double>() || !pitch_request_tenths(doc["angle_deg"].as<double>(), angle_tenths)) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected angle_deg from 5 to 85 in 0.1 degree steps");
        return ESP_OK;
    }
    uint32_t sequence = 0;
    const auto error = remote_set_pitch(angle_tenths, &sequence);
    if (error == ESP_ERR_INVALID_STATE) {
        httpd_resp_set_status(request, "409 Conflict");
        return httpd_resp_sendstr(request, "Enable motor control before applying pitch");
    }
    if (error != ESP_OK) return unavailable(request, "Pitch persistence unconfirmed; movement was not queued");
    JsonDocument result;
    result["accepted"] = true;
    result["persisted"] = true;
    result["request_seq"] = sequence;
    remote_pitch_setting_json(result["pitch_setting"].to<JsonObject>());
    httpd_resp_set_status(request, "202 Accepted");
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
    if (face_detection_enabled() && !frame->faces.empty())
        httpd_resp_set_hdr(request, "X-Face-Detection", frame->faces.c_str());
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
            // At 100 Hz, 5 ms rounds to zero ticks. A priority-4 busy loop
            // starves JPEG/display work while waiting for the next audio block.
            vTaskDelay(std::max<TickType_t>(1, pdMS_TO_TICKS(5)));
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
    --audio_clients;
    block.reset(); // FreeRTOS task deletion does not unwind C++ stack objects.
    vTaskDelete(nullptr);
}

esp_err_t audio_handler(httpd_req_t* request)
{
    if (!authorize(request)) return ESP_OK;
    if (!audio_ready) return unavailable(request, "Audio unavailable");
    // The HTTP dispatcher serializes admission; workers only decrement.
    if (audio_clients.fetch_add(1) >= 2) {
        --audio_clients;
        return unavailable(request, "Both audio subscriptions are busy");
    }
    httpd_req_t* async_request = nullptr;
    if (httpd_req_async_handler_begin(request, &async_request) != ESP_OK) {
        --audio_clients;
        return ESP_FAIL;
    }
    if (xTaskCreate(audio_stream_task, "audio_stream", 8192, async_request, 4, nullptr) != pdPASS) {
        unavailable(async_request, "Audio task unavailable");
        httpd_req_async_handler_complete(async_request);
        --audio_clients;
    }
    return ESP_OK;
}

void start_server(int port, int control_port, bool audio_only)
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = port;
    config.ctrl_port = control_port;
    config.stack_size = 8192;
    config.max_open_sockets = audio_only ? kAudioHttpSessions : kMainHttpSessions;
    config.max_uri_handlers = 12;
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
        register_get("/v1/discovery", discovery_handler);
        register_get("/v1/info", info_handler);
        register_get("/v1/telemetry", telemetry_handler);
        register_get("/v1/frame", frame_handler);
        httpd_uri_t control{};
        control.uri = "/v1/remote"; control.method = HTTP_POST; control.handler = remote_handler;
        ESP_ERROR_CHECK(httpd_register_uri_handler(server, &control));
        httpd_uri_t pitch{};
        pitch.uri = "/v1/settings/pitch"; pitch.method = HTTP_POST; pitch.handler = pitch_handler;
        ESP_ERROR_CHECK(httpd_register_uri_handler(server, &pitch));
        for (const char* uri : {"/v1/m12/view", "/v1/m12/mode", "/v1/motion/yaw-step"}) {
            httpd_uri_t endpoint{}; endpoint.uri = uri; endpoint.method = HTTP_POST; endpoint.handler = web_control_handler;
            ESP_ERROR_CHECK(httpd_register_uri_handler(server, &endpoint));
        }
        httpd_uri_t voice{};
        voice.uri="/v1/voice/play"; voice.method=HTTP_POST; voice.handler=web_control_handler;
        ESP_ERROR_CHECK(httpd_register_uri_handler(server,&voice));
        httpd_uri_t playback{};
        playback.uri = "/v1/playback"; playback.method = HTTP_POST;
        playback.handler = [](httpd_req_t* request) -> esp_err_t {
            if (!authorize(request)) return ESP_FAIL;
            if (!audio_ready) { unavailable(request, "Audio initialization incomplete"); return ESP_FAIL; }
            return playback_handle(request);
        };
        ESP_ERROR_CHECK(httpd_register_uri_handler(server, &playback));
    }
}

void update_screen(const std::string& ip)
{
    const int64_t now = esp_timer_get_time();
    const auto remote = remote_snapshot();
    Sensors sample;
    { std::lock_guard<std::mutex> lock(sensor_mutex); sample = sensors; }
    int64_t frame_time = 0;
    { std::lock_guard<std::mutex> lock(frame_mutex); if (latest_frame) frame_time = latest_frame->timestamp_us; }
    static uint32_t last_frame = 0;
    static int64_t last_fps_us = 0;
    static float fps = 0;
    // Capture sequence advances for failed attempts as well. The summary rate
    // counts only JPEG frames successfully made available to observers.
    const auto current_frame = published_frames.load();
    if (last_fps_us && now - last_fps_us >= 1000000) {
        fps = (current_frame - last_frame) * 1000000.0f / (now - last_fps_us);
        last_frame = current_frame; last_fps_us = now;
    } else if (!last_fps_us) { last_frame = current_frame; last_fps_us = now; }
    ScreenStatus status{};
    status.version = kVersion; status.ip = ip.c_str(); status.token = token.c_str();
    status.motor = remote_display_state();
    uint8_t channel = 0; wifi_second_chan_t secondary; esp_wifi_get_channel(&channel, &secondary);
    status.channel = channel; status.frame = current_frame;
    status.audio_ready = audio_ready.load(); status.audio_streaming = audio_clients.load() > 0;
    status.wifi_connected = wifi_connected.load() && !ip.empty();
    status.camera_working = frame_time > 0 && now - frame_time < 3000000;
    status.camera_fps = fps;
    status.remote_ready = remote.ready;
    status.remote_received = remote.rx_packets;
    status.remote_last_age_ms = remote.last_rx_us > 0 ? static_cast<int32_t>(std::min<int64_t>((now - remote.last_rx_us) / 1000, INT32_MAX)) : -1;
    status.remote_recent = status.remote_last_age_ms >= 0 && status.remote_last_age_ms < 3000;
    int64_t power_time = 0;
    status.power_valid = hal_bridge::board_get_power_snapshot(status.battery_percent, status.charging, power_time);
    status.imu_valid = sample.imu_valid && now - sample.imu_us < 200000;
    status.accel[0] = sample.imu.accel_x; status.accel[1] = sample.imu.accel_y; status.accel[2] = sample.imu.accel_z;
    status.gyro[0] = sample.imu.gyro_x; status.gyro[1] = sample.imu.gyro_y; status.gyro[2] = sample.imu.gyro_z;
    const bool fresh_feedback = remote.feedback_timestamp_us > 0 && now - remote.feedback_timestamp_us < 1000000;
    status.yaw_valid = fresh_feedback && remote.yaw_valid;
    status.pitch_valid = fresh_feedback && remote.pitch_valid;
    status.yaw_deg = remote.yaw_angle_deg; status.pitch_deg = remote.pitch_angle_deg;
    auto display = static_cast<StackChanAvatarDisplay*>(Board::GetInstance().GetDisplay());
    display->LvglLock();
    if (!screen_initialized) {
        screen_create(lv_screen_active());
        lv_timer_create([](lv_timer_t*) {
            const auto touch = hal_bridge::get_touch_point();
            const int64_t now = esp_timer_get_time();
            const auto remote = remote_snapshot();
            // This timer keeps running with the screen off. Display control
            // never depends on the observer, motor enable or servo feedback.
            screen_set_enabled(remote.display_requested, static_cast<uint32_t>(now / 1000));
            {
                std::lock_guard<std::mutex> lock(display_state_mutex);
                if (display_state.enabled != screen_enabled()) display_state.changed_us = now;
                display_state.enabled = screen_enabled();
                display_state.applied_center_event_seq = remote.center_event_seq;
            }
            screen_tick(static_cast<uint32_t>(now / 1000));
            if (touch.valid && now - touch.timestampUs < 100000)
                screen_touch(touch.num > 0, static_cast<uint32_t>(now / 1000),
                             touch.num > 0 ? touch.x : -1, touch.num > 0 ? touch.y : -1);
            else screen_cancel_touch();
            static uint32_t reaction_seen = 0;
            if (remote.reaction_seq != reaction_seen) {
                reaction_seen = remote.reaction_seq;
                if (now >= remote.reaction_timestamp_us && now - remote.reaction_timestamp_us < 500000 &&
                    remote.reaction != RemoteReaction::None) {
                    const auto reaction = remote.reaction == RemoteReaction::Heart ? ScreenReaction::Heart : ScreenReaction::Reject;
                    // A fresh input gets the full display interval even when
                    // this timer was briefly delayed; stale inputs still drop.
                    screen_show_reaction(reaction, static_cast<uint32_t>(now / 1000));
                    const auto enqueue_started = esp_timer_get_time();
                    if (audio_ready) playback_reaction(remote.reaction);
                    ESP_LOGI("CollectorScreen", "input seq=%lu age_us=%lu sound_enqueue_us=%lu",
                             static_cast<unsigned long>(remote.reaction_seq),
                             static_cast<unsigned long>(now - remote.reaction_timestamp_us),
                             static_cast<unsigned long>(esp_timer_get_time() - enqueue_started));
                }
            }
            // Unit ASR: "forward" shows the home heart, "backward" the reject X.
            // Display only: the unit already speaks its own reply, and a second
            // sound would also reach the microphone. "turn on" opens the M12 face
            // page and requests detection; "turn off" (or a tap on it) returns
            // home and requests standby. Other inputs never wake the M12.
            static uint32_t asr_seen = 0;
            static bool face_view = false;
            static uint32_t view_seen = 0;
            const uint32_t asr = asr_latest();
            const auto tick_ms = static_cast<uint32_t>(now / 1000);
            if (asr != asr_seen) {
                asr_seen = asr;
                const uint8_t id = asr & 0xFF;
                const auto spoken = asr_reaction(id);
                if (spoken != AsrReaction::none)
                    screen_show_reaction(spoken == AsrReaction::heart ? ScreenReaction::Heart : ScreenReaction::Reject, tick_ms);
                const int face_switch = asr_face_switch(id);
                if (face_switch != 0) {
                    m12_set_view(face_switch > 0);
                }
            }
            const uint32_t requested_view = m12_view_request();
            if (requested_view != view_seen) {
                view_seen = requested_view; face_view = m12_requested_view(requested_view);
                screen_show_faces(face_view, tick_ms);
            }
            if (face_view && !screen_showing_faces()) {
                face_view = false;
                m12_set_view(false);
            }
            if (face_view) {
                static uint32_t shown_seq = 0, status_ms = 0;
                const auto m12 = m12_snapshot();
                if (m12.frame_seq != shown_seq || tick_ms - status_ms >= 500) {
                    shown_seq = m12.frame_seq;
                    status_ms = tick_ms;
                    const bool answered = m12.requested_us > 0 && m12.frame_us > m12.requested_us;
                    const bool fresh = answered && now - m12.frame_us < 1500000;
                    ScreenFace boxes[kScreenMaxFaces];
                    size_t shown = 0;
                    for (size_t i = 0; fresh && i < m12.count && shown < kScreenMaxFaces; ++i) {
                        const auto b = m12_to_screen(m12.faces[i]);
                        boxes[shown].x = b.x; boxes[shown].y = b.y; boxes[shown].w = b.w; boxes[shown].h = b.h;
                        ++shown;
                    }
                    char status[64];
                    const auto waited_s = static_cast<unsigned long>(m12.requested_us > 0 ? (now - m12.requested_us) / 1000000 : 0);
                    if (!m12.valid) std::snprintf(status, sizeof(status), "M12 serial unavailable");
                    else if (!answered && m12.rx_bytes == 0 && waited_s >= 10)
                        std::snprintf(status, sizeof(status), "no data from M12 - check blue port");
                    else if (!answered) std::snprintf(status, sizeof(status), "starting M12... %lus", waited_s);
                    else if (!fresh) std::snprintf(status, sizeof(status), "M12 data stale");
                    else if (shown == 0) std::snprintf(status, sizeof(status), "no face");
                    else if (m12.frame_recognition) std::snprintf(status, sizeof(status), "%s", m12.identity.known ? m12.identity.name : "unknown face");
                    else std::snprintf(status, sizeof(status), "faces %u", static_cast<unsigned>(shown));
                    screen_update_faces(boxes, shown, status);
                }
            }
            static uint32_t last_log_ms = 0;
            const auto now_ms = static_cast<uint32_t>(now / 1000);
            if (screen_showing_log() && now_ms - last_log_ms >= 200) {
                last_log_ms = now_ms;
                RuntimeLogSnapshot snapshot;
                if (runtime_log_snapshot(snapshot)) {
                    char text[sizeof(snapshot.lines) + 16]{};
                    size_t used = 0;
                    for (size_t i = 0; i < snapshot.count; ++i) {
                        const int written = snprintf(text + used, sizeof(text) - used, "%s%s", i ? "\n" : "", snapshot.lines[i]);
                        if (written < 0 || static_cast<size_t>(written) >= sizeof(text) - used) break;
                        used += written;
                    }
                    screen_update_log(text, snapshot.sequence);
                }
            }
        }, 20, nullptr);
        screen_initialized = true;
    }
    screen_update(status);
    display->LvglUnlock();
}
} // namespace

void run()
{
    runtime_log_init();
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
    asr_init();
    m12_init();
    temperature_init();
    update_screen({});
    ESP_ERROR_CHECK(xTaskCreate(sensor_task, "collector_sensors", 6144, nullptr, 3, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(environment_task, "collector_environment", 4096, nullptr, 2, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(audio_task, "collector_audio", 6144, nullptr, 7, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    // Keep JPEG work off Wi-Fi's core 0, still below sensor and audio priorities.
    ESP_ERROR_CHECK(xTaskCreatePinnedToCore(camera_task, "collector_camera", 8192, nullptr, 2, nullptr, 1) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(serial_config_task, "collector_serial", 4096, nullptr, 2, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    start_server(8080, 32768, false);
    start_server(8081, 32769, true);
    ESP_ERROR_CHECK(xTaskCreate(discovery_task, "collector_discovery", 4096, nullptr, 1, nullptr) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_LOGI(kTag, "HTTP ready :8080 /v1/info /v1/telemetry /v1/frame; :8081 /v1/audio");
    ESP_LOGI(kTag, "USB fallback: one JSON line with cmd=wifi, ssid and password; saved only in collector NVS");
    size_t attempt = 0;
    int64_t last_attempt = -10000000;
    int64_t last_health_log = 0;
    std::string previous_ip;
    while (true) {
        std::string ip;
        if (wifi_connected) {
            attempt = 0; // Each reconnection starts again with the preferred profile.
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
        const int64_t now = esp_timer_get_time();
        // One interrupt allocation table per boot (USB log): core 0 must keep free
        // inputs for interrupts that are allocated late, e.g. Wi-Fi's AES on first connect.
        static bool interrupts_dumped = false;
        if (!interrupts_dumped && now >= 8000000) {
            interrupts_dumped = true;
            esp_intr_dump(stdout);
        }
        if (now - last_health_log >= 5000000) {
            last_health_log = now;
            const auto remote = remote_snapshot();
            ESP_LOGI(kTag, "CAM #%lu MIC %s RX #%lu", static_cast<unsigned long>(frame_seq.load()),
                     audio_clients.load() ? "LIVE" : (audio_ready ? "READY" : "WAIT"), static_cast<unsigned long>(remote.rx_packets));
        }
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}
} // namespace collector
