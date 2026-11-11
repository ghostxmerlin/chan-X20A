/* SPDX-License-Identifier: MIT */
#include "hearing_socket.h"
#include "hearing_policy.h"
#include "hearing_transport_policy.h"
#include <esp_transport.h>
#include <esp_transport_tcp.h>
#include <esp_transport_ssl.h>
#include <esp_transport_ws.h>
#include <esp_crt_bundle.h>
#include <esp_timer.h>
#include <esp_log.h>
#include <lwip/sockets.h>
#include <algorithm>
#include <mutex>

namespace collector::hearing {
struct HearingSocket::Impl {
    esp_transport_handle_t native=nullptr,bounded=nullptr,ws=nullptr;
    OperationDeadline deadline;
    std::mutex descriptor_mutex;int descriptor=-1;
    bool connected=false;
    std::string auth,error;
    MessageBuffer buffer;
    std::function<void(const char*,size_t,bool)> on_data;
    std::function<void()> on_disconnected;
    ~Impl(){close();if(ws)esp_transport_destroy(ws);if(bounded)esp_transport_destroy(bounded);if(native)esp_transport_destroy(native);}
    void close(){
        {std::lock_guard<std::mutex> lock(descriptor_mutex);descriptor=-1;if(native)esp_transport_close(native);}
        const bool notify=connected;connected=false;buffer.clear();if(notify&&on_disconnected)on_disconnected();
    }
    void abort(){deadline.cancel();std::lock_guard<std::mutex> lock(descriptor_mutex);if(descriptor>=0)shutdown(descriptor,SHUT_RDWR);}
    int budget(int requested)const{return std::min(requested,deadline.remaining(esp_timer_get_time()));}
    static Impl& self(esp_transport_handle_t handle){return *static_cast<Impl*>(esp_transport_get_context_data(handle));}
    bool connect(const char* uri) {
        if(!valid_url(uri)||deadline.cancelled)return false;
        const std::string url=uri;const bool tls=url.rfind("wss://",0)==0;const size_t start=tls?6:5;
        const size_t slash=url.find('/',start);const std::string authority=url.substr(start,slash==std::string::npos?slash:slash-start);
        const auto colon=authority.find(':');const std::string host=authority.substr(0,colon);
        const int port=colon==std::string::npos?(tls?443:80):std::stoi(authority.substr(colon+1));
        native=tls?esp_transport_ssl_init():esp_transport_tcp_init();bounded=esp_transport_init();
        if(!native||!bounded)return false;
        if(tls)esp_transport_ssl_crt_bundle_attach(native,esp_crt_bundle_attach);
        esp_transport_set_context_data(bounded,this);
        esp_transport_set_func(bounded,
            [](esp_transport_handle_t t,const char* host,int port,int timeout){
                auto& s=self(t);const int ms=s.budget(timeout);if(!ms)return -1;
                const int result=esp_transport_connect(s.native,host,port,ms);
                {std::lock_guard<std::mutex> lock(s.descriptor_mutex);s.descriptor=esp_transport_get_socket(s.native);
                    if(result>=0&&s.descriptor>=0){const int enabled=1;setsockopt(s.descriptor,IPPROTO_TCP,TCP_NODELAY,&enabled,sizeof(enabled));}}
                return s.deadline.cancelled?-1:result;
            },
            [](esp_transport_handle_t t,char* b,int len,int timeout){auto& s=self(t);const int ms=s.budget(timeout);return ms?esp_transport_read(s.native,b,len,ms):-1;},
            [](esp_transport_handle_t t,const char* b,int len,int timeout){auto& s=self(t);const int ms=s.budget(timeout);return ms?esp_transport_write(s.native,b,len,ms):-1;},
            [](esp_transport_handle_t t){auto& s=self(t);std::lock_guard<std::mutex> lock(s.descriptor_mutex);s.descriptor=-1;return esp_transport_close(s.native);},
            [](esp_transport_handle_t t,int timeout){auto& s=self(t);const int ms=s.deadline.poll_timeout(timeout,esp_timer_get_time());return ms>=0?esp_transport_poll_read(s.native,ms):0;},
            [](esp_transport_handle_t t,int timeout){auto& s=self(t);const int ms=s.deadline.poll_timeout(timeout,esp_timer_get_time());return ms>=0?esp_transport_poll_write(s.native,ms):0;},
            [](esp_transport_handle_t){return 0;});
        ws=esp_transport_ws_init(bounded);if(!ws)return false;
        esp_transport_ws_set_path(ws,slash==std::string::npos?"/":url.c_str()+slash);
        if(esp_transport_ws_set_auth(ws,auth.c_str())!=ESP_OK)return false;
        // Numeric endpoints avoid an uninterruptible DNS resolver. All native
        // connect, HTTP header reads and writes share this one absolute budget.
        deadline.begin(esp_timer_get_time(),2000);
        connected=esp_transport_connect(ws,host.c_str(),port,2000)>=0&&!deadline.cancelled;
        if(!connected){error="connect";close();}
        return connected;
    }
    bool send(const void* data,size_t size,bool binary) {
        if(!connected||deadline.cancelled||size>(binary?32768u:8192u))return false;   // binary: camera frames (vision uplink)
        // 1 s, not 200 ms: the uplink to the backend sees RTT spikes past 300 ms (Mac wired ping,
        // 2026-11-09), and lwIP stops reporting the socket writable at 8 queued segments.
        constexpr int kSendBudgetMs=1000;
        deadline.begin(esp_timer_get_time(),kSendBudgetMs);
        const auto opcode=static_cast<ws_transport_opcodes_t>((binary?WS_TRANSPORT_OPCODES_BINARY:WS_TRANSPORT_OPCODES_TEXT)|WS_TRANSPORT_OPCODES_FIN);
        const int result=esp_transport_ws_send_raw(ws,opcode,static_cast<const char*>(data),size,kSendBudgetMs);
        if(result!=static_cast<int>(size)||deadline.cancelled){error="send_"+std::to_string(result);close();return false;}return true;
    }
    void poll() {
        if(!connected)return;
        if(deadline.cancelled){close();return;}
        // An idle poll must not spend the frame-read budget or stall capture.
        // Once readable, allow the complete header/payload a shared 200 ms.
        char bytes[2048];deadline.begin(esp_timer_get_time(),200);
        const int available=esp_transport_poll_read(ws,0);
        if(available<0){error="poll";close();return;}
        if(!available)return;
        int n=esp_transport_read(ws,bytes,sizeof(bytes),200);
        if(n<0||deadline.cancelled){error="read_"+std::to_string(n);close();return;}
        if(!n)return;
        const auto result=buffer.add(esp_transport_ws_get_read_opcode(ws),esp_transport_ws_get_fin_flag(ws),
            esp_transport_ws_get_read_payload_len(ws),bytes,n);
        if(result==MessageBuffer::Invalid){error="frame_"+std::to_string(esp_transport_ws_get_read_opcode(ws))+"_"+std::to_string(esp_transport_ws_get_read_payload_len(ws));close();return;}
        if(result==MessageBuffer::Complete){
            // Callback executes synchronously on the worker and finishes before
            // any subsequent close/destruction; stop only sets cancellation.
            if(on_data)on_data(buffer.message.data(),buffer.message.size(),false);
            buffer.clear();
        }
    }
};
HearingSocket::HearingSocket():impl_(std::make_unique<Impl>()){
    // IDF 5.5.4 prints the entire credential-bearing upgrade request on one
    // write-error path. Keep this transport tag silent; expose generic status only.
    esp_log_level_set("transport_ws",ESP_LOG_NONE);
}
HearingSocket::~HearingSocket()=default;
void HearingSocket::SetHeader(const char*,const char* value){impl_->auth=value;}
bool HearingSocket::Connect(const char* uri){return impl_->connect(uri);}
bool HearingSocket::IsConnected()const{return impl_->connected&&!impl_->deadline.cancelled;}
bool HearingSocket::Send(const std::string& text){return impl_->send(text.data(),text.size(),false);}
bool HearingSocket::Send(const void* data,size_t size,bool binary){return impl_->send(data,size,binary);}
void HearingSocket::Poll(){impl_->poll();}
void HearingSocket::Abort(){impl_->abort();}
const std::string& HearingSocket::Error()const{return impl_->error;}
void HearingSocket::OnData(std::function<void(const char*,size_t,bool)> cb){impl_->on_data=std::move(cb);}
void HearingSocket::OnDisconnected(std::function<void()> cb){impl_->on_disconnected=std::move(cb);}
}
