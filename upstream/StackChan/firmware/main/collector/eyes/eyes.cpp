/* SPDX-License-Identifier: MIT */
#include "eyes.h"
#include "loader.h"
#include "player.h"
#include "frame_pool.h"
#include <algorithm>
#include <cstdio>
#include <mutex>
#include <new>

namespace collector::eyes {
namespace {
struct Runtime {
    Backend& backend;std::mutex mutex;Player player;FramePool pool;Cache cache[2];
    uint16_t* pixels[2]{};uint8_t* scratch=nullptr;int idle_slot=0;
    Style frame_style[2]{Style::Invalid,Style::Invalid};State frame_state[2]{State::Idle,State::Idle};
    Stats stats{};
    explicit Runtime(Backend& b):backend(b) {}
    void sync() { player.tick(backend.now_us());pool.cancel(player.snapshot().generation); }
    bool visible() const {
        const auto s=player.snapshot();const int bound=pool.bound();
        // An accepted replacement loads behind the previous complete image.
        // Cancel/page/off transitions do not get this exception, so they cannot
        // resurrect the previous expression when the page is shown again.
        return s.enabled && player.gate() && bound>=0 &&
            (pool.slot(bound).generation==s.generation || s.phase==Phase::Loading);
    }
};
Runtime* runtime=nullptr;
Error load(Runtime& r,int slot,const Asset& a) {
    const auto began=r.backend.now_us();const auto result=r.cache[slot].load(r.backend,a);
    std::lock_guard<std::mutex> lock(r.mutex);r.stats.load_us+=r.backend.now_us()-began;return result;
}
}
bool initialize(Backend& backend) {
    if (runtime) return false;
    runtime=new(std::nothrow)Runtime(backend);if (!runtime) return false;
    auto& r=*runtime;bool memory=backend.available();
    if (memory) {
        for (auto& c:r.cache) { c.bytes=static_cast<uint8_t*>(backend.allocate(kAssetCapacity));memory=memory && c.bytes; }
        for (auto& p:r.pixels) { p=static_cast<uint16_t*>(backend.allocate(kPixels*2));memory=memory && p; }
        r.scratch=static_cast<uint8_t*>(backend.allocate(4096));memory=memory && r.scratch;
    }
    if (!memory) {
        for (auto& c:r.cache) {backend.release(c.bytes);c.bytes=nullptr;}
        for (auto& p:r.pixels) {backend.release(p);p=nullptr;}
        backend.release(r.scratch);r.scratch=nullptr;
    } else r.stats.allocated_bytes=2*kAssetCapacity+4*kPixels+4096;
    r.player.set_resources(backend.available(),memory);return memory;
}
void startup_failed() {
    if(!runtime)return;
    auto& r=*runtime;std::lock_guard<std::mutex> lock(r.mutex);
    // This hook is only called if the worker could not be created, before any
    // step/display binding. It must never free buffers referenced by LVGL.
    if(r.pool.submitted())return;
    for(auto& c:r.cache) {r.backend.release(c.bytes);c.bytes=nullptr;c.metadata=nullptr;}
    for(auto& p:r.pixels) {r.backend.release(p);p=nullptr;}
    r.backend.release(r.scratch);r.scratch=nullptr;r.stats.allocated_bytes=0;
    r.player.set_resources(r.backend.available(),false);
    r.player.failed(r.player.snapshot().generation,Error::NoMemory,r.backend.now_us());r.player.idle_failed();r.sync();
}
SubmitResult submit(const Command& command,Source source) {
    if (!runtime) return {false,0,Error::NoMemory};
    auto& r=*runtime;
    std::lock_guard<std::mutex> lock(r.mutex);
    r.player.set_resources(r.backend.available(),r.scratch!=nullptr);
    auto result=r.player.submit(command,source,r.backend.now_us());r.sync();return result;
}
Snapshot snapshot() {
    if (!runtime) return {};
    auto& r=*runtime;std::lock_guard<std::mutex> lock(r.mutex);r.sync();
    auto s=r.player.snapshot();s.stats=r.stats;s.rendered_seq=r.pool.rendered_sequence();
    const int bound=r.pool.bound();s.active_style=s.enabled && bound>=0?r.frame_style[bound]:Style::Invalid;
    s.visible=r.visible();
    s.storage_ready=r.backend.available();return s;
}
RequestResult query(const char* id) {
    if (!runtime) {RequestResult r;r.error=Error::RequestUnknown;return r;}
    auto& r=*runtime;std::lock_guard<std::mutex> lock(r.mutex);r.sync();return r.player.query(id,r.backend.now_us());
}
void display_gate(bool home,bool display_on,bool reaction) {
    if (!runtime) return;
    auto& r=*runtime;std::lock_guard<std::mutex> lock(r.mutex);
    r.player.set_gate(home&&display_on&&!reaction,reaction?Error::Reaction:!home||!display_on?Error::PageHidden:Error::None,r.backend.now_us());r.sync();
}
void voice_started(uint32_t job,int clip,uint32_t duration) {
    if (!runtime || clip<1 || clip>40) return;
    auto& r=*runtime;Command c;
    std::snprintf(c.request_id,sizeof(c.request_id),"feedface-0000-4000-8000-%012lx",static_cast<unsigned long>(job));
    c.state=voice_state(clip);c.mode=Mode::Loop;c.loop_for_ms=std::clamp<uint32_t>(duration,1000,30000);
    std::lock_guard<std::mutex> lock(r.mutex);r.player.set_resources(r.backend.available(),r.scratch!=nullptr);
    r.player.submit(c,Source::Voice,r.backend.now_us(),job);r.sync();
}
void cancel_voice(uint32_t job) {
    if (!runtime) return;
    auto& r=*runtime;std::lock_guard<std::mutex> lock(r.mutex);
    r.player.cancel_voice(job,r.backend.now_us());r.sync();
}
void step() {
    if (!runtime) return;
    auto& r=*runtime;
    FramePlan plan{};int output=-1;bool configuring=false;
    {
        std::lock_guard<std::mutex> lock(r.mutex);r.sync();auto s=r.player.snapshot();
        r.stats.internal_low_water_bytes=r.backend.internal_low_water();
        if (!s.memory_ready) return;
        configuring=r.player.configuring();
        if (!r.player.gate() && !configuring) return;
        if (!s.enabled && s.phase!=Phase::Loading) return;
        // Only one unpublished frame is useful; do not decode past it.
        for (int i=0;i<2;++i) if (r.pool.slot(i).life==FramePool::Life::Ready) return;
        const int bound=r.pool.bound();
        const int previous=bound>=0 && r.pool.slot(bound).generation==s.generation?r.pool.slot(bound).frame:-1;
        plan=r.player.frame_plan(r.backend.now_us(),previous);output=r.pool.begin(plan.generation);
        if (output<0) return;
    }
    Error error=Error::None;int idle=r.idle_slot,effect=1-r.idle_slot;
    const Asset* required_idle=asset(plan.style,State::Idle);
    if (r.cache[idle].metadata!=required_idle) {
        error=load(r,effect,*required_idle);
        if (error==Error::None) {
            // Validate a complete first frame before changing applied style.
            error=r.cache[effect].decode(0,r.pixels[output],r.scratch);
            if (error==Error::None) std::swap(idle,effect);
        }
    }
    const int source=plan.state==State::Idle?idle:effect;
    if (error==Error::None) error=load(r,source,*asset(plan.style,plan.state));
    const auto began=r.backend.now_us();
    if (error==Error::None) error=r.cache[source].decode(plan.frame,r.pixels[output],r.scratch);
    const auto elapsed=r.backend.now_us()-began;
    std::lock_guard<std::mutex> lock(r.mutex);r.sync();
    if (plan.generation!=r.player.snapshot().generation) {r.pool.abandon(output);return;}
    if (error!=Error::None) {
        ++r.stats.failures;r.pool.abandon(output);r.player.failed(plan.generation,error,r.backend.now_us());
        if (r.cache[r.idle_slot].metadata!=asset(r.player.snapshot().selected_style,State::Idle) || (source==r.idle_slot && plan.style==r.player.snapshot().selected_style && plan.state==State::Idle)) r.player.idle_failed();
        r.sync();return;
    }
    r.idle_slot=idle;r.player.set_idle_ready(true);r.player.loaded(plan.generation,r.backend.now_us());r.sync();
    r.stats.decode_us+=elapsed;r.stats.max_decode_us=std::max<uint64_t>(r.stats.max_decode_us,elapsed);++r.stats.decoded;
    if (plan.generation!=r.player.snapshot().generation) {r.pool.abandon(output);return;}
    r.frame_style[output]=plan.style;r.frame_state[output]=plan.state;
    r.pool.publish(output,plan.generation,plan.frame,plan.due_us);
    (void)configuring;
}
DisplayFrame take_frame() {
    if (!runtime) return {};
    auto& r=*runtime;std::lock_guard<std::mutex> lock(r.mutex);r.sync();
    const auto s=r.player.snapshot();if (!s.enabled || !r.player.gate()) return {};
    const auto now=r.backend.now_us();int ready=r.pool.ready(s.generation,now);if (ready<0) return {};
    const auto desired=r.player.frame_plan(now);
    const auto slot=r.pool.slot(ready);
    if (slot.due_us && desired.frame!=slot.frame) {r.pool.abandon(ready);++r.stats.skipped;return {};}
    if (!r.player.presented(s.generation,slot.frame,r.pool.submitted()+1,now)) {r.pool.abandon(ready);r.sync();return {};}
    const int index=r.pool.take(s.generation,now);
    if (slot.due_us) r.stats.max_lateness_us=std::max<uint64_t>(r.stats.max_lateness_us,now-slot.due_us);
    return {r.pixels[index],index,s.generation,r.pool.submitted(),r.frame_style[index],r.frame_state[index]};
}
void rendered(bool showing) {
    if (!runtime) return;
    auto& r=*runtime;std::lock_guard<std::mutex> lock(r.mutex);r.pool.rendered(showing);
}
bool show_bound_frame() {
    if (!runtime) return false;
    auto& r=*runtime;std::lock_guard<std::mutex> lock(r.mutex);
    return r.visible();
}
}
