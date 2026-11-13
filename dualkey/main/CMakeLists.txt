idf_component_register(
    SRCS "main.cpp" "config.cpp" "console.cpp" "net.cpp" "inputs.cpp" "leds.cpp"
    INCLUDE_DIRS "."
    REQUIRES m5chain nvs_flash esp_wifi esp_netif esp_event esp_timer json driver esp_driver_usb_serial_jtag
             esp_driver_gpio bootloader_support mbedtls)
