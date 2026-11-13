/*
 * SCSerial.cpp
 * 飞特串行舵机硬件接口层程序
 * 日期: 2024.4.2
 * 作者:
 */

#include "SCSerial.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "soc/uart_reg.h"

static const char *TAG = "FT_SCSerial";

SCSerial::SCSerial()
{
	IOTimeOut = 10;
	uart_num = UART_NUM_MAX;
}

SCSerial::SCSerial(u8 End):SCS(End)
{
	IOTimeOut = 10;
	uart_num = UART_NUM_MAX;
}

SCSerial::SCSerial(u8 End, u8 Level):SCS(End, Level)
{
	IOTimeOut = 10;
	uart_num = UART_NUM_MAX;
}

bool SCSerial::begin(uart_port_t uart_num, int baud_rate, int tx_pin, int rx_pin, int buf_size)
{
	this->uart_num = uart_num;

	uart_config_t uart_config = {
		.baud_rate = baud_rate,
		.data_bits = UART_DATA_8_BITS,
		.parity = UART_PARITY_DISABLE,
		.stop_bits = UART_STOP_BITS_1,
		.flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
		.rx_flow_ctrl_thresh = 0,
		.source_clk = UART_SCLK_DEFAULT,
	};

	const bool events_enabled = diagnostic_ids[0]>=0 || diagnostic_ids[1]>=0;
	diagnostic_event_queue=nullptr;
	diagnostic_uart_events={};
	esp_err_t ret = uart_driver_install(uart_num, buf_size, buf_size,
		events_enabled ? diagnostic_event_capacity : 0,
		events_enabled ? &diagnostic_event_queue : nullptr, 0);
	if (ret != ESP_OK) {
		ESP_LOGE(TAG, "Failed to install UART driver: %s", esp_err_to_name(ret));
		diagnostic_event_queue=nullptr;
		return false;
	}

	ret = uart_param_config(uart_num, &uart_config);
	if (ret != ESP_OK) {
		ESP_LOGE(TAG, "Failed to configure UART: %s", esp_err_to_name(ret));
		uart_driver_delete(uart_num);
		diagnostic_event_queue=nullptr;
		this->uart_num = UART_NUM_MAX;
		return false;
	}

	ret = uart_set_pin(uart_num, tx_pin, rx_pin, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
	if (ret != ESP_OK) {
		ESP_LOGE(TAG, "Failed to set UART pins: %s", esp_err_to_name(ret));
		uart_driver_delete(uart_num);
		diagnostic_event_queue=nullptr;
		this->uart_num = UART_NUM_MAX;
		return false;
	}

	diagnostic_uart_events.enabled=diagnostic_event_queue!=nullptr;
	diagnostic_uart_events.queue_capacity=diagnostic_event_queue ? diagnostic_event_capacity : 0;
	if (diagnostic_event_queue) {
		// Add only the missing frame-error source, preserving the driver's other
		// interrupts, format and timeouts. Event collection remains best effort.
		const auto error=uart_enable_intr_mask(uart_num,UART_FRM_ERR_INT_ENA_M);
		diagnostic_uart_events.frame_error_interrupt_error=error;
		diagnostic_uart_events.frame_error_interrupt_enabled=error==ESP_OK;
	}
	return true;
}

void SCSerial::end()
{
	if (uart_num < UART_NUM_MAX) {
		uart_driver_delete(uart_num);
		uart_num = UART_NUM_MAX;
	}
	diagnostic_event_queue=nullptr;
	diagnostic_uart_events.enabled=false;
	diagnostic_uart_events.frame_error_interrupt_enabled=false;
}

namespace {
// Ticks to block for the remaining milliseconds: at least one tick, so the
// caller always yields, and never past the caller's own deadline by more than a tick.
TickType_t read_wait_ticks(int64_t left_ms)
{
	if (left_ms <= 0) return 1;
	const TickType_t ticks = pdMS_TO_TICKS(static_cast<uint32_t>(left_ms));
	return ticks > 0 ? ticks : 1;
}
}

int SCSerial::readSCS(unsigned char *nDat, int nLen, unsigned long TimeOut)
{
	if (uart_num >= UART_NUM_MAX) {
		return 0;
	}

	int size = 0;
	int64_t t_begin = esp_timer_get_time() / 1000;

	while (1) {
		// Block on the UART instead of polling: pdMS_TO_TICKS(1) is 0 at 100 Hz,
		// so the former vTaskDelay never yielded and a silent bus spun its core
		// (2026-11-13 white screen). The driver wakes this task when a byte lands.
		const int64_t left = static_cast<int64_t>(TimeOut) - ((esp_timer_get_time() / 1000) - t_begin);
		unsigned char byte;
		const int len = uart_read_bytes(uart_num, &byte, 1, read_wait_ticks(left));
		if (len > 0) {
			traceReadByte(byte);
			if (nDat) {
				nDat[size] = byte;
			}
			size++;
		}

		if (size >= nLen) {
			break;
		}

		if ((esp_timer_get_time() / 1000) - t_begin > static_cast<int64_t>(TimeOut)) {
			break;
		}
	}

	return size;
}

int SCSerial::readSCS(unsigned char *nDat, int nLen)
{
	if (uart_num >= UART_NUM_MAX) {
		return 0;
	}

	int size = 0;
	int64_t t_begin = esp_timer_get_time() / 1000;

	while (1) {
		// Same blocking wait as above; the timeout restarts after every byte.
		const int64_t left = static_cast<int64_t>(IOTimeOut) - ((esp_timer_get_time() / 1000) - t_begin);
		unsigned char byte;
		const int len = uart_read_bytes(uart_num, &byte, 1, read_wait_ticks(left));
		if (len > 0) {
			traceReadByte(byte);
			if (nDat) {
				nDat[size] = byte;
			}
			size++;
			t_begin = esp_timer_get_time() / 1000;
		}

		if (size >= nLen) {
			break;
		}

		if ((esp_timer_get_time() / 1000) - t_begin > static_cast<int64_t>(IOTimeOut)) {
			break;
		}
	}

	return size;
}

int SCSerial::writeSCS(unsigned char *nDat, int nLen)
{
	if (uart_num >= UART_NUM_MAX || nDat == NULL) {
		return 0;
	}

	const uint64_t started = diagnostic_slot >= 0 ? esp_timer_get_time() : 0;
	int written = uart_write_bytes(uart_num, reinterpret_cast<const char *>(nDat), nLen);
	if (diagnostic_slot >= 0) traceWrite(nLen, written, started, esp_timer_get_time());
	return written;
}

int SCSerial::writeSCS(unsigned char bDat)
{
	if (uart_num >= UART_NUM_MAX) {
		return 0;
	}

	const uint64_t started = diagnostic_slot >= 0 ? esp_timer_get_time() : 0;
	int written = uart_write_bytes(uart_num, reinterpret_cast<const char *>(&bDat), 1);
	if (diagnostic_slot >= 0) traceWrite(1, written, started, esp_timer_get_time());
	return written;
}

void SCSerial::rFlushSCS()
{
	if (uart_num >= UART_NUM_MAX) {
		return;
	}

	if (diagnostic_slot >= 0) {
		size_t available = 0;
		if (uart_get_buffered_data_len(uart_num, &available) == ESP_OK)
			diagnostic_trace.discarded_before = available;
	}
	uart_flush_input(uart_num);
}

void SCSerial::wFlushSCS()
{
	if (uart_num >= UART_NUM_MAX) {
		return;
	}

	const auto error = uart_wait_tx_done(uart_num, pdMS_TO_TICKS(100));
	if (diagnostic_slot >= 0) diagnostic_trace.tx_wait_error = error;
}

void SCSerial::enableDiagnostics(u8 first, u8 second)
{
	diagnostic_ids[0]=first;diagnostic_ids[1]=second;
	diagnostic_stats[0]={};diagnostic_stats[1]={};diagnostic_slot=-1;
}

SCSDiagnostics SCSerial::getDiagnostics(u8 id) const
{
	for (int i=0;i<2;i++) if (diagnostic_ids[i]==id) {
		auto result=diagnostic_stats[i];
		result.uart_events=diagnostic_uart_events;
		return result;
	}
	return {};
}

void SCSerial::drainDiagnosticEvents()
{
	if (!diagnostic_event_queue) return;
	if (uxQueueMessagesWaiting(diagnostic_event_queue)>=diagnostic_event_capacity)
		++diagnostic_uart_events.queue_full_observations;
	uart_event_t event;
	// Bound work even under continuously arriving noise. Zero wait; no UART
	// read/flush/send, and no event is attributed to the currently selected axis.
	for (unsigned i=0;i<diagnostic_event_capacity &&
		xQueueReceive(diagnostic_event_queue,&event,0)==pdTRUE;++i) {
		++diagnostic_uart_events.events_drained;
		switch (event.type) {
			case UART_FRAME_ERR: ++diagnostic_uart_events.frame_errors; break;
			case UART_PARITY_ERR: ++diagnostic_uart_events.parity_errors; break;
			case UART_FIFO_OVF: ++diagnostic_uart_events.fifo_overflows; break;
			case UART_BUFFER_FULL: ++diagnostic_uart_events.buffer_full; break;
			case UART_BREAK: ++diagnostic_uart_events.breaks; break;
			default: break;
		}
	}
	diagnostic_uart_events.last_poll_timestamp_us=esp_timer_get_time();
}

void SCSerial::onTransactionStart(u8 id, u8 address, u8 instruction, u8 bytes, const u8* data)
{
	drainDiagnosticEvents();
	diagnostic_slot=-1;
	for (int i=0;i<2;i++) if (diagnostic_ids[i]==id) diagnostic_slot=i;
	if (diagnostic_slot < 0) return;
	diagnostic_trace={};
	diagnostic_trace.valid=true;
	diagnostic_trace.timestamp_us=esp_timer_get_time();
	diagnostic_trace.id=id;diagnostic_trace.address=address;
	diagnostic_trace.instruction=instruction;diagnostic_trace.requested_bytes=bytes;
	if (data) {
		diagnostic_trace.tx_data_prefix_size=bytes<sizeof(diagnostic_trace.tx_data_prefix) ? bytes : sizeof(diagnostic_trace.tx_data_prefix);
		for (unsigned i=0;i<diagnostic_trace.tx_data_prefix_size;++i) diagnostic_trace.tx_data_prefix[i]=data[i];
	}
	diagnostic_torque_disable=instruction==INST_WRITE && address==40 && bytes==1 && data && data[0]==0;
}

void SCSerial::traceReadByte(u8 value)
{
	if (diagnostic_slot < 0) return;
	auto& trace=diagnostic_trace;
	if (trace.rx_prefix_size < sizeof(trace.rx_prefix)) trace.rx_prefix[trace.rx_prefix_size++]=value;
	++trace.rx_bytes;
}

void SCSerial::traceWrite(int requested, int written, uint64_t started, uint64_t finished)
{
	if (diagnostic_slot < 0) return;
	if (!diagnostic_trace.tx_calls) diagnostic_tx_started_us=started;
	else {
		const uint64_t gap=started-diagnostic_tx_finished_us;
		if (gap>diagnostic_trace.tx_max_gap_us) diagnostic_trace.tx_max_gap_us=gap;
	}
	diagnostic_tx_finished_us=finished;
	diagnostic_trace.tx_submit_span_us=finished-diagnostic_tx_started_us;
	++diagnostic_trace.tx_calls;
	if (written > 0) diagnostic_trace.tx_bytes+=written;
	if (written!=requested) ++diagnostic_trace.tx_short_writes;
}

void SCSerial::onReadData(const u8* data, u8 bytes)
{
	if (diagnostic_slot < 0 || (bytes!=1 && bytes!=2)) return;
	diagnostic_trace.raw_valid=true;
	diagnostic_trace.raw_value=bytes==1 ? data[0] : SCS2Host(data[0],data[1]);
}

void SCSerial::onTransactionEnd()
{
	if (diagnostic_slot < 0) { drainDiagnosticEvents(); return; }
	auto& trace=diagnostic_trace;
	trace.duration_us=esp_timer_get_time()-trace.timestamp_us;
	trace.transport_error=u8Error;
	trace.status_valid=u8Error==0;
	if (trace.status_valid) trace.servo_status=u8Status;
	const bool tx_error=trace.tx_short_writes>0 || trace.tx_wait_error!=0;
	const bool ok=u8Error==0 && u8Status==0 && !tx_error;
	auto count=[&](SCSDiagnostics::Counter& stats) {
		++stats.attempts;
		if (ok) ++stats.ok;
		if (u8Error==ERR_NO_REPLY) ++stats.no_reply;
		if (u8Error==ERR_CRC_CMP) ++stats.bad_checksum;
		if (u8Error==ERR_SLAVE_ID) ++stats.wrong_id;
		if (u8Error==ERR_BUFF_LEN) ++stats.bad_length;
		if (trace.status_valid && u8Status!=0) ++stats.servo_error;
		if (tx_error) ++stats.tx_error;
	};
	auto& stats=diagnostic_stats[diagnostic_slot];
	++stats.generation;count(stats.all);
	// These byte addresses are the SCSCL map used by the StackChan HAL.
	if (trace.instruction==INST_READ) {
		if (trace.address==56 && trace.requested_bytes==2) count(stats.position_read);
		else if (trace.address==66 && trace.requested_bytes==1) count(stats.moving_read);
		else if (trace.address==40) count(stats.torque_read);
	}
	if (diagnostic_torque_disable) count(stats.torque_disable);
	stats.last=trace;
	if (!ok) stats.last_error=trace;
	if (trace.instruction==INST_WRITE && trace.address==42) stats.last_position_write=trace;
	if (diagnostic_torque_disable) stats.last_torque_disable=trace;
	diagnostic_slot=-1;
	drainDiagnosticEvents();
}
