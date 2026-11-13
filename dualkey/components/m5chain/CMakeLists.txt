# Subset of M5Stack's ESP-IDF port of M5Chain, copied unchanged from m5stack/M5DualKey-UserDemo
# (components/m5chain, MIT, commit 6f8e3cca). Only the common bus code and the Chain Joystick are built.
idf_component_register(
    SRC_DIRS "src/ChainCommon" "src/ChainJoystick"
    INCLUDE_DIRS "src"
    REQUIRES driver esp_timer freertos)
