/* SPDX-License-Identifier: MIT */
#include "SCSCL.h"
#include "soc/uart_reg.h"
#include "../upstream/StackChan/firmware/main/stackchan/motion/servo.h"
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <functional>
#include <vector>

// The production parser, byte-wise transport and timeout arithmetic are linked
// unchanged. Only IDF UART/timer/task APIs below are substituted.
namespace {
int64_t now_us=1000000;
struct Delivery {int64_t time;std::vector<uint8_t> bytes;};
std::vector<Delivery> pending;
std::deque<uint8_t> rx;
std::vector<uint8_t> tx;
std::vector<std::vector<uint8_t>> requests;
std::function<void(const std::vector<uint8_t>&)> respond;
unsigned write_calls=0,read_calls=0;
bool short_write=false;
int wait_error=0;
int timer_step_us=0,write_work_us=0;
std::deque<uart_event_t> uart_events;
unsigned event_capacity=0,event_polls=0,event_drops=0;
unsigned frame_intr_calls=0;
int frame_intr_error=0;
uint32_t enabled_intr_mask=0;
bool refill_events=false;
bool event(uart_event_type_t type){
    if(!event_capacity||uart_events.size()>=event_capacity){++event_drops;return false;}
    uart_events.push_back({type});return true;
}

std::vector<uint8_t> packet(uint8_t id,const std::vector<uint8_t>& payload,uint8_t status=0){
    std::vector<uint8_t> result={255,255,id,static_cast<uint8_t>(payload.size()+2),status};
    uint8_t sum=id+payload.size()+2+status;
    for(auto value:payload){result.push_back(value);sum+=value;}
    result.push_back(static_cast<uint8_t>(~sum));return result;
}
void deliver(std::vector<uint8_t> bytes,int64_t delay=200){pending.push_back({now_us+delay,std::move(bytes)});}
void update_rx(){
    std::stable_sort(pending.begin(),pending.end(),[](const Delivery& a,const Delivery& b){return a.time<b.time;});
    while(!pending.empty()&&pending.front().time<=now_us){
        for(auto byte:pending.front().bytes)rx.push_back(byte);
        pending.erase(pending.begin());
    }
}
void reset_wire(){rx.clear();tx.clear();pending.clear();requests.clear();respond=nullptr;short_write=false;wait_error=0;write_calls=read_calls=0;timer_step_us=write_work_us=0;uart_events.clear();refill_events=false;}
void good_reply(const std::vector<uint8_t>& request){
    if(request[4]==INST_READ){
        std::vector<uint8_t> value(request[6],0);
        if(request[5]==56&&request[6]>=2){value[0]=2;value[1]=205;} // raw 717, SCSCL big-endian
        deliver(packet(request[2],value));
    }else deliver(packet(request[2],{}));
}
}

int uart_driver_install(int,int,int,int size,QueueHandle_t* queue,int){
    event_capacity=size;uart_events.clear();enabled_intr_mask=0x2000;
    if(queue)*queue=size ? &uart_events : nullptr;return 0;
}
int uart_driver_delete(int){event_capacity=0;uart_events.clear();return 0;}
int uart_enable_intr_mask(int port,uint32_t mask){
    assert(port==UART_NUM_1&&mask==UART_FRM_ERR_INT_ENA_M);++frame_intr_calls;
    if(frame_intr_error==0)enabled_intr_mask|=mask;
    return frame_intr_error;
}
unsigned uxQueueMessagesWaiting(QueueHandle_t queue){assert(queue==&uart_events);return uart_events.size();}
int xQueueReceive(QueueHandle_t queue,void* output,unsigned wait){
    assert(queue==&uart_events&&wait==0);++event_polls;
    if(uart_events.empty())return pdFALSE;
    *static_cast<uart_event_t*>(output)=uart_events.front();uart_events.pop_front();
    if(refill_events)event(UART_DATA);
    return pdTRUE;
}
int uart_param_config(int,const uart_config_t*){return 0;}
int uart_set_pin(int,int,int,int,int){return 0;}
const char* esp_err_to_name(int){return "fake";}
int64_t esp_timer_get_time(){now_us+=timer_step_us;return now_us;}
void vTaskDelay(unsigned ticks){now_us+=ticks ? ticks*10000 : 100;}
int uart_get_buffered_data_len(int,size_t* count){update_rx();*count=rx.size();return 0;}
int uart_read_bytes(int,void* dest,size_t requested,int){
    ++read_calls;update_rx();size_t count=std::min(requested,rx.size());
    for(size_t i=0;i<count;i++){static_cast<uint8_t*>(dest)[i]=rx.front();rx.pop_front();}return count;
}
int uart_write_bytes(int,const void* data,size_t requested){
    ++write_calls;size_t size=short_write&&requested ? requested-1 : requested;
    const auto* bytes=static_cast<const uint8_t*>(data);tx.insert(tx.end(),bytes,bytes+size);
    if(tx.size()>=4&&tx.size()==static_cast<size_t>(tx[3])+4){
        requests.push_back(tx);if(respond)respond(tx);tx.clear();
    }
    now_us+=write_work_us;
    return size;
}
int uart_flush_input(int){update_rx();rx.clear();return 0;}
int uart_wait_tx_done(int,unsigned){return wait_error;}

int main(){
    // Factory/default mode allocates no event queue and performs no queue work.
    SCSCL plain;assert(plain.begin(UART_NUM_1,1000000,6,7)&&event_capacity==0);
    reset_wire();respond=good_reply;assert(plain.ReadPos(1)==717&&event_polls==0);
    assert(!plain.getDiagnostics(1).uart_events.enabled&&frame_intr_calls==0);plain.end();
    // A rejected diagnostic interrupt enable is visible, while communication
    // still works through the unchanged original UART configuration.
    SCSCL failed_enable;failed_enable.enableDiagnostics(1,2);frame_intr_error=0x102;
    assert(failed_enable.begin(UART_NUM_1,1000000,6,7)&&frame_intr_calls==1&&enabled_intr_mask==0x2000);
    const auto failed_events=failed_enable.getDiagnostics(1).uart_events;
    assert(failed_events.enabled&&!failed_events.frame_error_interrupt_enabled&&failed_events.frame_error_interrupt_error==0x102);
    reset_wire();respond=good_reply;assert(failed_enable.ReadPos(1)==717&&requests.size()==1);
    failed_enable.end();frame_intr_error=0;
    SCSCL bus;bus.enableDiagnostics(1,2);assert(bus.begin(UART_NUM_1,1000000,6,7)&&event_capacity==32);
    assert(frame_intr_calls==2&&enabled_intr_mask==(0x2000|UART_FRM_ERR_INT_ENA_M));
    assert(bus.getDiagnostics(1).uart_events.frame_error_interrupt_enabled&&bus.getDiagnostics(1).uart_events.frame_error_interrupt_error==0);
    reset_wire();respond=good_reply;
    assert(bus.ReadPos(1)==717);auto d=bus.getDiagnostics(1);
    assert(d.generation==1&&d.position_read.attempts==1&&d.position_read.ok==1);
    assert(d.last.valid&&d.last.status_valid&&d.last.raw_valid&&d.last.raw_value==717);
    assert(d.last.id==1&&d.last.address==56&&d.last.requested_bytes==2&&d.last.instruction==INST_READ);
    assert(d.last.rx_bytes==8&&d.last.rx_prefix_size==8&&d.last.rx_prefix[5]==2&&d.last.rx_prefix[6]==205);
    assert(d.last.tx_bytes==8&&d.last.tx_calls==3&&d.last.duration_us>0&&!d.last_error.valid);
    const std::vector<uint8_t> expected_request={255,255,1,4,2,56,2,static_cast<uint8_t>(~65)};
    assert(requests.back()==expected_request); // unchanged original wire packet
    auto count=write_calls;auto reads=read_calls;auto clock=now_us;
    for(int i=0;i<100;i++)assert(bus.getDiagnostics(1).generation==1);
    assert(write_calls==count&&read_calls==reads&&now_us==clock); // getters never poll hardware
    assert(bus.ReadPos(2)==717&&bus.getDiagnostics(2).generation==1);

    respond=[](const auto& q){deliver(packet(q[2]==1?2:1,{2,205}));};
    assert(bus.ReadPos(1)==-1&&bus.getLastError()==ERR_SLAVE_ID);
    d=bus.getDiagnostics(1);assert(d.position_read.wrong_id==1&&d.last_error.transport_error==ERR_SLAVE_ID);
    assert(!d.last_error.raw_valid&&!d.last_error.status_valid&&d.last_error.rx_prefix[2]==2);
    const auto error_time=d.last_error.timestamp_us;
    respond=good_reply;assert(bus.ReadPos(1)==717);
    assert(bus.getDiagnostics(1).last_error.timestamp_us==error_time);
    assert(bus.getDiagnostics(1).last.discarded_before==3); // rejected frame's unread payload/checksum

    respond=[](const auto& q){deliver(packet(q[2],{0}));};
    assert(bus.ReadPos(1)==-1&&bus.getLastError()==ERR_BUFF_LEN);
    respond=[](const auto& q){auto bytes=packet(q[2],{2,205});bytes.back()^=1;deliver(bytes);};
    assert(bus.ReadPos(1)==-1&&bus.getLastError()==ERR_CRC_CMP);
    respond=nullptr;assert(bus.ReadPos(1)==-1&&bus.getLastError()==ERR_NO_REPLY);
    d=bus.getDiagnostics(1);assert(d.position_read.bad_length==1&&d.position_read.bad_checksum==1&&d.position_read.no_reply==1);
    assert(d.last_error.rx_bytes==0&&!d.last_error.status_valid);
    respond=[](const auto& q){deliver(packet(q[2],{2,205},32));};
    assert(bus.ReadPos(1)==717&&bus.getState()==32); // protocol still returns raw data; HAL rejects nonzero status
    d=bus.getDiagnostics(1);assert(d.position_read.servo_error==1&&d.last_error.servo_status==32&&d.last_error.status_valid);

    reset_wire();respond=good_reply;
    assert(bus.ReadMove(1)==0);assert(bus.EnableTorque(1,0)==1);
    d=bus.getDiagnostics(1);assert(d.moving_read.ok==1&&d.torque_disable.ok==1&&d.last.instruction==INST_WRITE);
    assert(d.last.address==40&&d.last.requested_bytes==1&&d.last.rx_bytes==6);
    assert(bus.WritePos(1,700,20,0)==1);
    assert(requests.back()[5]==42&&requests.back()[6]==2&&requests.back()[7]==188);
    assert(bus.getDiagnostics(1).torque_disable.attempts==1); // position write is not a release ack

    // Torque enable occupies only register 40. Serve the requested slice of a
    // register map so the old readWord implementation fails the off assertion:
    // it interpreted disabled=0 plus unrelated register 41=7 as enabled (>0).
    uint8_t torque_register=0, adjacent_register=7;
    respond=[&](const auto& q){
        assert(q[4]==INST_READ&&q[5]==40&&q[6]>=1&&q[6]<=2);
        std::vector<uint8_t> registers={torque_register,adjacent_register};
        registers.resize(q[6]);deliver(packet(q[2],registers));
    };
    auto requests_before=requests.size();
    assert(bus.ReadToqueEnable(1)==0);
    assert(requests.size()==requests_before+1); // no added query or retry
    const std::vector<uint8_t> torque_request={255,255,1,4,2,40,1,207};
    assert(requests.back()==torque_request);
    d=bus.getDiagnostics(1);assert(d.torque_read.ok==1&&d.last.requested_bytes==1&&d.last.raw_value==0);
    torque_register=1;adjacent_register=255;
    assert(bus.ReadToqueEnable(1)==1);
    torque_register=0;adjacent_register=0;
    assert(bus.ReadToqueEnable(1)==0);
    respond=nullptr;assert(bus.ReadToqueEnable(1)==-1&&bus.getLastError()==ERR_NO_REPLY);
    respond=[](const auto&){deliver(packet(2,{0}));};
    assert(bus.ReadToqueEnable(1)==-1&&bus.getLastError()==ERR_SLAVE_ID);
    respond=[](const auto& q){deliver(packet(q[2],{0,7}));};
    assert(bus.ReadToqueEnable(1)==-1&&bus.getLastError()==ERR_BUFF_LEN);
    respond=[](const auto& q){auto bytes=packet(q[2],{0});bytes.back()^=1;deliver(bytes);};
    assert(bus.ReadToqueEnable(1)==-1&&bus.getLastError()==ERR_CRC_CMP);
    // The write/ACK path is unchanged by this register-width correction.
    respond=good_reply;assert(bus.EnableTorque(1,0)==1);
    assert(bus.getDiagnostics(1).torque_disable.ok==2);

    reset_wire();respond=[](const auto& q){deliver(packet(q[2],std::vector<uint8_t>(q[6],0)),4000);};
    bus.IOTimeOut=2;
    assert(bus.ReadPos(1)==-1&&bus.getLastError()==ERR_NO_REPLY);
    // The old response arrives AFTER the next transaction flush and BEFORE
    // its actual answer. Existing parser returns a wrong-ID failure; there is
    // no claim that this synthetic response delay equals real hardware delay.
    respond=[](const auto& q){deliver(packet(q[2],{2,205}),2000);};
    assert(bus.ReadPos(2)==-1&&bus.getLastError()==ERR_SLAVE_ID);
    assert(bus.getDiagnostics(2).last_error.rx_prefix[2]==1);
    reset_wire();respond=[](const auto& q){deliver(packet(q[2],{2,205}),4000);};
    bus.IOTimeOut=10;assert(bus.ReadPos(1)==717); // same delay is within the original default timeout

    reset_wire();respond=[](const auto& q){
        auto bytes=packet(q[2],{2,205});
        for(size_t i=0;i<bytes.size();i++)deliver({bytes[i]},200+i*1900);
    };
    bus.IOTimeOut=2;assert(bus.ReadPos(1)==717);
    assert(bus.getDiagnostics(1).last.duration_us>=13500); // 2ms is an inter-byte timeout, not whole-transaction deadline
    bus.IOTimeOut=10;

    reset_wire();respond=good_reply;wait_error=0x107;
    assert(bus.ReadPos(1)==717); // transport API behavior remains unchanged
    d=bus.getDiagnostics(1);assert(d.last.tx_wait_error==0x107&&d.all.tx_error==1&&d.last_error.tx_wait_error==0x107);
    reset_wire();short_write=true;assert(bus.ReadPos(1)==-1);
    d=bus.getDiagnostics(1);assert(d.last.tx_short_writes==3&&d.all.tx_error==2&&d.last_error.tx_short_writes==3);

    reset_wire();respond=[](const auto& q){auto bytes=packet(q[2],std::vector<uint8_t>(15,0));bytes.insert(bytes.begin(),9,0);deliver(bytes);};
    assert(bus.FeedBack(2)==15);
    d=bus.getDiagnostics(2);assert(d.last.rx_bytes==30&&d.last.rx_prefix_size==24&&!d.last.raw_valid);
    assert(d.last_error.transport_error==ERR_SLAVE_ID); // success never wipes preserved failure
    reset_wire();respond=good_reply;timer_step_us=10;write_work_us=20;
    assert(bus.ReadPos(1)==717);d=bus.getDiagnostics(1);
    // Three 30us API calls (20us work plus timed return), separated by two
    // 10us scheduling gaps. RX wait contributes only to total duration.
    assert(d.last.tx_submit_span_us==110&&d.last.tx_max_gap_us==10);
    assert(d.last.duration_us>d.last.tx_submit_span_us&&d.last.tx_calls==3);
    reset_wire();respond=good_reply;
    assert(bus.WritePos(1,700,20,0)==1);const auto position=bus.getDiagnostics(1).last_position_write;
    assert(position.valid&&position.status_valid&&!position.raw_valid&&position.tx_data_prefix_size==6);
    const uint8_t goal_payload[]={2,188,0,20,0,0};
    assert(std::equal(position.tx_data_prefix,position.tx_data_prefix+6,goal_payload));
    assert(bus.ReadMove(1)==0&&bus.EnableTorque(1,0)==1);const auto disabled=bus.getDiagnostics(1).last_torque_disable;
    assert(disabled.valid&&disabled.timestamp_us>position.timestamp_us&&disabled.status_valid&&!disabled.raw_valid);
    assert(disabled.tx_data_prefix_size==1&&disabled.tx_data_prefix[0]==0);
    assert(bus.ReadMove(1)==0);d=bus.getDiagnostics(1);
    assert(d.last.address==66&&d.last.tx_data_prefix_size==0&&d.last.raw_valid);
    assert(d.last_position_write.timestamp_us==position.timestamp_us&&d.last_torque_disable.timestamp_us==disabled.timestamp_us);
    // Failed position writes are retained as attempted commands, never measured values.
    respond=nullptr;assert(bus.WritePos(1,701,20,0)==0);d=bus.getDiagnostics(1);
    assert(d.last_position_write.timestamp_us>position.timestamp_us&&!d.last_position_write.status_valid&&!d.last_position_write.raw_valid);
    assert(d.last_position_write.tx_data_prefix[1]==189&&d.last_torque_disable.timestamp_us==disabled.timestamp_us);
    assert(bus.EnableTorque(1,0)==0);d=bus.getDiagnostics(1);
    assert(d.last_torque_disable.timestamp_us>disabled.timestamp_us&&!d.last_torque_disable.status_valid&&!d.last_torque_disable.raw_valid);
    assert(d.last_torque_disable.tx_data_prefix_size==1&&d.last_torque_disable.tx_data_prefix[0]==0);
    uint8_t long_payload[10]={1,2,3,4,5,6,7,8,9,10};respond=good_reply;
    assert(bus.genWrite(1,42,long_payload,sizeof(long_payload))==1);d=bus.getDiagnostics(1);
    assert(d.last_position_write.requested_bytes==10&&d.last_position_write.tx_data_prefix_size==6&&d.last_position_write.tx_data_prefix[5]==6);

    reset_wire();respond=[](const auto& q){event(UART_BUFFER_FULL);event(UART_BREAK);good_reply(q);};
    assert(event(UART_FRAME_ERR)&&event(UART_PARITY_ERR)&&event(UART_FIFO_OVF)&&event(UART_DATA));
    const auto before_events=bus.getDiagnostics(1).uart_events;
    assert(bus.ReadPos(1)==717);d=bus.getDiagnostics(1);const auto& events=d.uart_events;
    assert(requests.size()==1&&write_calls==3&&read_calls==8); // no new wire operations
    assert(events.enabled&&events.queue_capacity==32&&!events.counts_complete&&events.events_drained==before_events.events_drained+6);
    assert(events.frame_errors==1&&events.parity_errors==1&&events.fifo_overflows==1&&events.buffer_full==1&&events.breaks==1);
    assert(events.last_poll_timestamp_us>0&&uart_events.empty());
    assert(bus.getDiagnostics(2).uart_events.events_drained==events.events_drained); // shared bus, not per-axis attribution
    assert(event(UART_FRAME_ERR));const auto polled=event_polls;const auto stamp=now_us;
    for(unsigned i=0;i<20;++i)assert(bus.getDiagnostics(1).uart_events.frame_errors==1);
    assert(event_polls==polled&&now_us==stamp&&uart_events.size()==1); // snapshot getters never drain
    respond=good_reply;assert(bus.ReadPos(2)==717&&bus.getDiagnostics(1).uart_events.frame_errors==2);

    reset_wire();respond=good_reply;const auto lower_bound=bus.getDiagnostics(1).uart_events;
    for(unsigned i=0;i<32;++i)assert(event(UART_DATA));
    assert(!event(UART_FRAME_ERR)&&event_drops==1); // a real error may be silently absent from received events
    assert(bus.ReadPos(1)==717);d=bus.getDiagnostics(1);
    assert(d.uart_events.frame_errors==lower_bound.frame_errors&&!d.uart_events.counts_complete);
    assert(d.uart_events.queue_full_observations==lower_bound.queue_full_observations+1);
    assert(d.uart_events.events_drained==lower_bound.events_drained+32);
    reset_wire();respond=good_reply;for(unsigned i=0;i<32;++i)assert(event(UART_DATA));
    refill_events=true;const auto bounded_polls=event_polls;assert(bus.ReadPos(1)==717);
    assert(event_polls-bounded_polls==64); // both transaction boundaries are bounded despite continuous events
    refill_events=false;

    // Execute the same one-shot register query used by the HAL, through the
    // actual parser. Distinct bytes must not be decoded as a big-endian word.
    using stackchan::motion::readServoDeadbandSnapshot;
    static_assert(!stackchan::motion::ServoDeadbandSnapshot{}.supported);
    reset_wire();bus.IOTimeOut=7;
    respond=[](const auto& q){deliver(packet(q[2],{8,10}));};
    auto deadband=readServoDeadbandSnapshot(bus,1,now_us);
    assert(deadband.supported&&deadband.attempted&&deadband.valid&&deadband.timestamp_us>0);
    assert(deadband.cw_raw==8&&deadband.ccw_raw==10&&deadband.transport_error==0&&deadband.servo_status==0);
    assert(bus.IOTimeOut==7&&requests.size()==1);
    const std::vector<uint8_t> deadband_request={255,255,1,4,2,26,2,220};
    assert(requests.back()==deadband_request); // one READ, no EEPROM unlock/write
    // Valid zero settings remain distinguishable from a failed read.
    respond=[](const auto& q){deliver(packet(q[2],{0,0}));};
    deadband=readServoDeadbandSnapshot(bus,2,now_us);
    assert(deadband.valid&&deadband.cw_raw==0&&deadband.ccw_raw==0&&requests.back()[2]==2);
    for(int failure=0;failure<5;++failure){
        reset_wire();
        respond=[failure](const auto& q){
            if(failure==0)return;
            if(failure==1){deliver(packet(2,{8,10}));return;}
            if(failure==2){deliver(packet(q[2],{8}));return;}
            auto bytes=packet(q[2],{8,10},failure==4?32:0);
            if(failure==3)bytes.back()^=1;
            deliver(bytes);
        };
        deadband=readServoDeadbandSnapshot(bus,1,now_us);
        assert(deadband.supported&&deadband.attempted&&!deadband.valid);
        assert(deadband.cw_raw==-1&&deadband.ccw_raw==-1&&requests.size()==1&&bus.IOTimeOut==7);
        const int expected_errors[]={ERR_NO_REPLY,ERR_SLAVE_ID,ERR_BUFF_LEN,ERR_CRC_CMP,0};
        assert(deadband.transport_error==expected_errors[failure]);
        if(failure==4)assert(deadband.servo_status==32);
    }
    bus.end();assert(!bus.getDiagnostics(1).uart_events.enabled);
    std::puts("PASS: actual FTServo UART/parser: single-byte torque read; preserved position/release commands and bounded payloads; passive bus events, possible event loss and bounded zero-wait drains; default mode has no queue; unchanged wire requests/timeouts and reproduced late-response failure");
}
