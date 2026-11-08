/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "joystick_handle.h"
#include "center_button.h"
#include "center_packet.h"
#include "mapped_target.h"
#include "freertos/semphr.h"
#include "esp_random.h"
#include "nvs.h"
#include "../remote_settings.h"

i2c_bus_device_handle_t i2c_device1;  // i2c device handle
static SemaphoreHandle_t joystick_mutex;
static center_button_filter_t center_button;
static uint32_t mode_generation;

void joystick_set_mode(joystick_data_t *data, uint8_t mode)
{
    xSemaphoreTake(joystick_mutex, portMAX_DELAY);
    if (data->screen_mode != mode) {
        center_button_reset(&center_button);
        ++mode_generation;
        data->screen_mode = mode;
    }
    xSemaphoreGive(joystick_mutex);
}

/**
 * @brief Initialize joystick via I2C interface
 * @note This is an internal static function that configures I2C_NUM_0 as master with SDA on GPIO0 and SCL on GPIO26
 * @details
 *      1. Configures I2C master mode with 100kHz clock speed
 *      2. Creates I2C bus handle using I2C_NUM_0
 *      3. Scans the I2C bus to detect connected devices and logs their addresses
 *      4. Creates device handle for joystick at I2C address 0x54
 *      5. Assigns the device handle to global variable [i2c_device1]
 * @warning This function assumes the joystick device is at I2C address 0x54
 */
static void joystick_i2c_init()
{
    i2c_config_t conf;
    {
        conf.mode             = I2C_MODE_MASTER;
        conf.sda_io_num       = 0;
        conf.scl_io_num       = 26;
        conf.sda_pullup_en    = GPIO_PULLUP_ENABLE;
        conf.scl_pullup_en    = GPIO_PULLUP_ENABLE;
        conf.master.clk_speed = 100000;
        conf.clk_flags        = 0;
    };
    i2c_bus_handle_t i2c0_bus = i2c_bus_create(I2C_NUM_0, &conf);
    uint8_t buf[128];
    memset(buf, 0, sizeof(buf));

    i2c_bus_scan(i2c0_bus, buf, sizeof(buf));
    for (size_t i = 0; i < sizeof(buf); i++) {
        if (buf[i] != 0) {
            ESP_LOGI("I2C Scanner", "Found device at address 0x%02X", buf[i]);
        }
    }

    i2c_device1 = i2c_bus_device_create(i2c0_bus, 0x54, 0);
    joystick_mutex = xSemaphoreCreateMutex();
    configASSERT(joystick_mutex);
}

/**
 * @brief Read X and Y axis values from the joystick via I2C
 * @param joyX Pointer to store X-axis value (16-bit unsigned integer)
 * @param joyY Pointer to store Y-axis value (16-bit unsigned integer)
 * @return void
 * @details
 *      1. Reads 2 bytes from register address 0x00 (X-axis low/high bytes)
 *      2. Waits 10ms to ensure data stability
 *      3. Reads 2 bytes from register address 0x02 (Y-axis low/high bytes)
 *      4. Combines high and low bytes for both X and Y axes using bit shifting
 *      5. Stores the combined values in the provided pointers
 * @warning This function assumes the joystick provides 16-bit data in little-endian format
 */
static void joystick_read_xy(uint16_t *joyX, uint16_t *joyY)
{
    xSemaphoreTake(joystick_mutex, portMAX_DELAY);
    uint8_t data[4];
    esp_err_t ret = i2c_bus_read_bytes(i2c_device1, 0x00, 2, data);
    vTaskDelay(20 / portTICK_PERIOD_MS);
    ret |= i2c_bus_read_bytes(i2c_device1, 0x02, 2, &data[2]);
    if (ret == ESP_OK) {
        *joyX = (data[1] << 8) | data[0];
        *joyY = (data[3] << 8) | data[2];
    } else {
        // ESP_LOGE("I2C Joystick", "Failed to read joystick data");
    }
    xSemaphoreGive(joystick_mutex);
}

static bool joystick_read_center(joystick_data_t *data, uint8_t expected_mode, uint32_t *generation)
{
    // Official Mini JoyC library uses a STOP between register selection and
    // read. Its STM32 prepares 0x30 in the write-completion callback; allow it
    // one tick and keep the X/Y readers from changing the selected register.
    // https://github.com/m5stack/M5Hat-Mini-JoyC/blob/main/src/M5HatMiniJoyC.cpp
    const uint8_t reg = 0x30;
    uint8_t value = 0xff;
    xSemaphoreTake(joystick_mutex, portMAX_DELAY);
    if (data->screen_mode != expected_mode) {
        xSemaphoreGive(joystick_mutex);
        return false;
    }
    *generation = mode_generation;
    esp_err_t ret = i2c_bus_write_bytes(i2c_device1, NULL_I2C_MEM_ADDR, 1, &reg);
    if (ret == ESP_OK) {
        vTaskDelay(1);
        ret = i2c_bus_read_bytes(i2c_device1, NULL_I2C_MEM_ADDR, 1, &value);
    }
    if (center_button_sample(&center_button, ret == ESP_OK, value)) {
        if (++data->center_press_seq == 0) {
            // Sequence wrap starts a fresh baseline instead of replaying.
            do { data->sender_boot_nonce = esp_random(); } while (!data->sender_boot_nonce);
        }
    }
    xSemaphoreGive(joystick_mutex);
    return true;
}

/**
 * @brief Public interface to initialize the joystick and return default configuration
 * @return joystick_data_t Structure containing initialized joystick parameters
 * @note This is the main initialization function exposed to users
 * @details
 *      1. Calls internal： device_joystick_init()
 *      2. Initializes all fields of 'joystick_data_t'
 *         - channel/id: saved connection, otherwise CH6/receiver 1
 *         - bat: 0 (battery level, to be updated later)
 *         - joyX, joyY: 0 (initial joystick positions)
 *         - screen_mode: MODE_RUNNING (ready for joystick control)
 *         - select_mode: CHANNEL_SELECT (default selection mode)
 * @return joystick_data_t
 */
joystick_data_t joystick_init()
{
    joystick_i2c_init();
    remote_settings_t settings;
    const esp_err_t settings_err = remote_settings_load(&settings);
    if (settings_err != ESP_OK && settings_err != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGW("remote_init", "Connection settings unavailable (%s); using CH6/ID1 without changing storage",
                 esp_err_to_name(settings_err));
    }
    joystick_data_t tmp;
    tmp.channel     = settings.channel;
    tmp.id          = settings.id;
    tmp.bat         = 0;
    tmp.joyX        = 0;
    tmp.joyY        = 0;
    tmp.accel_x     = 0.0f;
    tmp.accel_y     = 0.0f;
    tmp.accel_z     = 0.0f;
    tmp.screen_mode = MODE_RUNNING;
    tmp.select_mode = CHANNEL_SELECT;
    tmp.btnB_status = false;
    do { tmp.sender_boot_nonce = esp_random(); } while (!tmp.sender_boot_nonce);
    tmp.center_press_seq = 0;
    return tmp;
}

/**
 * @brief Task to handle joystick setup screen
 * @param pvParam Pointer to joystick data, pointing to joystick_data_t structure
 * @note This function runs an infinite loop that continuously reads joystick XY coordinates
 *       and updates the setup screen when the screen mode is MODE_SETUP
 * @details Reads raw joystick data and then calls update_setup_screen function to update screen display
 *          Each loop iteration has a 50ms delay to ensure interface responsiveness
 */
void handle_setup_screen(void *pvParam)
{
    joystick_data_t *joystick_data = (joystick_data_t *)pvParam;
    while (1) {
        if (joystick_data->screen_mode == MODE_SETUP) {
            joystick_read_xy(&joystick_data->joyX, &joystick_data->joyY);
            update_setup_screen(joystick_data);
            vTaskDelay(50 / portTICK_PERIOD_MS);
        } else {
            vTaskDelay(200 / portTICK_PERIOD_MS);
        }
    }
}

/**
 * @brief Task to handle joystick running screen, responsible for reading joystick data and sending ESP-NOW control
 * packets
 * @param pvParam Pointer to joystick data, pointing to joystick_data_t structure
 * @note This function runs an infinite loop that reads joystick input, processes data, and sends control packets in
 * running mode
 * @details
 *      1. Reads raw X/Y values from the joystick via I2C
 *      2. Updates the running screen display with current values
 *      3. Applies deadzone correction to center the joystick values
 *      4. Maps raw values to yaw/pitch angle ranges (-1280 to 1280 for yaw, 0 to 900 for pitch)
 *      5. Only sends data when changes exceed threshold (5 units) to reduce network traffic
 *      6. Constructs and sends ESP-NOW packet containing target ID, yaw, pitch, speed and button status
 *      7. Each loop iteration has a 30ms delay when in running mode
 */
void handle_running_screen(void *pvParam)
{
    joystick_data_t *joystick_data = (joystick_data_t *)pvParam;
    // communicate packet
    uint8_t pkt[16] = {0};
    pkt[0]              = joystick_data->id;  // id: 0 for broadcast
    int16_t yaw_angle   = 0;
    int16_t pitch_angle = 0;
    mapped_target_filter_t accepted = {0};
    int16_t speed_val   = 600;
    bool sent_packet = false;
    uint32_t last_center_seq = 0;
    TickType_t last_packet_tick = 0;
    uint32_t observed_mode_generation = UINT32_MAX;

    while (1) {
        // update screen and send packet when in running mode
        if (joystick_data->screen_mode == MODE_RUNNING) {
            uint32_t generation;
            if (!joystick_read_center(joystick_data, MODE_RUNNING, &generation)) {
                vTaskDelay(1);
                continue;
            }
            if (generation != observed_mode_generation) {
                observed_mode_generation = generation;
                sent_packet = false;
                accepted.initialized = false;
            }
            joystick_read_xy(&joystick_data->joyX, &joystick_data->joyY);

            update_running_screen(joystick_data->joyX, joystick_data->joyY, joystick_data->channel,
                                  joystick_data->id, joystick_data->bat);

            // handle data from joystick
            if ((joystick_data->joyX < X_CENTER + DEAD_ZONE) && (joystick_data->joyX > X_CENTER - DEAD_ZONE)) {
                joystick_data->joyX = X_CENTER;
            }
            if ((joystick_data->joyY < Y_CENTER + DEAD_ZONE) && (joystick_data->joyY > Y_CENTER - DEAD_ZONE)) {
                joystick_data->joyY = Y_CENTER;
            }

            yaw_angle   = (int16_t)map(joystick_data->joyX, X_MIN, X_MAX, 1280, -1280);
            pitch_angle = (int16_t)map(joystick_data->joyY, Y_MIN, Y_MAX, 0, 900);

            // Heartbeats and button events retain the last accepted angle pair.
            const bool target_changed = mapped_target_accept(&accepted, yaw_angle, pitch_angle, 5);
            if (sent_packet && !target_changed &&
                pkt[7] == joystick_data->btnB_status && pkt[0] == joystick_data->id &&
                last_center_seq == joystick_data->center_press_seq &&
                xTaskGetTickCount() - last_packet_tick < pdMS_TO_TICKS(200)) {
                vTaskDelay(30 / portTICK_PERIOD_MS);
                continue;
            }

            pkt[0] = joystick_data->id;
            memcpy(&pkt[1], &accepted.yaw, sizeof(int16_t));
            memcpy(&pkt[3], &accepted.pitch, sizeof(int16_t));
            memcpy(&pkt[5], &speed_val, sizeof(int16_t));
            pkt[7] = joystick_data->btnB_status;
            center_packet_extension(pkt, joystick_data->sender_boot_nonce, joystick_data->center_press_seq);

#if 0 
            ESP_LOGI("handle_running_screen", "Yaw: %d, Pitch: %d, Speed: %d, id: %u, Button: %u", 
                        yaw_angle, pitch_angle, speed_val, joystick_data->id, joystick_data->btnB_status);
#endif

            espnow_send_data(pkt, sizeof(pkt));
            sent_packet = true;
            last_center_seq = joystick_data->center_press_seq;
            last_packet_tick = xTaskGetTickCount();
            vTaskDelay(30 / portTICK_PERIOD_MS);
        } else {
            sent_packet = false;
            vTaskDelay(200 / portTICK_PERIOD_MS);
        }
    }
}

/**
 * @brief Task to handle joystick IMU screen functionality, processing IMU sensor data and sending ESP-NOW control
 * packets
 *
 * This function runs an infinite loop that reads IMU sensor data (accelerometer and gyroscope),
 * updates the IMU visualization screen, processes the angle data to control remote devices,
 * and sends ESP-NOW packets with the processed control information.
 * The function maps the IMU pitch and roll angles to yaw and pitch values for remote control,
 * applies filtering to reduce unnecessary transmissions, and sends control packets at regular intervals.
 *
 * @param pvParam Pointer to joystick data structure containing IMU sensor values, battery level,
 *                device ID, communication channel, and other control parameters
 * @details
 *      1. Continuously reads IMU data (acceleration and gyro values) from the joystick_data structure
 *      2. Updates the IMU screen visualization with current sensor values
 *      3. Limits roll values to range [-1.5, 1.5] and pitch values to range [0, 1.5]
 *      4. Maps limited angle values to appropriate yaw/pitch ranges for remote control (-1280 to 1280 for yaw, 900 to 0
 * for pitch)
 *      5. Only sends control packets when changes exceed threshold (10 units) to minimize network traffic
 *      6. Constructs and transmits ESP-NOW packet with device ID, yaw, pitch, speed, and button status
 *      7. Includes a 50ms delay between iterations when in IMU mode, 200ms otherwise
 */
void handle_imu_screen(void *pvParam)
{
    joystick_data_t *joystick_data = (joystick_data_t *)pvParam;

    static IMU_Angle_t last_imu_angle = {0.0f, 0.0f};

    // communicate packet
    uint8_t pkt[16] = {0};
    pkt[0]              = joystick_data->id;  // id: 0 for broadcast
    int16_t yaw_angle   = 0;
    int16_t pitch_angle = 0;
    mapped_target_filter_t accepted = {0};
    int16_t speed_val   = 600;
    bool sent_packet = false;
    uint32_t last_center_seq = 0;
    TickType_t last_packet_tick = 0;
    uint32_t observed_mode_generation = UINT32_MAX;

    while (1) {
        // update screen and send packet when in running mode
        if (joystick_data->screen_mode == MODE_IMU) {
            uint32_t generation;
            if (!joystick_read_center(joystick_data, MODE_IMU, &generation)) {
                vTaskDelay(1);
                continue;
            }
            if (generation != observed_mode_generation) {
                observed_mode_generation = generation;
                sent_packet = false;
                accepted.initialized = false;
            }
            IMU_Angle_t imu_angle =
                update_imu_screen(joystick_data->accel_x, joystick_data->accel_y, joystick_data->accel_z,
                                  joystick_data->bat, joystick_data->id, joystick_data->channel);

            // Limit the roll value to the range of -1.5 to 1.5
            float limited_roll = fmaxf(-1.5f, fminf(1.5f, imu_angle.roll));
            // Limit the pitch value to the range of 0 to 1.5
            float limited_pitch = fmaxf(0.0f, fminf(1.5f, imu_angle.pitch));

            yaw_angle   = (int16_t)map(limited_roll, -1.5, 1.5, -1280, 1280);
            pitch_angle = (int16_t)map(limited_pitch, 0, 1.5, 900, 0);

            const bool target_changed = mapped_target_accept(&accepted, yaw_angle, pitch_angle, 10);
            if (sent_packet && !target_changed &&
                pkt[7] == joystick_data->btnB_status && pkt[0] == joystick_data->id &&
                last_center_seq == joystick_data->center_press_seq &&
                xTaskGetTickCount() - last_packet_tick < pdMS_TO_TICKS(200)) {
                vTaskDelay(30 / portTICK_PERIOD_MS);
                continue;
            }
            pkt[0] = joystick_data->id;
            memcpy(&pkt[1], &accepted.yaw, sizeof(int16_t));
            memcpy(&pkt[3], &accepted.pitch, sizeof(int16_t));
            memcpy(&pkt[5], &speed_val, sizeof(int16_t));
            pkt[7] = joystick_data->btnB_status;
            center_packet_extension(pkt, joystick_data->sender_boot_nonce, joystick_data->center_press_seq);
            espnow_send_data(pkt, sizeof(pkt));
            sent_packet = true;
            last_center_seq = joystick_data->center_press_seq;
            last_packet_tick = xTaskGetTickCount();

#if 0 
            // ESP_LOGI("handle_imu_screen", "yaw_angle: %.2f, pitch_angle:%.2f, yaw: %d, pitch: %d\n", 
                                            imu_angle.roll, imu_angle.pitch, yaw_angle, pitch_angle);
#endif

            vTaskDelay(30 / portTICK_PERIOD_MS);
        } else {
            sent_packet = false;
            vTaskDelay(200 / portTICK_PERIOD_MS);
        }
    }
}
