/* SPDX-License-Identifier: MIT */
#pragma once
#include "types.h"
namespace collector::eyes {
class Backend;
// Backend must outlive the service. Only one background caller may invoke step.
bool initialize(Backend& backend);
void start(); // ESP-IDF board adapter starts the worker; no LVGL calls here.
void step();
SubmitResult submit(const Command& command,Source source);
Snapshot snapshot();
RequestResult query(const char* request_id);
void display_gate(bool home,bool display_on,bool reaction);
void voice_started(uint32_t job,int clip,uint32_t duration_ms);
void cancel_voice(uint32_t job);

// Internal display adapter only; pixels remain pinned until rendered().
struct DisplayFrame { const uint16_t* pixels=nullptr;int slot=-1;uint32_t generation=0,sequence=0;Style style=Style::Invalid;State state=State::Idle; };
DisplayFrame take_frame();
void rendered(bool showing);
bool show_bound_frame();
}
