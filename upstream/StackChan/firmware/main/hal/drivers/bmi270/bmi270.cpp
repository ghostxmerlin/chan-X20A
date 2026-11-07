/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "bmi270.h"
#include "../environment/bmi270_aux.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <cmath>
#include <cstring>

static const char* TAG = "BMI270";

BMI270::BMI270(i2c_master_bus_handle_t i2c_bus_handle, uint8_t addr) : _addr(addr), _initialized(false)
{
    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address  = _addr,
        .scl_speed_hz    = 400000,
    };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(i2c_bus_handle, &dev_cfg, &_i2c_dev));

    _bmi.intf            = BMI2_I2C_INTF;
    _bmi.read            = bmi2_i2c_read;
    _bmi.write           = bmi2_i2c_write;
    _bmi.delay_us        = bmi2_delay_us;
    _bmi.read_write_len  = 32;         // Max read/write length
    _bmi.config_file_ptr = NULL;       // Use default config
    _bmi.intf_ptr        = this;
}

BMI270::~BMI270()
{
    if (_i2c_dev) {
        i2c_master_bus_rm_device(_i2c_dev);
    }
}

BMI2_INTF_RETURN_TYPE BMI270::bmi2_i2c_read(uint8_t reg_addr, uint8_t* reg_data, uint32_t len, void* intf_ptr)
{
    auto* self = static_cast<BMI270*>(intf_ptr);
    esp_err_t err = i2c_master_transmit_receive(self->_i2c_dev, &reg_addr, 1, reg_data, len, self->_io_timeout_ms);
    return (err == ESP_OK) ? BMI2_OK : BMI2_E_COM_FAIL;
}

BMI2_INTF_RETURN_TYPE BMI270::bmi2_i2c_write(uint8_t reg_addr, const uint8_t* reg_data, uint32_t len, void* intf_ptr)
{
    auto* self = static_cast<BMI270*>(intf_ptr);

    uint8_t* buf = (uint8_t*)malloc(len + 1);
    if (!buf) return BMI2_E_COM_FAIL;

    buf[0] = reg_addr;
    memcpy(buf + 1, reg_data, len);

    esp_err_t err = i2c_master_transmit(self->_i2c_dev, buf, len + 1, self->_io_timeout_ms);
    free(buf);

    return (err == ESP_OK) ? BMI2_OK : BMI2_E_COM_FAIL;
}

#define NOP() asm volatile("nop")

void BMI270::bmi2_delay_us(uint32_t period, void* intf_ptr)
{
    uint64_t m = (uint64_t)esp_timer_get_time();
    if (period) {
        uint64_t e = (m + period);
        if (m > e) {  // overflow
            while ((uint64_t)esp_timer_get_time() > e) {
                NOP();
            }
        }
        while ((uint64_t)esp_timer_get_time() < e) {
            NOP();
        }
    }
}

bool BMI270::begin(uint8_t accelRangeG)
{
    if (accelRangeG != 2 && accelRangeG != 4 && accelRangeG != 8 && accelRangeG != 16) return false;
    _accel_range_g = accelRangeG;
    int8_t rslt;

    // Initialize bmi270
    rslt = bmi270_init(&_bmi);
    if (rslt != BMI2_OK) {
        ESP_LOGE(TAG, "bmi270_init failed: %d", rslt);
        return false;
    }

    // Configure Accel
    struct bmi2_sens_config config;
    config.type = BMI2_ACCEL;
    rslt        = bmi2_get_sensor_config(&config, 1, &_bmi);
    if (rslt == BMI2_OK) {
        config.cfg.acc.odr         = BMI2_ACC_ODR_100HZ;
        config.cfg.acc.range       = accelRangeG == 16 ? BMI2_ACC_RANGE_16G : accelRangeG == 8 ? BMI2_ACC_RANGE_8G : accelRangeG == 4 ? BMI2_ACC_RANGE_4G : BMI2_ACC_RANGE_2G;
        config.cfg.acc.bwp         = BMI2_ACC_NORMAL_AVG4;
        config.cfg.acc.filter_perf = BMI2_PERF_OPT_MODE;
        rslt                       = bmi2_set_sensor_config(&config, 1, &_bmi);
    }
    if (rslt != BMI2_OK) {
        ESP_LOGE(TAG, "Accel config failed: %d", rslt);
        return false;
    }

    // Configure Gyro
    config.type = BMI2_GYRO;
    rslt        = bmi2_get_sensor_config(&config, 1, &_bmi);
    if (rslt == BMI2_OK) {
        config.cfg.gyr.odr         = BMI2_GYR_ODR_100HZ;
        config.cfg.gyr.range       = BMI2_GYR_RANGE_2000;
        config.cfg.gyr.bwp         = BMI2_GYR_NORMAL_MODE;
        config.cfg.gyr.filter_perf = BMI2_PERF_OPT_MODE;
        config.cfg.gyr.noise_perf  = BMI2_PERF_OPT_MODE;
        rslt                       = bmi2_set_sensor_config(&config, 1, &_bmi);
    }
    if (rslt != BMI2_OK) {
        ESP_LOGE(TAG, "Gyro config failed: %d", rslt);
        return false;
    }

    // Enable sensors
    uint8_t sensor_list[2] = {BMI2_ACCEL, BMI2_GYRO};
    rslt                   = bmi2_sensor_enable(sensor_list, 2, &_bmi);
    if (rslt != BMI2_OK) {
        ESP_LOGE(TAG, "Sensor enable failed: %d", rslt);
        return false;
    }

    _initialized = true;
    return true;
}

bool BMI270::beginMagnetometer()
{
    using environment::Error;
    if (!_initialized) { environment::fail(_magnetic, Error::NotInitialized); return false; }
    const uint8_t saved_aps = _bmi.aps_status;
    const uint8_t aux_sensor = BMI2_AUX;
    // Optional init failures must not spend the original driver's 1 s timeout
    // per transaction, and must never reset/reconfigure the working accel/gyro.
    _io_timeout_ms = 20;
    auto failed = [&](Error error) {
        environment::fail(_magnetic, error);
        _magnetic.available = false;
        bmi2_sensor_disable(&aux_sensor, 1, &_bmi);
        bmi2_set_adv_power_save(saved_aps, &_bmi);
        _io_timeout_ms = 1000;
        return false;
    };
    if (bmi2_set_adv_power_save(BMI2_DISABLE, &_bmi) != BMI2_OK ||
        bmi2_sensor_disable(&aux_sensor, 1, &_bmi) != BMI2_OK) return failed(Error::I2c);
    bmi2_sens_config config{};
    config.type = BMI2_AUX;
    config.cfg.aux.aux_en = BMI2_ENABLE;
    config.cfg.aux.manual_en = BMI2_ENABLE;
    config.cfg.aux.man_rd_burst = 3; // register encoding: 8 bytes
    config.cfg.aux.aux_rd_burst = 3;
    config.cfg.aux.odr = BMI2_AUX_ODR_25HZ;
    config.cfg.aux.i2c_device_addr = 0x10;
    config.cfg.aux.read_addr = 0x42;
    if (bmi2_set_sensor_config(&config, 1, &_bmi) != BMI2_OK) return failed(Error::Config);
    auto read = [&](uint8_t reg, uint8_t* value, uint16_t length) {
        return environment::bmi270_read_aux(reg, value, length,
            [&](uint8_t address, uint8_t* data, size_t size) {
                return bmi2_get_regs(address, data, size, &_bmi) == BMI2_OK;
            },
            [&](uint8_t address, uint8_t data) {
                return bmi2_set_regs(address, &data, 1, &_bmi) == BMI2_OK;
            },
            []() { vTaskDelay(1); });
    };
    auto write_checked = [&](uint8_t reg, uint8_t value) {
        uint8_t actual = 0;
        return bmi2_write_aux_man_mode(reg, &value, 1, &_bmi) == BMI2_OK &&
               read(reg, &actual, 1) && actual == value;
    };
    uint8_t power = 1;
    if (bmi2_write_aux_man_mode(0x4b, &power, 1, &_bmi) != BMI2_OK) return failed(Error::I2c);
    vTaskDelay(pdMS_TO_TICKS(10) > 0 ? pdMS_TO_TICKS(10) : 1);
    uint8_t id = 0;
    if (!read(0x40, &id, 1)) return failed(Error::I2c);
    _magnetic.chip_id = id;
    if (id != 0x32) return failed(Error::ChipId);
    uint8_t trim_a[2]{}, trim_b[4]{}, trim_c[10]{};
    if (!read(0x5d, trim_a, 2) || !read(0x62, trim_b, 4) || !read(0x68, trim_c, 10)) return failed(Error::I2c);
    _mag_trim = environment::bmm150_trim(trim_a, trim_b, trim_c);
    if (!_mag_trim.valid()) return failed(Error::Trim);
    // Bosch regular preset: 10 Hz, XY repetition=4, Z repetition=7.
    // Configure in sleep, then enter normal mode; never force a new conversion
    // from the 50 Hz IMU loop.
    if (!write_checked(0x4c, 6) || !write_checked(0x51, 4) ||
        !write_checked(0x52, 7) || !write_checked(0x4c, 0)) return failed(Error::Config);
    config.cfg.aux.manual_en = BMI2_DISABLE;
    if (bmi2_set_sensor_config(&config, 1, &_bmi) != BMI2_OK ||
        bmi2_sensor_enable(&aux_sensor, 1, &_bmi) != BMI2_OK) return failed(Error::Config);
    // Auto AUX needs APS off. Accel/gyro ODR/ranges and enable bits are retained.
    _io_timeout_ms = 1000;
    _magnetic.available = true;
    _magnetic.error = Error::NotInitialized; // available hardware, no sample yet
    return true;
}

bool BMI270::update()
{
    if (!_initialized) return false;

    struct bmi2_sens_data sens_data = {{0}};
    int8_t rslt                     = bmi2_get_sensor_data(&sens_data, &_bmi);

    if (rslt == BMI2_OK) {
        environment::bmm150_consume(_magnetic, _mag_trim, sens_data.status,
                                    sens_data.aux_data, esp_timer_get_time());
        // Convert Accel
        _data.accel_x = lsb_to_mps2(sens_data.acc.x, _accel_range_g, 16);
        _data.accel_y = lsb_to_mps2(sens_data.acc.y, _accel_range_g, 16);
        _data.accel_z = lsb_to_mps2(sens_data.acc.z, _accel_range_g, 16);

        // Convert Gyro
        // Assuming 2000dps range and 16-bit resolution
        _data.gyro_x = lsb_to_dps(sens_data.gyr.x, 2000.0f, 16);
        _data.gyro_y = lsb_to_dps(sens_data.gyr.y, 2000.0f, 16);
        _data.gyro_z = lsb_to_dps(sens_data.gyr.z, 2000.0f, 16);

        return true;
    }
    if (_magnetic.available) environment::fail(_magnetic, environment::Error::I2c);
    return false;
}

void BMI270::getAccelerometer(float& x, float& y, float& z)
{
    x = _data.accel_x;
    y = _data.accel_y;
    z = _data.accel_z;
}

void BMI270::getGyroscope(float& x, float& y, float& z)
{
    x = _data.gyro_x;
    y = _data.gyro_y;
    z = _data.gyro_z;
}

const BMI270_Data& BMI270::getData()
{
    return _data;
}

float BMI270::lsb_to_mps2(int16_t val, float g_range, uint8_t bit_width)
{
    float half_scale = (float)(1 << (bit_width - 1));
    return (9.80665f * val * g_range) / half_scale;
}

float BMI270::lsb_to_dps(int16_t val, float dps, uint8_t bit_width)
{
    float half_scale = (float)(1 << (bit_width - 1));
    return (dps * val) / half_scale;
}
