/* SPDX-License-Identifier: MIT */
#pragma once
#include "types.h"
#include <array>
namespace collector::eyes {
struct FramePlan { Style style; State state; uint32_t generation; uint16_t frame; int64_t due_us; };
// Pure state machine; the service owns its mutex and supplies monotonic time.
class Player {
public:
    void set_resources(bool storage,bool memory);
    void seed_random(uint32_t seed) { random_=seed?seed:0x6d2b79f5; }
    void set_idle_ready(bool ready) { value_.ready=ready && value_.memory_ready; }
    void set_gate(bool allowed,Error reason,int64_t now);
    // Natural cycle pool: bit i = State(i). 0 keeps the original weighted 29-state cycle.
    void set_natural_pool(uint32_t mask) { natural_pool_=mask; }
    // false: a voice cue never enables the eyes by itself (normal mode keeps the home page).
    void set_voice_auto_enable(bool on) { auto_enable_=on; }
    SubmitResult submit(const Command&,Source,int64_t now,uint32_t voice_job=0);
    void loaded(uint32_t generation,int64_t now);
    void failed(uint32_t generation,Error error,int64_t now);
    void idle_failed(int64_t now);
    bool presented(uint32_t generation,uint16_t frame,uint32_t sequence,int64_t now);
    void cancel_voice(uint32_t job,int64_t now);
    void tick(int64_t now);
    Snapshot snapshot() const { return value_; }
    RequestResult query(const char* id,int64_t now) const;
    bool gate() const { return gate_; }
    bool configuring() const;
    bool loading() const { return current_>=0 && !loaded_; }
    bool can_decode(int64_t now) const {
        return now>=decode_after_ && !(natural_active_ && natural_frames_>=natural_rounds_*8);
    }
    FramePlan frame_plan(int64_t now,int bound_frame=-1) const;
private:
    struct Record { Command command{}; RequestResult result{}; int64_t at=0; Source source=Source::None; uint32_t voice_job=0; };
    std::array<Record,16> history_{};
    size_t next_=0; int current_=-1; uint32_t sequence_=0;
    Snapshot value_{};
    bool gate_=false, auto_enable_=true, loaded_=false, natural_active_=false;
    uint8_t natural_rounds_=0, natural_frames_=0;
    uint32_t natural_pool_=0;
    State last_natural_=State::Invalid;
    uint32_t random_=0x6d2b79f5;
    int64_t first_deadline_=0, began_=0, idle_began_=0;
    int64_t natural_due_=0, natural_frame_due_=0, natural_after_=0, decode_after_=0;
    bool active() const { return current_>=0 || natural_active_; }
    uint32_t random_below(uint32_t limit);
    void begin_natural(int64_t now);
    void update_countdown(int64_t now);
    void finish(RequestStatus,Error,int64_t now);
    void update_result(Record&,RequestStatus,Error);
};
}
