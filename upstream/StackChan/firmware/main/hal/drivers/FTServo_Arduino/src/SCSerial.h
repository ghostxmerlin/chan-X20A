/*
 * SCSerial.h
 * 飞特串行舵机硬件接口层程序
 * 日期: 2024.11.22
 * 作者: txl
 */

#ifndef _SCSERIAL_H
#define _SCSERIAL_H

#include "driver/gpio.h"
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "SCS.h"
#include "SCSDiagnostics.h"

class SCSerial : public SCS
{
public:
	SCSerial();
	SCSerial(u8 End);
	SCSerial(u8 End, u8 Level);
	bool begin(uart_port_t uart_num, int baud_rate, int tx_pin, int rx_pin, int buf_size = 1024);
	void end();
	// SCSCL register categories, for two explicitly selected servo IDs only.
	// Call before begin() to enable the optional passive UART event queue.
	void enableDiagnostics(u8 first, u8 second);
	SCSDiagnostics getDiagnostics(u8 id) const;

protected:
	int writeSCS(unsigned char *nDat, int nLen) override;//输出nLen字节
	int readSCS(unsigned char *nDat, int nLen) override;//输入nLen字节
	int readSCS(unsigned char *nDat, int nLen, unsigned long TimeOut) override;
	int writeSCS(unsigned char bDat) override;//输出1字节
	void rFlushSCS() override;//
	void wFlushSCS() override;//
	void onTransactionStart(u8 id, u8 address, u8 instruction, u8 bytes, const u8* data) override;
	void onTransactionEnd() override;
	void onReadData(const u8* data, u8 bytes) override;
private:
	int diagnostic_ids[2]={-1,-1},diagnostic_slot=-1;
	SCSDiagnostics diagnostic_stats[2];
	SCSDiagnostics::Trace diagnostic_trace;
	uint64_t diagnostic_tx_started_us=0,diagnostic_tx_finished_us=0;
	bool diagnostic_torque_disable=false;
	static constexpr unsigned diagnostic_event_capacity=32;
	QueueHandle_t diagnostic_event_queue=nullptr;
	SCSDiagnostics::UartEvents diagnostic_uart_events;
	void drainDiagnosticEvents();
	void traceReadByte(u8 value);
	void traceWrite(int requested, int written, uint64_t started, uint64_t finished);
public:
	unsigned long IOTimeOut;//输入输出超时
	uart_port_t uart_num;//串口编号
};

#endif
