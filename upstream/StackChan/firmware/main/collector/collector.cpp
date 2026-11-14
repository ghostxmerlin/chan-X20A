/* SPDX-License-Identifier: MIT */
#include "collector.h"
#include "asr_unit.h"
#include "m12_unit.h"
#include "temperature.h"
#include "remote.h"
#include "screen.h"
#include "runtime_log.h"
#include "playback.h"
#include "hearing.h"
#include "vision_uplink.h"
#include "mouth_link.h"
#include "camera_frames.h"
#include "interaction.h"
#include "eyes/eyes.h"
#include "eyes/http.h"
#include "voice_library.h"
#include "voice_policy.h"
#include "wifi_profiles.h"
#include "face_detection.h"
#include "face_pixels.h"
#include "remote_protocol.h"
#include "remote_actuation.h"
#include "connection_protocol.h"
#include "web_control.h"
#include "timed_yaw_turn.h"
#include "mic_gain.h"
#include "mem_diag.h"
#include "psram_task.h"
#include "eye_mode.h"
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
#include "control_reply.h"
#include "serial_monitor.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <lvgl.h>
#include <algorithm>
#include <cmath>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <iterator>
#include <memory>
#include <mutex>
#include <new>
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
#ifdef CONFIG_COLLECTOR_NETWORK_MONITOR
constexpr size_t kRingBlocks = 64;
#else
constexpr size_t kRingBlocks = 25; // only the half-second level window; no network PCM consumers
#endif
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
// Body camera capture/JPEG/serving is off unless the saved setting enables it.
std::atomic_bool camera_enabled{false};
// Two independent bounded cursors share the existing capture ring: phone + Mac.
std::atomic<unsigned> audio_clients{0};
std::atomic<uint32_t> audio_errors{0}, camera_errors{0}, audio_sent{0}, audio_skipped{0};
std::atomic<uint32_t> frame_seq{0};
std::atomic<uint32_t> published_frames{0};
std::atomic<int64_t> last_audio_us{0};
// Mic digital gain (mic_gain.h): set from the web task, read once per block by audio_task.
std::atomic<int> mic_gain_db{kMicGainDefaultDb};
std::atomic<uint32_t> mic_limited_blocks{0};
std::atomic<float> mic_gain_applied_db{0.0f};
std::atomic_bool mic_gain_saved{false};
std::atomic<const char*> mic_gain_source{"default"};
std::atomic<uint32_t> camera_processing_us{0}, camera_jpeg_us{0}, camera_jpeg_bytes{0};
std::mutex audio_mutex, frame_mutex, sensor_mutex;

struct AudioBlock {
    uint32_t seq;
    int64_t timestamp_us;
    std::array<int16_t, kFrames * kChannels> samples;
};
AudioBlock* audio_ring = nullptr;
uint32_t audio_sequence = 0;
using Frame = CameraFrame;           // camera_frames.h: shared with the vision uplink
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
// Operator network list (collector NVS "wifi_profiles"), scan results and the
// on-device setup requests. Only the main loop touches the radio and NVS;
// the LVGL timer copies state to/from the screen under wifi_mutex.
std::mutex wifi_mutex;
WifiProfileList wifi_profiles;
bool wifi_scan_requested = false, wifi_scan_running = false, wifi_scan_fresh = false;
std::atomic_bool wifi_scan_done{false};
int64_t wifi_scan_us = 0;
std::vector<ScreenWifiEntry> wifi_scan_results;
bool wifi_join_pending = false, wifi_reconnect_requested = false;
std::string wifi_join_ssid, wifi_join_password;
std::string wifi_status_text, wifi_ip;
bool wifi_status_dirty = false;
void wifi_json(JsonObject target);
void control_info_json(JsonObject target);
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

void load_camera_setting()
{
    nvs_handle_t handle;
    uint8_t enabled = 0;
    if (nvs_open("collector", NVS_READONLY, &handle) == ESP_OK) {
        if (nvs_get_u8(handle, "camera_on", &enabled) != ESP_OK) enabled = 0;
        nvs_close(handle);
    }
    camera_enabled = enabled == 1;
    ESP_LOGI(kTag, "Body camera streaming %s (saved setting; default off)", camera_enabled ? "enabled" : "disabled");
}

esp_err_t save_camera_setting(bool enabled)
{
    nvs_handle_t handle;
    esp_err_t error = nvs_open("collector", NVS_READWRITE, &handle);
    if (error != ESP_OK) return error;
    error = nvs_set_u8(handle, "camera_on", enabled ? 1 : 0);
    if (error == ESP_OK) error = nvs_commit(handle);
    nvs_close(handle);
    return error;
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
    if (base == WIFI_EVENT && id == WIFI_EVENT_SCAN_DONE) wifi_scan_done = true;
}

// Profiles persist as one JSON array; the first boot without it migrates the
// earlier wifi_primary / wifi_ssid keys so an existing robot keeps its networks.
void save_wifi_profiles_locked()
{
    JsonDocument doc;
    auto list = doc.to<JsonArray>();
    for (const auto& profile : wifi_profiles.items()) {
        auto entry = list.add<JsonObject>();
        entry["ssid"] = profile.ssid; entry["password"] = profile.password;
    }
    std::string encoded; serializeJson(doc, encoded);
    nvs_handle_t handle;
    if (nvs_open("collector", NVS_READWRITE, &handle) != ESP_OK) { ESP_LOGE(kTag, "Wi-Fi profiles: NVS open failed"); return; }
    const auto result = nvs_set_str(handle, "wifi_profiles", encoded.c_str());
    if (result == ESP_OK) nvs_commit(handle);
    else ESP_LOGE(kTag, "Wi-Fi profiles save failed: %s", esp_err_to_name(result));
    nvs_close(handle);
}

void load_wifi_profiles_locked()
{
    nvs_handle_t handle;
    if (nvs_open("collector", NVS_READONLY, &handle) != ESP_OK) return;
    const auto encoded = read_nvs_string(handle, "wifi_profiles", 1024);
    bool migrate = encoded.empty();
    if (!migrate) {
        JsonDocument doc;
        if (!deserializeJson(doc, encoded) && doc.is<JsonArray>()) {
            for (JsonObject entry : doc.as<JsonArray>()) wifi_profiles.add(entry["ssid"] | "", entry["password"] | "", false);
        } else migrate = true;
    }
    if (migrate) {
        JsonDocument preferred;
        const auto primary = read_nvs_string(handle, "wifi_primary", 768);
        if (!primary.empty() && !deserializeJson(preferred, primary))
            wifi_profiles.add(preferred["ssid"] | "", preferred["password"] | "", true);
        const auto ssid = read_nvs_string(handle, "wifi_ssid");
        const auto password = read_nvs_string(handle, "wifi_password");
        if (!ssid.empty()) wifi_profiles.add(ssid, password, false);
    }
    nvs_close(handle);
    if (migrate && !wifi_profiles.items().empty()) save_wifi_profiles_locked();
    ESP_LOGI(kTag, "Wi-Fi profiles: %u saved%s", static_cast<unsigned>(wifi_profiles.items().size()), migrate ? " (migrated)" : "");
}

// Candidate order: operator profiles, then the factory list (only `remove` ever changes it, since 0.4.91).
void rebuild_networks_locked()
{
    networks.clear();
    for (const auto& profile : wifi_profiles.items()) append_wifi_profile(networks, SsidItem{profile.ssid, profile.password});
    for (const auto& saved : SsidManager::GetInstance().GetSsidList()) append_wifi_profile(networks, saved);
    preferred_network_configured = !wifi_profiles.items().empty();
}

void set_wifi_status(const std::string& text)
{
    if (wifi_status_text == text) return;
    wifi_status_text = text; wifi_status_dirty = true;
}

void init_wifi()
{
    // Read factory Wi-Fi credentials; only an explicit remove rewrites that namespace (0.4.91). Never calibration.
    {
        std::lock_guard<std::mutex> lock(wifi_mutex);
        load_wifi_profiles_locked();
        rebuild_networks_locked();
    }
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
    SsidItem saved;
    {
        std::lock_guard<std::mutex> lock(wifi_mutex);
        if (networks.empty()) return;
        saved = networks[index % networks.size()];
        set_wifi_status("connecting to " + saved.ssid + " ...");
    }
    wifi_config_t config{};
    memcpy(config.sta.ssid, saved.ssid.data(), std::min(saved.ssid.size(), sizeof(config.sta.ssid)));
    memcpy(config.sta.password, saved.password.data(), std::min(saved.password.size(), sizeof(config.sta.password)));
    config.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    const auto result = esp_wifi_connect();
    if (result != ESP_OK) ESP_LOGW(kTag, "Wi-Fi connect request: %s", esp_err_to_name(result));
}

[[maybe_unused]] void serial_config_task(void*)
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
        bool stored;
        {
            std::lock_guard<std::mutex> lock(wifi_mutex);
            stored = wifi_profiles.add(ssid, password, request["priority"] | true);
            if (stored) { save_wifi_profiles_locked(); rebuild_networks_locked(); wifi_reconnect_requested = true; }
        }
        if (stored) ESP_LOGI(kTag, "Collector Wi-Fi profile saved (%u total); reconnecting", static_cast<unsigned>(wifi_profiles.items().size()));
        else ESP_LOGE(kTag, "Wi-Fi profile rejected (limit %u or invalid)", static_cast<unsigned>(kWifiProfileLimit));
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
    vTaskDeleteWithCaps(nullptr);
}

void camera_task(void*)
{
    auto camera = hal_bridge::board_get_camera();
    while (true) {
        if (!camera_enabled && !serial_monitor_camera_requested()) {
            // Disabled by default: no capture, no JPEG work, nothing to serve.
            { std::lock_guard<std::mutex> lock(frame_mutex); latest_frame.reset(); }
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }
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
            {
                const uint32_t format = camera->GetFrameFormat();
                const FacePixelFormat pixels = format == V4L2_PIX_FMT_YUYV ? FacePixelFormat::Yuyv :
                    format == V4L2_PIX_FMT_RGB565 ? FacePixelFormat::Rgb565 :
                    format == V4L2_PIX_FMT_RGB24 ? FacePixelFormat::Rgb888 : FacePixelFormat::Unsupported;
                frame->thumb = frame_thumb(camera->GetFrameData(), camera->GetFrameSize(), frame->width, frame->height, pixels);
            }
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

// Saved mic gain, read in the creating (internal-stack) task: audio_task runs on a PSRAM stack.
void load_mic_gain_setting()
{
    nvs_handle_t nvs; uint8_t saved = 0;
    if (nvs_open("collector", NVS_READONLY, &nvs) == ESP_OK) {
        if (nvs_get_u8(nvs, "mic_gain", &saved) == ESP_OK && mic_gain_valid(saved)) {
            mic_gain_db = saved; mic_gain_source = "saved"; mic_gain_saved = true;
        }
        nvs_close(nvs);
    }
}

void audio_task(void*)
{
    auto codec = Board::GetInstance().GetAudioCodec();
    codec->EnableOutput(false);
    if (codec->input_sample_rate() != kSampleRate || codec->input_channels() != kChannels) {
        ESP_LOGE(kTag, "Unexpected audio format; refusing mislabeled PCM");
        vTaskDeleteWithCaps(nullptr);
    }
    audio_ring = static_cast<AudioBlock*>(heap_caps_calloc(kRingBlocks, sizeof(AudioBlock), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!audio_ring) {
        ESP_LOGE(kTag, "Audio ring allocation failed");
        vTaskDeleteWithCaps(nullptr);
    }
    std::vector<int16_t> input(kFrames * kChannels);
    MicGain gain; int gain_db = mic_gain_db.load(); gain.set_db(gain_db);
    codec->EnableInput(true);
    audio_ready = true;
    playback_set_audio_ready();
    while (true) {
        const bool ok = codec->InputData(input);
        const int64_t completed = esp_timer_get_time();
        if (ok) {
            if (const int want = mic_gain_db.load(); want != gain_db) { gain_db = want; gain.set_db(want); }
            if (gain.process(input.data(), kFrames, kChannels, 0)) ++mic_limited_blocks; // channel 0 = mic; 1 = speaker reference
            mic_gain_applied_db = gain.applied_db();
        }
        {
            std::lock_guard<std::mutex> lock(audio_mutex);
            const uint32_t seq = ++audio_sequence; // Failed reads remain observable as sequence gaps.
            auto& block = audio_ring[seq % kRingBlocks];
            block.seq = seq;
            block.timestamp_us = ok ? completed - (1000000LL * kFrames / kSampleRate) : 0;
            if (ok) memcpy(block.samples.data(), input.data(), kPcmBytes);
        }
        if (ok) {
            last_audio_us = completed;
            hearing::feed_audio(input.data(),kFrames,audio_sequence,completed-20000);
        }
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

using collector::send_json;
using collector::unavailable;

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

[[maybe_unused]] void discovery_task(void*)
{
    const int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    sockaddr_in local{};
    local.sin_family = AF_INET;
    local.sin_port = htons(8082);
    local.sin_addr.s_addr = htonl(INADDR_ANY);
    if (fd < 0 || bind(fd, reinterpret_cast<sockaddr*>(&local), sizeof(local)) != 0) {
        if (fd >= 0) close(fd);
        ESP_LOGW(kTag, "UDP discovery unavailable; saved address remains usable");
        vTaskDeleteWithCaps(nullptr);
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

void fill_info(JsonDocument& doc)
{
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
    capabilities["eyes"]="supported";
    capabilities["hearing"]="supported";
    capabilities["interaction_modes"]="supported";
    capabilities["face_tracking"]="supported";
    eyes::catalog_json(doc["eyes"].to<JsonObject>());
    hearing::json(doc["hearing"].to<JsonObject>());
    for (const char* name : {"camera", "audio", "imu", "magnetometer", "light", "proximity", "head_touch", "touch", "power", "remote", "motion", "imu_batch", "playback", "display_toggle", "pitch_setting", "bound_discovery", "asr_unit", "m12_serial", "chip_temperature", "web_control", "m12_recognition", "voice_library"}) capabilities[name] = "supported";
    capabilities["face_detection"] = "unsupported";
    capabilities["remote_pitch"] = "unsupported";
    voice_library_json(doc["voice_library"].to<JsonObject>());
    capabilities["emotion_display"] = "unsupported";
    environment_json(doc, true);
    temperature_json(doc["temperature"].to<JsonObject>());
    remote_pitch_setting_json(doc["pitch_setting"].to<JsonObject>());
    face_detection_json(doc["face_detection"].to<JsonObject>());
    doc["pitch_setting"]["endpoint"] = "/v1/control";
    doc["pitch_setting"]["target"] = "pitch";
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
    doc["playback"]["volume"] = playback_volume();
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
    camera["enabled"] = camera_enabled.load();
    camera["default_enabled"] = false;
    camera["endpoint"] = "/v1/control";
    camera["target"] = "camera";
    capabilities["camera_control"] = "supported";
    capabilities["monitor_streams"] = "supported";
    capabilities["yaw_turn"] = "supported";
    capabilities["yaw_step_45"] = "supported";
    capabilities["asr_yaw_words"] = "supported"; // up/down/left/right/turn left/turn right/front/back, see asr.yaw_words
    capabilities["yaw_step_diagnostics"] = "supported"; // target yaw_step: whole-degree steps, speed and path
    capabilities["servo_tuning"] = "supported"; // target servo: EEPROM P/D/I/punch/deadband/max torque, runtime goal profile, release policy
    capabilities["wifi_profiles"] = "supported"; // target wifi: saved networks, scan; telemetry wifi carries the state
    capabilities["mic_gain"] = "supported"; // 0.4.60: target mic_gain, digital gain + limiter on the mic channel
    capabilities["unified_control"] = "supported"; // 0.4.46: every JSON control/config goes through POST /v1/control {target,...}
    capabilities["modes"] = "robot"; // 0.4.92: hello owns the mode; target mode {input, value}; info/telemetry mode
    control_info_json(doc["control"].to<JsonObject>());
    mouth::mode_json(doc["mode"].to<JsonObject>());
    doc["assets"]["endpoint"] = "/v1/assets";
    doc["assets"]["selector"] = "X-Voice-Id, or X-Eyes-Style + X-Eyes-State";
    doc["capture_active"] = true;
    doc["transport"] = "usb_serial_jtag";
    serial_monitor_stats(doc["monitor"].to<JsonObject>());

}

esp_err_t info_handler(httpd_req_t* request) {
    if (!authorize(request)) return ESP_OK;
    JsonDocument doc; fill_info(doc); return send_json(request,doc);
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

void fill_telemetry(JsonDocument& doc,uint32_t imu_after=0,bool imu_requested=false,uint32_t remote_after=0,bool remote_requested=false,bool servo_diag=false,uint32_t hearing_after=0,bool hearing_requested=false)
{
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
    remote_json(doc["remote"].to<JsonObject>(), remote_after, remote_requested, servo_diag);
    wifi_json(doc["wifi"].to<JsonObject>());
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
    serial_monitor_stats(doc["monitor"].to<JsonObject>());
    health["heap_free"] = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    health["heap_largest_free"] = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    // Internal 8-bit heaps may include memory unsuitable for SPI/I2S DMA.
    // Report the stricter capability pool separately, never infer it from heap_free.
    constexpr uint32_t dma_caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT;
    health["dma_free"] = heap_caps_get_free_size(dma_caps);
    health["dma_largest_free"] = heap_caps_get_largest_free_block(dma_caps);
    health["reset_reason_code"] = static_cast<int>(esp_reset_reason());
    health["psram_free"] = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    mem_diag_cpu_json(health["cpu_top"].to<JsonArray>());
    health["alloc_failures"] = mem_diag_failed_allocs(); health["alloc_failures_internal"] = mem_diag_failed_internal();
    health["heap_min_free"] = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    health["camera_enabled"] = camera_enabled.load();
    health["camera_preview"] = serial_monitor_camera_requested();
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
        // Mic channel level over the last 25 blocks (0.5 s) of the capture ring: replaces the
        // monitor's raw PCM stream for the level indicator.
        double sum = 0; int peak = 0; size_t samples = 0;
        if (audio_ring) for (uint32_t i = 0; i < 25 && i < audio_sequence; ++i) {
            const auto& block = audio_ring[(audio_sequence - i) % kRingBlocks];
            if (block.seq != audio_sequence - i || !block.timestamp_us) continue;
            for (size_t f = 0; f < kFrames; ++f) {
                const int v = block.samples[f * kChannels];
                sum += double(v) * v; peak = std::max(peak, v < 0 ? -v : v); ++samples;
            }
        }
        if (samples) {
            health["mic_level_dbfs"] = std::round(200.0 * std::log10(std::max(std::sqrt(sum / samples), 1.0) / 32768.0)) / 10.0;
            health["mic_peak_dbfs"] = std::round(200.0 * std::log10(std::max(peak, 1) / 32768.0)) / 10.0;
        } else {
            health["mic_level_dbfs"] = nullptr; health["mic_peak_dbfs"] = nullptr;
        }
        health["mic_window_ms"] = 500;
        // Levels above are after the digital gain; ADC PGA is fixed at its 37.5 dB maximum.
        health["mic_gain_db"] = mic_gain_db.load();
        health["mic_gain_applied_db"] = std::round(mic_gain_applied_db.load() * 10.0f) / 10.0f;
        health["mic_gain_min_db"] = kMicGainMinDb; health["mic_gain_max_db"] = kMicGainMaxDb;
        health["mic_gain_source"] = mic_gain_source.load(); health["mic_gain_saved"] = mic_gain_saved.load();
        health["mic_limited_blocks"] = mic_limited_blocks.load();
    }
    health["speaker_enabled"] = playback_output_enabled();
    const int speaker_reset = hal_bridge::board_speaker_reset_state();
    if (speaker_reset < 0) health["speaker_hardware_reset_held"] = nullptr;
    else health["speaker_hardware_reset_held"] = speaker_reset != 0;
    playback_json(doc["playback"].to<JsonObject>());
    hearing::json(doc["hearing"].to<JsonObject>(), hearing_requested, hearing_after);
    eyes::status_json(doc["eyes"].to<JsonObject>());
    asr_json(doc["asr"].to<JsonObject>());
    m12_json(doc["m12"].to<JsonObject>());
    vision_uplink_json(doc["m12"]["uplink"].to<JsonObject>());
    mouth::json(doc["mouth"].to<JsonObject>());
    mouth::mode_json(doc["mode"].to<JsonObject>());   // hello modes step 1b: hello owns the mode
    interaction::json(doc["interaction"].to<JsonObject>());
    health["servo_commands_enabled"] = remote_enabled();
    wifi_ap_record_t ap{};
    health["wifi_profile_count"] = networks.size();
    health["wifi_preferred_configured"] = preferred_network_configured;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        health["wifi_rssi_dbm"] = ap.rssi; health["wifi_channel"] = ap.primary;
        const std::string connected(reinterpret_cast<const char*>(ap.ssid), strnlen(reinterpret_cast<const char*>(ap.ssid), sizeof(ap.ssid)));
        health["wifi_on_preferred"] = preferred_network_configured && !networks.empty() && connected == networks.front().ssid;
    }

}
esp_err_t telemetry_handler(httpd_req_t* request)
{
    if (!authorize(request)) return ESP_OK;
    uint32_t imu_after = 0, remote_after = 0, servo_diag = 0, hearing_after = 0;
    bool imu_requested = false, remote_requested = false, servo_diag_requested = false, hearing_requested = false;
    if (!query_counter(request, "imu_after", imu_after, imu_requested) ||
        !query_counter(request, "remote_after", remote_after, remote_requested) ||
        !query_counter(request, "hearing_after", hearing_after, hearing_requested) ||
        !query_counter(request, "servo_diag", servo_diag, servo_diag_requested) || servo_diag > 1) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid sample cursor"); return ESP_OK;
    }
    JsonDocument doc;
    fill_telemetry(doc,imu_after,imu_requested,remote_after,remote_requested,servo_diag_requested&&servo_diag==1,hearing_after,hearing_requested);
    return send_json(request,doc);
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

// All JSON configuration and control arrives through POST /v1/control as
// {"target": <name>, ...}. The dispatcher authenticates, requires the current
// X-Boot-Id, reads one bounded body and hands the target-free object to the
// handler named in kControlTargets; each handler keeps its own field checks.
using ControlHandler = esp_err_t (*)(ControlReply*, JsonObject, const char* target);
constexpr size_t kControlBodyMax = 768;

esp_err_t web_control(ControlReply* request, JsonObject doc, const char* target)
{
    JsonDocument result;
    const bool yaw_step = strcmp(target, "yaw_step") == 0;
    size_t yaw_fields = 1;
    if (yaw_step) { if (doc["speed"].is<int>()) ++yaw_fields; if (doc["path"].is<const char*>()) ++yaw_fields; }
    const bool slot_request = yaw_step && doc["slot"].is<int>() && !doc["delta_deg"].is<int>();
    if (doc.size() < 1 || doc.size() > (yaw_step ? yaw_fields : 1u)) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected one control field"); return ESP_OK;
    }
    if (strcmp(target, "m12_view") == 0) {
        if (!doc["enabled"].is<bool>()) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected enabled boolean"); return ESP_OK; }
        if (doc["enabled"].as<bool>() && !m12_snapshot().valid) return unavailable(request, "M12 serial unavailable");
        interaction::release_for_manual_m12();
        m12_set_view(doc["enabled"].as<bool>());
        result["request_seq"] = m12_view_request() >> 2;
    } else if (strcmp(target, "m12_mode") == 0) {
        const char* mode = doc["mode"] | "";
        // m12d: detection on/off only (cfg det); identification is a request, see m12_request.
        M12Function function = strcmp(mode, "face_detector") == 0 ? M12Function::detect :
            strcmp(mode, "standby") == 0 ? M12Function::standby : M12Function::none;
        if (function == M12Function::none) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid M12 mode"); return ESP_OK; }
        if (function != M12Function::standby && !m12_snapshot().valid) return unavailable(request, "M12 serial unavailable");
        interaction::release_for_manual_m12();
        m12_set_mode(function);
        result["request_seq"] = m12_view_request() >> 2;
    } else if (strcmp(target, "voice_play") == 0) {
        if (!doc["clip_id"].is<int>() || doc["clip_id"].as<int>()<0 || doc["clip_id"].as<int>()>kVoiceCount) {
            httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected clip_id 0..40"); return ESP_OK;
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
        const int delta_deg = doc["delta_deg"] | 0;
        if (!slot_request && (!doc["delta_deg"].is<int>() || delta_deg == 0 || (std::abs(delta_deg) > kWebYawMaxDeltaTenths / 10 && std::abs(delta_deg) != kWebYawStepTenths / 10))) {
            httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected delta_deg (+/-1..30 or +/-45) or slot (-2..2)"); return ESP_OK;
        }
        if (slot_request && (doc["slot"].as<int>() < -kWebYawSlots || doc["slot"].as<int>() > kWebYawSlots)) {
            httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected slot -2..2"); return ESP_OK;
        }
        bool remote_like = false;
        if (doc["path"].is<const char*>()) {
            const char* path = doc["path"];
            if (strcmp(path, "remote") == 0) remote_like = true;
            else if (strcmp(path, "web") != 0) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected path web or remote"); return ESP_OK; }
        }
        int speed = kWebYawDefaultSpeed;
        if (doc["speed"].is<int>()) {
            speed = doc["speed"].as<int>();
            if (speed < kWebYawMinSpeed || speed > kWebYawMaxSpeed) {
                httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected speed 50..1000"); return ESP_OK;
            }
        }
        uint32_t seq = 0;
        const auto error = slot_request ? remote_yaw_slot(doc["slot"].as<int>(), speed, &seq)
                                        : remote_step_yaw(delta_deg * 10, speed, remote_like, &seq);
        if (error != ESP_OK) {
            httpd_resp_set_status(request, "409 Conflict");
            return httpd_resp_sendstr(request, "Yaw unavailable: enable control, wait for valid settled feedback, stay within the ±90° range");
        }
        result["request_seq"] = seq;
        result["speed"] = speed;
        result["path"] = remote_like ? "remote_like" : "web";
        if (slot_request) result["slot"] = doc["slot"].as<int>();
    }
    result["accepted"] = true;
    httpd_resp_set_status(request, "202 Accepted"); return send_json(request, result);
}

esp_err_t yaw_turn_control(ControlReply* request, JsonObject doc, const char*)
{
    if (doc.size() != 1 || !doc["duration_ms"].is<int>() ||
        doc["duration_ms"].as<int>() < TimedYawTurn::kMinMs || doc["duration_ms"].as<int>() > TimedYawTurn::kMaxMs) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected duration_ms 100..2000"); return ESP_OK;
    }
    uint32_t sequence = 0;
    if (remote_turn_yaw(doc["duration_ms"].as<int>(), &sequence) != ESP_OK) {
        httpd_resp_set_status(request, "409 Conflict");
        return httpd_resp_sendstr(request, "Turn unavailable: enable control and wait for current motion or tuning");
    }
    JsonDocument result; result["accepted"] = true; result["request_seq"] = sequence;
    httpd_resp_set_status(request, "202 Accepted"); return send_json(request, result);
}

esp_err_t remote_control(ControlReply* request, JsonObject doc, const char*)
{
    if (doc.size() != 1 || !doc["enabled"].is<bool>()) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected enabled boolean"); return ESP_OK;
    }
    if (!remote_arm(doc["enabled"].as<bool>())) return unavailable(request, "Remote or servo feedback unavailable");
    JsonDocument result; result["accepted"] = true; result["lease_ms"] = 0;
    return send_json(request, result);
}

esp_err_t pitch_control(ControlReply* request, JsonObject doc, const char*)
{
    int angle_tenths = 0;
    if (doc.size() != 1 || !doc["angle_deg"].is<double>() || !pitch_request_tenths(doc["angle_deg"].as<double>(), angle_tenths)) {
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

void wifi_json(JsonObject target)
{
    std::lock_guard<std::mutex> lock(wifi_mutex);
    target["connected"] = wifi_connected.load();
    if (wifi_connected) {
        wifi_ap_record_t ap{};
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
            target["ssid"] = reinterpret_cast<const char*>(ap.ssid); target["rssi_dbm"] = ap.rssi; target["channel"] = ap.primary;
        }
        target["ip"] = wifi_ip;
    } else { target["ssid"] = nullptr; target["ip"] = nullptr; }
    target["status"] = wifi_status_text;
    target["profile_limit"] = static_cast<uint32_t>(kWifiProfileLimit);
    auto profiles = target["profiles"].to<JsonArray>();
    for (size_t i = 0; i < wifi_profiles.items().size(); ++i) {
        auto entry = profiles.add<JsonObject>();
        entry["ssid"] = wifi_profiles.items()[i].ssid; entry["preferred"] = i == 0;
    }
    target["factory_count"] = static_cast<uint32_t>(SsidManager::GetInstance().GetSsidList().size());
    auto factory = target["factory"].to<JsonArray>();
    for (const auto& item : SsidManager::GetInstance().GetSsidList()) factory.add(item.ssid);
    target["scanning"] = wifi_scan_running;
    target["scan_age_ms"] = wifi_scan_us ? (esp_timer_get_time() - wifi_scan_us) / 1000 : -1;
    auto scan = target["scan"].to<JsonArray>();
    for (const auto& e : wifi_scan_results) {
        auto entry = scan.add<JsonObject>();
        entry["ssid"] = e.ssid; entry["rssi_dbm"] = e.rssi; entry["secure"] = e.secure; entry["saved"] = e.saved;
    }
    target["semantics"] = "profiles are tried in order, then the factory list (remove works on both); passwords are never returned";
}


esp_err_t wifi_control(ControlReply* request, JsonObject doc, const char*)
{
    const char* op = doc["op"] | "";
    const std::string ssid = doc["ssid"] | "";
    const std::string password = doc["password"] | "";
    const char* error = nullptr;
    {
        std::lock_guard<std::mutex> lock(wifi_mutex);
        if (strcmp(op, "scan") == 0) wifi_scan_requested = true;
        else if (strcmp(op, "reconnect") == 0) wifi_reconnect_requested = true;
        else if (strcmp(op, "add") == 0) {
            if (!wifi_profiles.add(ssid, password, doc["preferred"] | true)) error = "Invalid SSID/password or profile limit reached";
            else { save_wifi_profiles_locked(); rebuild_networks_locked(); if (doc["connect"] | true) wifi_reconnect_requested = true; set_wifi_status("saved " + ssid); }
        } else if (strcmp(op, "remove") == 0) {
            // 0.4.91: an SSID that is not an operator profile may be a factory entry (owner asked to drop
            // Force-M, 2026-11-14). Removing it rewrites the factory "wifi" namespace; nothing else there changes.
            bool removed = false;
            if (wifi_profiles.remove(ssid)) { save_wifi_profiles_locked(); removed = true; }
            else {
                auto& factory = SsidManager::GetInstance();
                const auto& list = factory.GetSsidList();
                for (size_t i = 0; i < list.size(); ++i)
                    if (list[i].ssid == ssid) { factory.RemoveSsid(static_cast<int>(i)); removed = true; break; }
            }
            if (!removed) error = "Unknown profile";
            else {
                rebuild_networks_locked(); set_wifi_status("removed " + ssid);
                wifi_ap_record_t ap{};
                if (wifi_connected && esp_wifi_sta_get_ap_info(&ap) == ESP_OK && ssid == reinterpret_cast<const char*>(ap.ssid))
                    wifi_reconnect_requested = true;   // leave the removed network now
            }
        } else if (strcmp(op, "prefer") == 0) {
            if (!wifi_profiles.prefer(ssid)) error = "Unknown profile";
            else { save_wifi_profiles_locked(); rebuild_networks_locked(); if (doc["connect"] | false) wifi_reconnect_requested = true; }
        } else error = "Expected op scan, reconnect, add, remove or prefer";
    }
    if (error) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, error); return ESP_OK; }
    JsonDocument result;
    result["accepted"] = true; result["op"] = op;
    wifi_json(result["wifi"].to<JsonObject>());
    httpd_resp_set_status(request, "202 Accepted"); return send_json(request, result);
}

esp_err_t camera_control(ControlReply* request, JsonObject doc, const char*)
{
    if (doc.size() != 1 || !doc["enabled"].is<bool>()) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected enabled boolean"); return ESP_OK;
    }
    const bool enabled = doc["enabled"].as<bool>();
    if (save_camera_setting(enabled) != ESP_OK) return unavailable(request, "Camera setting not persisted; streaming unchanged");
    camera_enabled = enabled;
    JsonDocument result;
    result["accepted"] = true; result["enabled"] = enabled; result["persisted"] = true;
    return send_json(request, result);
}

esp_err_t servo_control(ControlReply* request, JsonObject doc, const char*)
{
    ServoTuneRequest tune;
    const char* axis = doc["axis"] | "";
    if (strcmp(axis, "yaw") == 0) tune.axis = 0;
    else if (strcmp(axis, "pitch") == 0) tune.axis = 1;
    else { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected axis yaw or pitch"); return ESP_OK; }
    const char* op = doc["op"] | "";
    using Op = ServoTuneRequest::Op;
    if (strcmp(op, "eeprom") == 0) {
        tune.op = Op::Eeprom;
        auto take = [&](const char* key, bool& has, int& value) { if (doc[key].is<int>()) { has = true; value = doc[key].as<int>(); } };
        take("p", tune.eeprom.has_p, tune.eeprom.p); take("d", tune.eeprom.has_d, tune.eeprom.d); take("i", tune.eeprom.has_i, tune.eeprom.i);
        take("punch", tune.eeprom.has_punch, tune.eeprom.punch); take("dead_cw", tune.eeprom.has_dead_cw, tune.eeprom.dead_cw);
        take("dead_ccw", tune.eeprom.has_dead_ccw, tune.eeprom.dead_ccw); take("max_torque", tune.eeprom.has_max_torque, tune.eeprom.max_torque);
    } else if (strcmp(op, "runtime") == 0) {
        tune.op = Op::Runtime;
        if (doc["goal_time_ms"].is<int>() || doc["goal_speed"].is<int>()) {
            tune.has_goal = true; tune.goal_time_ms = doc["goal_time_ms"] | 20; tune.goal_speed = doc["goal_speed"] | 0;
        }
        if (doc["spring_stiffness"].is<float>() || doc["spring_damping"].is<float>()) {
            tune.has_spring = true; tune.spring_stiffness = doc["spring_stiffness"] | 0.0f; tune.spring_damping = doc["spring_damping"] | 0.0f;
        }
        if (doc["hold_min_ms"].is<int>() || doc["hold_max_ms"].is<int>() || doc["tolerance_deg"].is<float>()) {
            tune.has_release = true;
            tune.hold_min_ms = doc["hold_min_ms"] | 500; tune.hold_max_ms = doc["hold_max_ms"] | 2000;
            tune.tolerance_tenths = static_cast<int>(std::lround((doc["tolerance_deg"] | 1.0f) * 10.0f));
        }
    } else if (strcmp(op, "refresh") == 0) tune.op = Op::Refresh;
    else if (strcmp(op, "restore") == 0) tune.op = Op::Restore;
    else { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected op eeprom, runtime, refresh or restore"); return ESP_OK; }
    if (!servo_tune_request_valid(tune)) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Servo tuning values out of range"); return ESP_OK; }
    uint32_t sequence = 0;
    const auto error = remote_servo_tune(tune, &sequence);
    if (error == ESP_ERR_INVALID_STATE) {
        httpd_resp_set_status(request, "409 Conflict");
        return httpd_resp_sendstr(request, "Servo tuning busy or motor not initialized");
    }
    if (error != ESP_OK) { httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Servo tuning rejected"); return ESP_OK; }
    JsonDocument result;
    result["accepted"] = true; result["request_seq"] = sequence; result["axis"] = axis; result["op"] = op;
    result["semantics"] = "queued_for_motor_task_when_axis_idle; result in telemetry remote.motor.<axis>.tuning";
    httpd_resp_set_status(request, "202 Accepted"); return send_json(request, result);
}

struct ControlTarget { const char* name; ControlHandler handle; };
// {"target":"mode","input":"forward|...|select|digit|...","value":...} (hello modes step 1b): the page's mode bar.
// Runs the robot's own rules (mouth_link.h) like a spoken word, source "page"; digits 0 / 9 go on to mind.
// 200 with the outcome; an input word the rules do not know answers code unknown_input. billing is mind's: 400.
esp_err_t mode_control(ControlReply* r, JsonObject body, const char*)
{
    const char* word = body["input"].is<const char*>() ? body["input"].as<const char*>() : nullptr;
    bool fields_ok = true;
    for (JsonPair p : body) fields_ok = fields_ok && (!std::strcmp(p.key().c_str(), "input") || !std::strcmp(p.key().c_str(), "value"));
    if (!word || !fields_ok || !std::strcmp(word, "billing")) {
        httpd_resp_send_err(r, HTTPD_400_BAD_REQUEST, "Expected input (a mode input word, not billing) and optional value"); return ESP_OK;
    }
    const auto input = modes::rules::input_from(word);
    const mouth::Applied a = mouth::mode_apply("page", input, modes::rules::value_from(input, body["value"]));
    JsonDocument result;
    result["accepted"] = true;
    result["code"] = a.to_mind ? (a.input_seq ? "sent_to_mind" : "offline") : modes::rules::code_id(a.out.code);
    result["toast"] = static_cast<const char*>(a.out.toast);
    result["changed"] = a.changed; result["reported"] = a.reported;
    if (a.input_seq) result["input_seq"] = a.input_seq;
    mouth::mode_json(result["mode"].to<JsonObject>());
    return send_json(r, result);
}

const ControlTarget kControlTargets[] = {
    {"yaw_turn", yaw_turn_control}, {"remote", remote_control}, {"pitch", pitch_control}, {"camera", camera_control},
    {"servo", servo_control}, {"wifi", wifi_control},
    {"m12_view", web_control}, {"m12_mode", web_control}, {"yaw_step", web_control}, {"voice_play", web_control},
    {"m12_request", +[](ControlReply* r, JsonObject body, const char*) -> esp_err_t {
        // {"target":"m12_request","op":"ping|ver|status|cfg|learn|who|forget|faces"[,"name":..][,"face":id]}
        // Asynchronous: 202 with the request id; the result appears in telemetry m12.commands.
        M12Op op;
        if (!m12_op_from_name(body["op"] | "", op) || body.size() > 3 ||
            (body["name"].is<const char*>() == false && !body["name"].isNull()) || (!body["face"].isNull() && !body["face"].is<int>()) ||
            (op == M12Op::cfg && body.size() != 1)) {
            httpd_resp_send_err(r, HTTPD_400_BAD_REQUEST, "Expected op ping|ver|status|cfg|learn|who|forget|faces, optional name / face"); return ESP_OK;
        }
        M12RequestArgs args;
        const std::string name = body["name"] | "";
        if (op == M12Op::learn || op == M12Op::forget) {
            if (!m12_valid_name(name.c_str())) { httpd_resp_send_err(r, HTTPD_400_BAD_REQUEST, "Name must be 1-32 bytes UTF-8 without control characters"); return ESP_OK; }
            args.name = name.c_str();
        }
        if (op == M12Op::learn || op == M12Op::who) args.face = body["face"] | -1;
        const int32_t id = m12_request(op, args);
        if (id < 0) {
            const char* reason = m12_request_error(id);
            if (id == -4) { httpd_resp_send_err(r, HTTPD_400_BAD_REQUEST, reason); return ESP_OK; }
            return id == -3 ? (httpd_resp_set_status(r, "409 Conflict"), httpd_resp_sendstr(r, reason)) : unavailable(r, reason);
        }
        JsonDocument result; result["accepted"] = true; result["op"] = m12_op_name(op); result["id"] = id;
        httpd_resp_set_status(r, "202 Accepted"); return send_json(r, result); }},
    {"mode", mode_control},
    {"interaction", +[](ControlReply* r, JsonObject body, const char*) -> esp_err_t { return interaction::handle(r, body); }},
    {"hearing", +[](ControlReply* r, JsonObject body, const char*) -> esp_err_t { return hearing::handle(r, body); }},
    {"eyes", +[](ControlReply* r, JsonObject body, const char*) -> esp_err_t { return eyes::command_handler(r, body); }},
    {"volume", +[](ControlReply* r, JsonObject body, const char*) -> esp_err_t {
        // {"target":"volume","value":10..100}; applies to the next playback and is saved.
        if (body.size() != 1 || !body["value"].is<int>() || playback_set_volume(body["value"].as<int>(), "web") != ESP_OK) {
            httpd_resp_send_err(r, HTTPD_400_BAD_REQUEST, "Volume must be an integer 10..100"); return ESP_OK;
        }
        JsonDocument result; result["accepted"] = true; result["volume"] = playback_volume();
        httpd_resp_set_status(r, "202 Accepted"); return send_json(r, result); }},
    {"mic_gain", +[](ControlReply* r, JsonObject body, const char*) -> esp_err_t {
        // {"target":"mic_gain","value":0..24} dB; applies from the next 20 ms block and is saved.
        if (body.size() != 1 || !body["value"].is<int>() || !mic_gain_valid(body["value"].as<int>())) {
            httpd_resp_send_err(r, HTTPD_400_BAD_REQUEST, "Mic gain must be an integer 0..24 dB"); return ESP_OK;
        }
        const int db = body["value"].as<int>();
        mic_gain_db = db; mic_gain_source = "web";
        nvs_handle_t nvs; esp_err_t err = nvs_open("collector", NVS_READWRITE, &nvs);
        if (err == ESP_OK) { err = nvs_set_u8(nvs, "mic_gain", static_cast<uint8_t>(db)); if (err == ESP_OK) err = nvs_commit(nvs); nvs_close(nvs); }
        mic_gain_saved = err == ESP_OK;
        ESP_LOGI(kTag, "mic gain %d dB from web%s", db, err == ESP_OK ? "" : " (not saved)");
        JsonDocument result; result["accepted"] = true; result["mic_gain_db"] = db; result["saved"] = err == ESP_OK;
        httpd_resp_set_status(r, "202 Accepted"); return send_json(r, result); }},
};

void dispatch_control(JsonObject body,ControlReply& reply){
    static std::mutex mutex;std::lock_guard<std::mutex> lock(mutex);
    if(!body["target"].is<const char*>()){httpd_resp_send_err(&reply,HTTPD_400_BAD_REQUEST,"target required");return;}
    const std::string target=body["target"].as<const char*>();body.remove("target");
    for(const auto& entry:kControlTargets)if(target==entry.name){entry.handle(&reply,body,entry.name);return;}
    httpd_resp_send_err(&reply,HTTPD_400_BAD_REQUEST,"unknown target");
}

void control_info_json(JsonObject target)
{
    target["endpoint"] = "/v1/control";
    target["method"] = "POST";
    target["body_max_bytes"] = static_cast<uint32_t>(kControlBodyMax);
    target["headers"] = "Authorization: Bearer <token>; X-Boot-Id: <current boot_id>";
    auto names = target["targets"].to<JsonArray>();
    for (const auto& entry : kControlTargets) names.add(entry.name);
}

esp_err_t control_handler(httpd_req_t* request)
{
    if (!authorize(request) || !control_boot_matches(request, true)) return ESP_FAIL;
    if (request->content_len < 1 || request->content_len > static_cast<int>(kControlBodyMax)) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected a JSON object of at most 768 bytes");
        return ESP_FAIL; // Close instead of purging an oversized untrusted body.
    }
    char body[kControlBodyMax + 1]{};
    int total = 0;
    const int64_t deadline = esp_timer_get_time() + 3000000;
    while (total < request->content_len) {
        const int n = httpd_req_recv(request, body + total, request->content_len - total);
        if (n <= 0 || esp_timer_get_time() > deadline) {
            httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Incomplete control body"); return ESP_FAIL;
        }
        total += n;
    }
    JsonDocument doc;
    if (deserializeJson(doc, body, total) || !doc.is<JsonObject>() || !doc["target"].is<const char*>()) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Expected a JSON object with a target string"); return ESP_OK;
    }
    ControlReply reply;dispatch_control(doc.as<JsonObject>(),reply);return reply.http(request);
}

esp_err_t frame_handler(httpd_req_t* request)
{
    if (!authorize(request)) return ESP_OK;
    if (!camera_enabled) return unavailable(request, "Body camera streaming disabled; POST /v1/control {\"target\":\"camera\",\"enabled\":true}");
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

struct Route { const char* uri; httpd_method_t method; esp_err_t (*handler)(httpd_req_t*); };
// The handler limit is sized from these tables, so adding a route can never
// again overflow it and abort boot (the first 0.4.45 image did at 20 handlers).
const Route kMainRoutes[] = {
    {"/v1/discovery", HTTP_GET, discovery_handler},
    {"/v1/info", HTTP_GET, info_handler},
    {"/v1/telemetry", HTTP_GET, telemetry_handler},
    {"/v1/frame", HTTP_GET, frame_handler},
    {"/v1/control", HTTP_POST, control_handler},
    {"/v1/assets", HTTP_POST, +[](httpd_req_t* r) -> esp_err_t {
        if (!authorize(r) || !control_boot_matches(r, true)) return ESP_FAIL;
        return eyes::install_handler(r);
    }},
    {"/v1/playback", HTTP_POST, +[](httpd_req_t* r) -> esp_err_t {
        if (!authorize(r)) return ESP_FAIL;
        if (!audio_ready) { unavailable(r, "Audio initialization incomplete"); return ESP_FAIL; }
        return playback_handle(r);
    }},
};
const Route kAudioRoutes[] = {{"/v1/audio", HTTP_GET, audio_handler}};

[[maybe_unused]] void start_server(int port, int control_port, bool audio_only)
{
    const Route* routes = audio_only ? kAudioRoutes : kMainRoutes;
    const size_t route_count = audio_only ? std::size(kAudioRoutes) : std::size(kMainRoutes);
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = port;
    config.ctrl_port = control_port;
    config.stack_size = 8192;
    config.max_open_sockets = audio_only ? kAudioHttpSessions : kMainHttpSessions;
    config.max_uri_handlers = static_cast<uint16_t>(route_count);
    config.max_resp_headers = 10;
    config.recv_wait_timeout = 3;
    config.send_wait_timeout = 3;
    config.lru_purge_enable = !audio_only;
    httpd_handle_t server = nullptr;
    ESP_ERROR_CHECK(httpd_start(&server, &config));
    for (size_t i = 0; i < route_count; ++i) {
        httpd_uri_t endpoint{};
        endpoint.uri = routes[i].uri;
        endpoint.method = routes[i].method;
        endpoint.handler = routes[i].handler;
        ESP_ERROR_CHECK(httpd_register_uri_handler(server, &endpoint));
    }
}

void update_screen(const std::string& ip, const std::string& ssid = {})
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
    status.version = kVersion; status.ip = ip.c_str(); status.ssid = ssid.c_str(); status.token = token.c_str();
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
    // Never take service mutexes under LVGL: SD/media paths can acquire the
    // display lock in the other direction. The renderer only consumes flags.
    const auto modes = interaction::indicators();
    const auto listening = hearing::listening_intent();
    status.function_flags = (modes.tracking ? ScreenFunctionTracking : 0) |
                            (listening.requested ? ScreenFunctionListening : 0) |
                            (playback_busy() ? ScreenFunctionSpeaking : 0) |
                            (modes.recognition ? ScreenFunctionRecognition : 0);
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
            {
                // On-device Wi-Fi page: hand requests to the main loop, copy results back.
                if (screen_wifi_take_scan_request()) { std::lock_guard<std::mutex> lock(wifi_mutex); wifi_scan_requested = true; }
                char typed_ssid[33], typed_password[kScreenWifiPasswordMax + 1];
                if (screen_wifi_take_credentials(typed_ssid, sizeof(typed_ssid), typed_password, sizeof(typed_password))) {
                    std::lock_guard<std::mutex> lock(wifi_mutex);
                    wifi_join_ssid = typed_ssid; wifi_join_password = typed_password; wifi_join_pending = true;
                }
                std::lock_guard<std::mutex> lock(wifi_mutex);
                if (wifi_scan_fresh) { screen_update_wifi_scan(wifi_scan_results.data(), wifi_scan_results.size(), wifi_scan_running); wifi_scan_fresh = false; }
                if (wifi_status_dirty) { screen_update_wifi_status(wifi_status_text.c_str()); wifi_status_dirty = false; }
            }
            if (touch.valid && now - touch.timestampUs < 100000)
                screen_touch(touch.num > 0, static_cast<uint32_t>(now / 1000),
                             touch.num > 0 ? touch.x : -1, touch.num > 0 ? touch.y : -1);
            else screen_cancel_touch();
            {
                // hello modes step 1b: home tap / double tap are the mode's (rules on the robot, mouth_link.h);
                // the corner badge and the toast follow the mode state.
                const auto gesture = screen_take_home_gesture();
                if (gesture != ScreenHomeGesture::None)
                    mouth::mode_apply("touch", gesture == ScreenHomeGesture::Tap ? modes::rules::Input::Tap : modes::rules::Input::DoubleTap);
                static uint32_t mode_seen = 0, toasts_seen = 0;
                static bool mode_drawn = false;
                // 0.4.93 chat dots: a small copy of the chat stage (PSRAM, made once); the dots are decided every
                // tick (timeouts, speaking).
                struct ChatDots { bool in_chat = false; uint32_t color = 0; chat::StageState state; chat::DotsView view; };
                static ChatDots* cd = nullptr;
                if (!cd) if (void* m = heap_caps_calloc(1, sizeof(ChatDots), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)) cd = new (m) ChatDots();
                const uint32_t version = mouth::mode_version();
                if (!mode_drawn || version != mode_seen) {
                    mouth::ModeView view;
                    mouth::mode_view(view);
                    mode_seen = version; mode_drawn = true;
                    screen_update_mode(view.look.label, view.look.color, view.online);
                    if (view.toasts != toasts_seen) { toasts_seen = view.toasts; screen_show_toast(view.toast, static_cast<uint32_t>(now / 1000)); }
                    if (cd) { cd->in_chat = view.state.mode == modes::rules::Mode::Chat; cd->color = view.look.color; cd->state = view.chat; }
                }
                if (cd) {
                    const auto dots = cd->view.tick(cd->in_chat, cd->state, cd->in_chat && mouth::speaking(), now);
                    screen_update_chat_dots(dots.visible, dots.lit, dots.grey, cd->color);
                }
            }
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
            // sound would also reach the microphone. M12 commands are consumed
            // once by the ASR task in UART order; this timer applies the requested
            // preview visibility and handles explicit touch exits.
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
            }
            const uint32_t requested_view = m12_view_request();
            if (requested_view != view_seen) {
                // Normal eye mode (user, 2026-11-10): turning vision on no longer opens the full-screen
                // face-box page; the home page stays and shows the mode's eyes (eye_mode.h).
                view_seen = requested_view; face_view = false;
                if (screen_showing_faces()) screen_show_faces(false, tick_ms);
            }
            if (face_view && !screen_showing_faces()) {
                face_view = false;
                interaction::release_for_manual_m12();
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
                    const char* named = nullptr;
                    for (size_t i = 0; fresh && i < m12.count && !named; ++i) if (m12.faces[i].name[0]) named = m12.faces[i].name;
                    if (!m12.valid) std::snprintf(status, sizeof(status), "M12 serial unavailable");
                    else if (!m12.online && m12.rx_bytes == 0)
                        std::snprintf(status, sizeof(status), "no data from M12 - check blue port");
                    else if (!m12.online) std::snprintf(status, sizeof(status), "M12 offline (no heartbeat)");
                    else if (m12.hb.tier == 3) std::snprintf(status, sizeof(status), "M12 too hot %.0fC - paused", m12.hb.t);
                    else if (!answered) std::snprintf(status, sizeof(status), "starting detection... %lus", waited_s);
                    else if (!fresh) std::snprintf(status, sizeof(status), "M12 data stale");
                    else if (shown == 0) std::snprintf(status, sizeof(status), "no face");
                    else if (named) std::snprintf(status, sizeof(status), "%s", named);
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

std::shared_ptr<const CameraFrame> camera_latest_frame()
{
    if (!camera_enabled && !serial_monitor_camera_requested()) return nullptr;
    std::shared_ptr<Frame> frame;
    { std::lock_guard<std::mutex> lock(frame_mutex); frame = latest_frame; }
    if (!frame || esp_timer_get_time() - frame->timestamp_us > 3000000) return nullptr;
    return frame;
}

bool camera_streaming() { return camera_enabled; }

void run()
{
    runtime_log_init();
    // Never erase NVS on errors: factory Wi-Fi/calibration remain intact.
    ESP_ERROR_CHECK(nvs_flash_init());
    hal_bridge::set_collector_mode(true);
    mem_diag_start(); // USB MEM/TASKS/ALLOCFAIL lines for the network-stack wedge
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
    eyes::start();
#ifdef CONFIG_COLLECTOR_NETWORK_MONITOR
    eyes::start_asset_installer();
#endif
    playback_init();
    hearing::init(device_id,boot_id);
    vision_uplink_init(device_id, boot_id);
    mouth::init(device_id, boot_id);
    update_screen({});
    ESP_ERROR_CHECK(create_psram_task(sensor_task, "collector_sensors", 6144, nullptr, 3) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(create_psram_task(environment_task, "collector_environment", 4096, nullptr, 2) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    load_mic_gain_setting();
    ESP_ERROR_CHECK(create_psram_task(audio_task, "collector_audio", 6144, nullptr, 7) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    load_camera_setting();
    // Keep JPEG work off Wi-Fi's core 0, still below sensor and audio priorities.
    ESP_ERROR_CHECK(create_psram_task(camera_task, "collector_camera", 8192, nullptr, 2, 1) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    serial_monitor_start(device_id,boot_id,token,MonitorHooks{
        fill_info,
        +[](JsonDocument& d,bool diag){fill_telemetry(d,0,false,0,false,diag);},
        +[](JsonDocument& d,uint32_t asr_after,uint32_t hearing_after){
            asr_json(d["asr"].to<JsonObject>(),asr_after);
            hearing::events_json(d["hearing"].to<JsonObject>(),hearing_after);
        },
        dispatch_control,discovery_reply});
#ifdef CONFIG_COLLECTOR_NETWORK_MONITOR
    start_server(8080, 32768, false);
    start_server(8081, 32769, true);
    ESP_ERROR_CHECK(create_psram_task(discovery_task, "collector_discovery", 4096, nullptr, 1) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_LOGI(kTag,"Network monitor enabled; USB monitor available");
#else
    ESP_LOGI(kTag,"USB monitor ready; network monitor disabled");
#endif
    size_t attempt = 0;
    int64_t last_attempt = -10000000;
    int64_t last_health_log = 0;
    std::string previous_ip;
    while (true) {
        std::string ip, ssid;
        if (wifi_connected) {
            attempt = 0; // Each reconnection starts again with the preferred profile.
            esp_netif_ip_info_t info{};
            if (esp_netif_get_ip_info(station, &info) == ESP_OK) {
                char buffer[16];
                snprintf(buffer, sizeof(buffer), IPSTR, IP2STR(&info.ip));
                ip = buffer;
            }
            wifi_ap_record_t ap{};
            if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK)
                ssid.assign(reinterpret_cast<const char*>(ap.ssid), strnlen(reinterpret_cast<const char*>(ap.ssid), sizeof(ap.ssid)));
#ifdef CONFIG_COLLECTOR_NETWORK_MONITOR
            if (ip != previous_ip) ESP_LOGI(kTag, "PAIRING_URL=http://%s:8080 BOOT_ID=%s SSID=%s", ip.c_str(), boot_id.c_str(), ssid.c_str());
#endif
        } else if (esp_timer_get_time() - last_attempt >= 10000000) {
            if (attempt != 0) esp_wifi_disconnect();
            connect_network(attempt++ / 3);
            last_attempt = esp_timer_get_time();
        }
        {
            bool reconnect = false, join = false, start_scan = false;
            std::string join_ssid, join_password;
            {
                std::lock_guard<std::mutex> lock(wifi_mutex);
                wifi_ip = ip;
                if (wifi_connected) set_wifi_status("connected: " + ssid + "  " + ip);
                if (wifi_join_pending) { join = true; join_ssid = wifi_join_ssid; join_password = wifi_join_password; wifi_join_pending = false; }
                if (wifi_reconnect_requested) { reconnect = true; wifi_reconnect_requested = false; }
                start_scan = wifi_scan_requested && !wifi_scan_running;
            }
            if (join) {
                std::lock_guard<std::mutex> lock(wifi_mutex);
                if (wifi_profiles.add(join_ssid, join_password, true)) {
                    save_wifi_profiles_locked(); rebuild_networks_locked(); reconnect = true;
                    set_wifi_status("saved " + join_ssid + "; connecting ...");
                } else set_wifi_status("rejected: bad password or list full");
            }
            if (reconnect) { esp_wifi_disconnect(); wifi_connected = false; attempt = 0; last_attempt = -10000000; }
            if (start_scan) {
                wifi_scan_config_t config{};
                config.scan_type = WIFI_SCAN_TYPE_ACTIVE;
                config.scan_time.active.min = 100; config.scan_time.active.max = 300;
                const auto result = esp_wifi_scan_start(&config, false);
                std::lock_guard<std::mutex> lock(wifi_mutex);
                if (result == ESP_OK) { wifi_scan_requested = false; wifi_scan_running = true; wifi_scan_done = false; }
                else if (result != ESP_ERR_WIFI_STATE) { wifi_scan_requested = false; set_wifi_status(std::string("scan failed: ") + esp_err_to_name(result)); }
            }
            if (wifi_scan_done) {
                wifi_scan_done = false;
                static wifi_ap_record_t records[16];
                uint16_t count = 16;
                if (esp_wifi_scan_get_ap_records(&count, records) != ESP_OK) count = 0;
                std::lock_guard<std::mutex> lock(wifi_mutex);
                wifi_scan_results.clear();
                for (uint16_t i = 0; i < count; ++i) {
                    const char* name = reinterpret_cast<const char*>(records[i].ssid);
                    if (!name[0]) continue;
                    bool duplicate = false;
                    for (auto& e : wifi_scan_results) if (std::strncmp(e.ssid, name, sizeof(e.ssid)) == 0) { duplicate = true; e.rssi = std::max<int>(e.rssi, records[i].rssi); break; }
                    if (duplicate) continue;
                    ScreenWifiEntry entry;
                    std::snprintf(entry.ssid, sizeof(entry.ssid), "%s", name);
                    entry.rssi = records[i].rssi;
                    entry.secure = records[i].authmode != WIFI_AUTH_OPEN;
                    entry.saved = std::any_of(networks.begin(), networks.end(), [&](const SsidItem& n) { return n.ssid == name; });
                    wifi_scan_results.push_back(entry);
                }
                std::sort(wifi_scan_results.begin(), wifi_scan_results.end(), [](const ScreenWifiEntry& a, const ScreenWifiEntry& b) { return a.rssi > b.rssi; });
                if (wifi_scan_results.size() > kScreenWifiEntries) wifi_scan_results.resize(kScreenWifiEntries);
                wifi_scan_running = false; wifi_scan_fresh = true; wifi_scan_us = esp_timer_get_time();
            }
        }
        previous_ip = ip;
        interaction::tick(esp_timer_get_time());
        {
            // Eyes per mode (eye_mode.h, hello modes step 1b): talk = the laughing man always (vision on or
            // off); chat v05, crowd v14 (lively pool), companion v18; the page_* modes keep the laughing man
            // until their pages come. Settings closes itself after 5 minutes (mode_tick).
            mouth::mode_tick();
            static EyeModeController eye_mode;
            static bool eye_mode_ready = false;
            const int64_t t = esp_timer_get_time();
            if (!eye_mode_ready) { eyes::set_voice_auto_enable(false); eye_mode_ready = true; }
            mouth::ModeView mode;
            mouth::mode_view(mode);
            EyeLook want;
            switch (mode.look.eye) {
            case modes::Eye::V05: want.enabled = true; want.style = eyes::Style::V05; break;
            case modes::Eye::V14: want.enabled = true; want.style = eyes::Style::V14; break;
            case modes::Eye::V18: want.enabled = true; want.style = eyes::Style::V18; break;
            default: break;      // laughing_man, page_subtitle / page_settings / page_idle
            }
            want.pool = mode.look.pool == modes::Pool::Lively ? kLivelyNaturalPool : kNormalNaturalPool;
            if (eye_mode.due(want, t)) {
                eyes::set_natural_pool(want.pool);
                eyes::Command c;
                c.op = eyes::Operation::Configure; c.enabled = want.enabled; c.style = want.style;
                eye_request_id(c.request_id, [] { return esp_random(); });
                const auto result = eyes::submit(c, eyes::Source::Manual);
                eye_mode.applied(want, result.accepted, t);
                ESP_LOGI(kTag, "eyes: mode %s -> %s (%s)", mode.look.label,
                         want.enabled ? eyes::kStyleIds[static_cast<size_t>(want.style)] : "laughing man",
                         result.accepted ? "accepted" : eyes::name(result.error));
            }
        }
        update_screen(ip, ssid);
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
