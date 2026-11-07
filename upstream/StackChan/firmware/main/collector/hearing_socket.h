/* SPDX-License-Identifier: MIT */
#pragma once
#include <functional>
#include <memory>
#include <string>
namespace collector::hearing {
// Single worker owns every connect/read/write/close. Abort only cancels and
// shuts down a published descriptor; no asynchronous WebSocket callbacks exist.
class HearingSocket {
public:
    HearingSocket();~HearingSocket();
    void SetHeader(const char*,const char*);
    bool Connect(const char*);
    bool IsConnected()const;
    bool Send(const std::string&);
    bool Send(const void*,size_t,bool binary);
    void Poll();
    void Abort();
    void OnData(std::function<void(const char*,size_t,bool)>);
    void OnDisconnected(std::function<void()>);
private:
    struct Impl;std::unique_ptr<Impl> impl_;
};
}
