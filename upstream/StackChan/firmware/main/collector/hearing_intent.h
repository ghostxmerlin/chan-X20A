/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>

namespace collector::hearing {
struct ListeningIntent {bool requested;bool enrolling;uint32_t revision;};
enum class OwnerStart {Started,AlreadyListening,Unavailable,Superseded};

// All access is serialized by hearing.cpp's mutex. Intent is independent of
// transport sessions: reconnecting must neither claim nor release listening.
class ListeningControl {
public:
    ListeningIntent snapshot() const {return {requested_,enrolling_,revision_};}
    bool requested() const {return requested_;}
    bool enrolling() const {return enrolling_;}
    uint32_t generation() const {return generation_;}
    void next_session() {advance(generation_);}

    bool set_listening(bool enabled,bool available) {
        invalidate_owner();
        if(enabled&&!available)return false;
        requested_=enabled;
        if(!enabled)enrolling_=false;
        return true;
    }
    bool toggle_listening(bool available) {
        return set_listening(!(requested_||enrolling_),available);
    }
    OwnerStart start_for_owner(uint32_t expected_revision,bool available,uint32_t& owner_revision) {
        owner_revision=0;
        if(expected_revision!=revision_)return OwnerStart::Superseded;
        if(!available||enrolling_)return OwnerStart::Unavailable;
        if(requested_)return OwnerStart::AlreadyListening;
        requested_=true;
        advance(revision_);
        owner_revision_=owner_revision=revision_;
        return OwnerStart::Started;
    }
    bool stop_for_owner(uint32_t owner_revision) {
        if(!owner_revision||owner_revision_!=owner_revision||revision_!=owner_revision||enrolling_)return false;
        requested_=false;
        invalidate_owner();
        return true;
    }
    void configure() {
        invalidate_owner();
        requested_=enrolling_=false;
    }
    void begin_enrollment() {invalidate_owner();enrolling_=true;}
    void finish_enrollment() {enrolling_=false;}
    void cancel_enrollment() {invalidate_owner();enrolling_=false;}

private:
    static void advance(uint32_t& value) {if(++value==0)++value;}
    void invalidate_owner() {advance(revision_);owner_revision_=0;}
    bool requested_=false,enrolling_=false;
    uint32_t revision_=1,generation_=1,owner_revision_=0;
};
}
