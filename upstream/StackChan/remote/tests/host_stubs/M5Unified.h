#pragma once
namespace m5 {
struct imu_data_t { struct { float x = 0, y = 0, z = 0; } accel; };
struct Button { bool pressed = false; bool wasPressed() { bool result = pressed; pressed = false; return result; } };
struct M5Stub {
    Button BtnA, BtnB;
    struct { void begin() {} struct { int getBatteryLevel() { return 80; } } Axp192; } Power;
    struct { void setBrightness(int) {} } Lcd;
    struct { long width() { return 135; } long height() { return 240; } } Display;
    struct { int getPort() { return 0; } } In_I2C;
    struct { void init(void *) {} void update() {} imu_data_t getImuData() { return {}; } } Imu;
    void begin() {} void update() {}
};
}
static m5::M5Stub M5;
