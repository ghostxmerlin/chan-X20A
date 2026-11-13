"""Compile the actual FTServo parser/transport with a deterministic, device-free UART."""
import pathlib
import subprocess
import tempfile
import unittest


class ServoDiagnosticsTest(unittest.TestCase):
    def test_actual_ftservo_transactions(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        source = root / "upstream/StackChan/firmware/main/hal/drivers/FTServo_Arduino/src"
        with tempfile.TemporaryDirectory(prefix="chan-servo-diagnostics-") as folder:
            temporary = pathlib.Path(folder)
            (temporary / "driver").mkdir()
            (temporary / "freertos").mkdir()
            (temporary / "soc").mkdir()
            # The public UART driver header does not expose the HAL interrupt
            # aliases. Production and test code must include this SoC header.
            (temporary / "soc/uart_reg.h").write_text("#pragma once\n#define UART_FRM_ERR_INT_ENA_M (1U << 3)\n")
            (temporary / "driver/gpio.h").write_text("#pragma once\n")
            (temporary / "driver/uart.h").write_text("""#pragma once
#include <cstddef>
#include <cstdint>
#include "freertos/FreeRTOS.h"
using esp_err_t=int;
using uart_port_t=int;
constexpr int ESP_OK=0, UART_NUM_1=1,UART_NUM_MAX=3,UART_DATA_8_BITS=8,UART_PARITY_DISABLE=0,
 UART_STOP_BITS_1=1,UART_HW_FLOWCTRL_DISABLE=0,UART_SCLK_DEFAULT=0,UART_PIN_NO_CHANGE=-1;
struct uart_config_t {int baud_rate,data_bits,parity,stop_bits,flow_ctrl,rx_flow_ctrl_thresh,source_clk;};
enum uart_event_type_t {UART_DATA,UART_FRAME_ERR,UART_PARITY_ERR,UART_FIFO_OVF,UART_BUFFER_FULL,UART_BREAK};
struct uart_event_t {uart_event_type_t type;size_t size=0;bool timeout_flag=false;};
int uart_driver_install(int,int,int,int,QueueHandle_t*,int);
int uart_driver_delete(int);
int uart_enable_intr_mask(int,uint32_t);
int uart_param_config(int,const uart_config_t*);
int uart_set_pin(int,int,int,int,int);
int uart_get_buffered_data_len(int,size_t*);
int uart_read_bytes(int,void*,size_t,int);
int uart_write_bytes(int,const void*,size_t);
int uart_flush_input(int);
int uart_wait_tx_done(int,unsigned);
const char* esp_err_to_name(int);
""")
            (temporary / "freertos/FreeRTOS.h").write_text("#pragma once\nusing QueueHandle_t=void*;\nusing TickType_t=unsigned;\nconstexpr int pdTRUE=1,pdFALSE=0;\n#define pdMS_TO_TICKS(ms) ((ms)/10)\n")
            (temporary / "freertos/task.h").write_text("#pragma once\nvoid vTaskDelay(unsigned);\n")
            (temporary / "freertos/queue.h").write_text("#pragma once\n#include \"FreeRTOS.h\"\nunsigned uxQueueMessagesWaiting(QueueHandle_t);\nint xQueueReceive(QueueHandle_t,void*,unsigned);\n")
            (temporary / "esp_timer.h").write_text("#pragma once\n#include <cstdint>\nint64_t esp_timer_get_time();\n")
            (temporary / "esp_log.h").write_text("#pragma once\n#define ESP_LOGE(tag,...) ((void)(tag))\n")
            binary=temporary / "servo-diagnostics"
            subprocess.run(["c++","-std=gnu++20","-O1","-Wall","-Wextra",
                "-I"+str(temporary),"-I"+str(source),
                "-I"+str(root / "upstream/StackChan/firmware/components/smooth_ui_toolkit/src"),
                str(root / "tests/servo_diagnostics_test.cpp"),str(source / "SCS.cpp"),
                str(source / "SCSerial.cpp"),str(source / "SCSCL.cpp"),"-o",str(binary)],check=True,cwd=root)
            subprocess.run([str(binary)],check=True,cwd=root)


if __name__ == "__main__":
    unittest.main()
