#include "cores3_audio_codec.h"
#include "hal_bridge.h"

#include <esp_log.h>
#include <esp_timer.h>
#include <driver/i2c_master.h>
#include <driver/i2s_tdm.h>
#include <algorithm>
#include <array>

#define TAG "CoreS3AudioCodec"

CoreS3AudioCodec::CoreS3AudioCodec(void* i2c_master_handle, int input_sample_rate, int output_sample_rate,
    gpio_num_t mclk, gpio_num_t bclk, gpio_num_t ws, gpio_num_t dout, gpio_num_t din,
    uint8_t aw88298_addr, uint8_t es7210_addr, bool input_reference) {
    duplex_ = true; // 是否双工
    input_reference_ = input_reference; // 是否使用参考输入，实现回声消除
    input_channels_ = input_reference_ ? 2 : 1; // 输入通道数
    input_sample_rate_ = input_sample_rate;
    output_sample_rate_ = output_sample_rate;
    input_gain_ = 60;
    collector_i2c_bus_ = i2c_master_handle;
    collector_aw88298_addr_ = aw88298_addr;

    CreateDuplexChannels(mclk, bclk, ws, dout, din);

    // Do initialize of related interface: data_if, ctrl_if and gpio_if
    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = I2S_NUM_0,
        .rx_handle = rx_handle_,
        .tx_handle = tx_handle_,
    };
    data_if_ = audio_codec_new_i2s_data(&i2s_cfg);
    assert(data_if_ != NULL);

    // Audio Output(Speaker)
    audio_codec_i2c_cfg_t i2c_cfg = {
        .port = (i2c_port_t)1,
        .addr = aw88298_addr,
        .bus_handle = i2c_master_handle,
    };
    // aw88298_codec_new() powers/unmutes the amplifier during construction.
    // Collector creates it only during an explicitly requested playback.
    if (!hal_bridge::is_collector_mode()) {
        out_ctrl_if_ = audio_codec_new_i2c_ctrl(&i2c_cfg);
        assert(out_ctrl_if_ != NULL);
        gpio_if_ = audio_codec_new_gpio();
        assert(gpio_if_ != NULL);
        aw88298_codec_cfg_t aw88298_cfg = {};
        aw88298_cfg.ctrl_if = out_ctrl_if_;
        aw88298_cfg.gpio_if = gpio_if_;
        aw88298_cfg.reset_pin = GPIO_NUM_NC;
        aw88298_cfg.hw_gain.pa_voltage = 5.0;
        aw88298_cfg.hw_gain.codec_dac_voltage = 3.3;
        aw88298_cfg.hw_gain.pa_gain = 1;
        out_codec_if_ = aw88298_codec_new(&aw88298_cfg);
        assert(out_codec_if_ != NULL);
        esp_codec_dev_cfg_t output_cfg = {
            .dev_type = ESP_CODEC_DEV_TYPE_OUT,
            .codec_if = out_codec_if_,
            .data_if = data_if_,
        };
        output_dev_ = esp_codec_dev_new(&output_cfg);
        assert(output_dev_ != NULL);
    }

    // Audio Input(Microphone)
    i2c_cfg.addr = es7210_addr;
    in_ctrl_if_ = audio_codec_new_i2c_ctrl(&i2c_cfg);
    assert(in_ctrl_if_ != NULL);

    es7210_codec_cfg_t es7210_cfg = {};
    es7210_cfg.ctrl_if = in_ctrl_if_;
    es7210_cfg.mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 | ES7210_SEL_MIC3;
    in_codec_if_ = es7210_codec_new(&es7210_cfg);
    assert(in_codec_if_ != NULL);

    esp_codec_dev_cfg_t dev_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
        .codec_if = in_codec_if_,
        .data_if = data_if_,
    };
    input_dev_ = esp_codec_dev_new(&dev_cfg);
    assert(input_dev_ != NULL);

    ESP_LOGI(TAG, "CoreS3AudioCodec initialized");
}

CoreS3AudioCodec::~CoreS3AudioCodec() {
    if (output_dev_) {
        ESP_ERROR_CHECK(esp_codec_dev_close(output_dev_));
        esp_codec_dev_delete(output_dev_);
    }
    ESP_ERROR_CHECK(esp_codec_dev_close(input_dev_));
    esp_codec_dev_delete(input_dev_);

    audio_codec_delete_codec_if(in_codec_if_);
    audio_codec_delete_ctrl_if(in_ctrl_if_);
    if (out_codec_if_) audio_codec_delete_codec_if(out_codec_if_);
    if (out_ctrl_if_) audio_codec_delete_ctrl_if(out_ctrl_if_);
    if (gpio_if_) audio_codec_delete_gpio_if(gpio_if_);
    audio_codec_delete_data_if(data_if_);
}

void CoreS3AudioCodec::CreateDuplexChannels(gpio_num_t mclk, gpio_num_t bclk, gpio_num_t ws, gpio_num_t dout, gpio_num_t din) {
    assert(input_sample_rate_ == output_sample_rate_);

    ESP_LOGI(TAG, "Audio IOs: mclk: %d, bclk: %d, ws: %d, dout: %d, din: %d", mclk, bclk, ws, dout, din);

    i2s_chan_config_t chan_cfg = {
        .id = I2S_NUM_0,
        .role = I2S_ROLE_MASTER,
        .dma_desc_num = AUDIO_CODEC_DMA_DESC_NUM,
        .dma_frame_num = AUDIO_CODEC_DMA_FRAME_NUM,
        .auto_clear_after_cb = true,
        .auto_clear_before_cb = false,
        .intr_priority = 0,
    };
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &tx_handle_, &rx_handle_));

    i2s_std_config_t std_cfg = {
        .clk_cfg = {
            .sample_rate_hz = (uint32_t)output_sample_rate_,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .ext_clk_freq_hz = 0,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256
        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_16BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode = I2S_SLOT_MODE_STEREO,
            .slot_mask = I2S_STD_SLOT_BOTH,
            .ws_width = I2S_DATA_BIT_WIDTH_16BIT,
            .ws_pol = false,
            .bit_shift = true,
            .left_align = true,
            .big_endian = false,
            .bit_order_lsb = false
        },
        .gpio_cfg = {
            .mclk = mclk,
            .bclk = bclk,
            .ws = ws,
            .dout = dout,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false
            }
        }
    };

    i2s_tdm_config_t tdm_cfg = {
        .clk_cfg = {
            .sample_rate_hz = (uint32_t)input_sample_rate_,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .ext_clk_freq_hz = 0,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
            .bclk_div = 8,
        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_16BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode = I2S_SLOT_MODE_STEREO,
            .slot_mask = i2s_tdm_slot_mask_t(I2S_TDM_SLOT0 | I2S_TDM_SLOT1 | I2S_TDM_SLOT2 | I2S_TDM_SLOT3),
            .ws_width = I2S_TDM_AUTO_WS_WIDTH,
            .ws_pol = false,
            .bit_shift = true,
            .left_align = false,
            .big_endian = false,
            .bit_order_lsb = false,
            .skip_mask = false,
            .total_slot = I2S_TDM_AUTO_SLOT_NUM
        },
        .gpio_cfg = {
            .mclk = mclk,
            .bclk = bclk,
            .ws = ws,
            .dout = I2S_GPIO_UNUSED,
            .din = din,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false
            }
        }
    };

    ESP_ERROR_CHECK(i2s_channel_init_std_mode(tx_handle_, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_init_tdm_mode(rx_handle_, &tdm_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(tx_handle_));
    ESP_ERROR_CHECK(i2s_channel_enable(rx_handle_));
    ESP_LOGI(TAG, "Duplex channels created");
}

void CoreS3AudioCodec::SetOutputVolume(int volume) {
    if (!output_dev_) return;
    ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(output_dev_, volume));
    AudioCodec::SetOutputVolume(volume);
}

void CoreS3AudioCodec::EnableInput(bool enable) {
    if (enable == input_enabled_) {
        return;
    }
    if (enable) {
        esp_codec_dev_sample_info_t fs = {
            .bits_per_sample = 16,
            .channel = 2,
            .channel_mask = ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0),
            .sample_rate = (uint32_t)output_sample_rate_,
            .mclk_multiple = 0,
        };
        if (input_reference_) {
            fs.channel_mask |= ESP_CODEC_DEV_MAKE_CHANNEL_MASK(1);
        }
        ESP_ERROR_CHECK(esp_codec_dev_open(input_dev_, &fs));
        ESP_ERROR_CHECK(esp_codec_dev_set_in_channel_gain(input_dev_, ESP_CODEC_DEV_MAKE_CHANNEL_MASK(0), input_gain_));
    } else {
        ESP_ERROR_CHECK(esp_codec_dev_close(input_dev_));
    }
    AudioCodec::EnableInput(enable);
}

void CoreS3AudioCodec::EnableOutput(bool enable) {
    if (!output_dev_) return;
    if (enable == output_enabled_) {
        return;
    }
    if (enable) {
        // Play 16bit 1 channel
        esp_codec_dev_sample_info_t fs = {
            .bits_per_sample = 16,
            .channel = 1,
            .channel_mask = 0,
            .sample_rate = (uint32_t)output_sample_rate_,
            .mclk_multiple = 0,
        };
        ESP_ERROR_CHECK(esp_codec_dev_open(output_dev_, &fs));
        ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(output_dev_, output_volume_));
    } else {
        ESP_ERROR_CHECK(esp_codec_dev_close(output_dev_));
    }
    AudioCodec::EnableOutput(enable);
}

int CoreS3AudioCodec::Read(int16_t* dest, int samples) {
    if (input_enabled_) {
        const auto result = esp_codec_dev_read(input_dev_, (void*)dest, samples * sizeof(int16_t));
        if (result != ESP_OK) return 0;
        return samples;
    }
    return 0;
}

int CoreS3AudioCodec::Write(const int16_t* data, int samples) {
    if (output_enabled_) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_codec_dev_write(output_dev_, (void*)data, samples * sizeof(int16_t)));
    }
    return samples;
}

namespace {
esp_err_t output_result(int result, const char* operation)
{
    if (result == ESP_CODEC_DEV_OK) return ESP_OK;
    ESP_LOGE(TAG, "Collector output %s failed: %d", operation, result);
    return ESP_FAIL;
}
}

esp_err_t CoreS3AudioCodec::PrepareCollectorOutput()
{
    if (!hal_bridge::is_collector_mode() || !input_enabled_ || collector_output_active_ ||
        hal_bridge::board_speaker_reset_state() != 1) return ESP_ERR_INVALID_STATE;
    i2s_chan_info_t info{};
    esp_err_t err = i2s_channel_get_info(tx_handle_, &info);
    if (err != ESP_OK) return err;
    if (!info.is_enabled || info.mode != I2S_COMM_MODE_STD || !info.mode_cfg || !info.total_dma_buf_size)
        return ESP_ERR_INVALID_STATE;
    const auto& config = *static_cast<const i2s_std_config_t*>(info.mode_cfg);
    if (config.clk_cfg.sample_rate_hz != 24000 || config.slot_cfg.data_bit_width != I2S_DATA_BIT_WIDTH_16BIT ||
        config.slot_cfg.slot_mode != I2S_SLOT_MODE_STEREO || config.slot_cfg.slot_mask != I2S_STD_SLOT_BOTH ||
        (config.slot_cfg.slot_bit_width != I2S_SLOT_BIT_WIDTH_AUTO &&
         config.slot_cfg.slot_bit_width != I2S_SLOT_BIT_WIDTH_16BIT)) return ESP_ERR_NOT_SUPPORTED;
    // Leave the shared clock/slots untouched. Overwrite a complete DMA ring
    // with zeros before releasing amplifier reset, including after a failure.
    static const std::array<int16_t, 960> silence{};
    const int64_t deadline = esp_timer_get_time() + 2000000;
    for (size_t offset = 0; offset < info.total_dma_buf_size;) {
        if (esp_timer_get_time() >= deadline) return ESP_ERR_TIMEOUT;
        const size_t count = std::min(sizeof(silence), size_t(info.total_dma_buf_size) - offset);
        size_t written = 0;
        err = i2s_channel_write(tx_handle_, silence.data(), count, &written, 100);
        vTaskDelay(1);
        if (err != ESP_OK) return err;
        if (written != count) return ESP_FAIL;
        offset += written;
    }
    return esp_timer_get_time() < deadline ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t CoreS3AudioCodec::BeginCollectorOutput(int volume)
{
    if (volume < 1 || volume > 100) return ESP_ERR_INVALID_ARG;
    if (!hal_bridge::is_collector_mode() || !input_enabled_ || output_dev_ || out_codec_if_ ||
        out_ctrl_if_ || gpio_if_ || output_sample_rate_ != 24000) return ESP_ERR_INVALID_STATE;
    audio_codec_i2c_cfg_t control{};
    control.port = I2C_NUM_1;
    control.addr = collector_aw88298_addr_;
    control.bus_handle = collector_i2c_bus_;
    out_ctrl_if_ = audio_codec_new_i2c_ctrl(&control);
    if (!out_ctrl_if_) return ESP_ERR_NO_MEM;
    gpio_if_ = audio_codec_new_gpio();
    if (!gpio_if_) return ESP_ERR_NO_MEM;
    aw88298_codec_cfg_t config{};
    config.ctrl_if = out_ctrl_if_;
    config.gpio_if = gpio_if_;
    config.reset_pin = GPIO_NUM_NC;
    config.hw_gain.pa_voltage = 5.0;
    config.hw_gain.codec_dac_voltage = 3.3;
    config.hw_gain.pa_gain = 1;
    out_codec_if_ = aw88298_codec_new(&config);
    if (!out_codec_if_) return ESP_FAIL;
    // The upstream constructor briefly unmutes. TX contains only silence here:
    // no input payload is written until every setup step has succeeded.
    esp_err_t err = output_result(out_codec_if_->mute(out_codec_if_, true), "mute");
    if (err != ESP_OK) return err;
    esp_codec_dev_sample_info_t format{};
    format.bits_per_sample = 16;
    format.channel = 1;
    format.sample_rate = 24000;
    err = output_result(out_codec_if_->set_fs(out_codec_if_, &format), "format");
    if (err != ESP_OK) return err;
    err = output_result(out_codec_if_->enable(out_codec_if_, true), "enable");
    if (err != ESP_OK) return err;
    // Match the locked component's default 0..100 -> -50..0 dB curve.
    // Calling the codec interface directly checks I2C errors and avoids NVS.
    err = output_result(out_codec_if_->set_vol(out_codec_if_, -50.0f + volume * 0.5f), "volume");
    if (err != ESP_OK) return err;
    err = output_result(out_codec_if_->mute(out_codec_if_, false), "unmute");
    if (err == ESP_OK) collector_output_active_ = true;
    return err;
}

esp_err_t CoreS3AudioCodec::WriteCollectorOutput(const int16_t* samples, size_t count)
{
    if (!collector_output_active_ || !samples || count == 0 || count > 480) return ESP_ERR_INVALID_STATE;
    // Existing microphone setup keeps TX at 24 kHz, 16-bit stereo. Duplicate
    // mono samples into its slots instead of reconfiguring the shared bus.
    std::array<int16_t, 960> stereo;
    for (size_t i = 0; i < count; ++i) stereo[i * 2] = stereo[i * 2 + 1] = samples[i];
    const size_t bytes = count * 2 * sizeof(int16_t);
    size_t written = 0;
    const auto err = i2s_channel_write(tx_handle_, stereo.data(), bytes, &written, 100);
    return err != ESP_OK ? err : (written == bytes ? ESP_OK : ESP_FAIL);
}

esp_err_t CoreS3AudioCodec::EndCollectorOutput()
{
    if (!hal_bridge::is_collector_mode()) return ESP_ERR_INVALID_STATE;
    esp_err_t result = ESP_OK;
    auto check = [&result](int status, const char* operation) {
        const esp_err_t err = output_result(status, operation);
        if (result == ESP_OK) result = err;
    };
    if (out_codec_if_) {
        check(out_codec_if_->mute(out_codec_if_, true), "cleanup mute");
        check(out_codec_if_->enable(out_codec_if_, false), "cleanup disable");
    }
    if (out_codec_if_) { audio_codec_delete_codec_if(out_codec_if_); out_codec_if_ = nullptr; }
    if (out_ctrl_if_) { audio_codec_delete_ctrl_if(out_ctrl_if_); out_ctrl_if_ = nullptr; }
    if (gpio_if_) { audio_codec_delete_gpio_if(gpio_if_); gpio_if_ = nullptr; }
    collector_output_active_ = false;
    return result;
}
