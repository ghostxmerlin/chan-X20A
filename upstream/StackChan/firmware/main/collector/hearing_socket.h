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
    // true when a frame (or part of one) was read; call again until false to drain.
    bool Poll();
    // /mouth (0.4.77): also take binary messages up to max_bytes; hearing stays text only.
    void AcceptBinary(size_t max_bytes);
    // Budget to finish one readable frame (default 200 ms: hearing must not stall capture).
    void SetReadBudgetMs(int ms);
    void Abort();
    const std::string& Error() const;
    void OnData(std::function<void(const char*,size_t,bool)>);
    void OnDisconnected(std::function<void()>);
private:
    struct Impl;std::unique_ptr<Impl> impl_;
};
}
