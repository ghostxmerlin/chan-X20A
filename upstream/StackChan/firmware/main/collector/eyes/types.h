/* SPDX-License-Identifier: MIT */
#pragma once
#include "catalog.generated.h"
namespace collector::eyes {
enum class Operation { Configure, Play, Stop };
enum class Mode { Once, Loop };
enum class Source { None, Manual, Voice, Natural };
inline constexpr uint32_t kManualFirstFrameDeadlineMs=10000, kVoiceFirstFrameDeadlineMs=1500;
enum class Phase { Legacy, Loading, Idle, Playing, Suspended, Error };
enum class RequestStatus { Unknown, Accepted, Presenting, Completed, Cancelled, Expired, Failed };
enum class Error { None, InvalidArgument, Disabled, NotVisible, Busy, StorageUnavailable, NoMemory,
    AssetMissing, AssetInvalid, IoError, Expired, Superseded, PageHidden, Reaction, Stopped,
    VoiceEnded, RequestUnknown, IdConflict };
inline const char* name(Operation v) { return v==Operation::Configure?"configure":v==Operation::Play?"play":"stop"; }
inline const char* name(Mode v) { return v==Mode::Once?"once":"loop"; }
inline const char* name(Source v) { return v==Source::Manual?"manual":v==Source::Voice?"voice":v==Source::Natural?"natural":"none"; }
inline const char* name(Phase v) { const char* a[]={"legacy","loading","idle","playing","suspended","error"};return a[size_t(v)]; }
inline const char* name(RequestStatus v) { const char* a[]={"unknown","accepted","presenting","completed","cancelled","expired","failed"};return a[size_t(v)]; }
inline const char* name(Error v) {
    const char* a[]={"none","invalid_argument","disabled","not_visible","busy","storage_unavailable","no_memory",
        "asset_missing","asset_invalid","io_error","expired","superseded","page_hidden","reaction","stopped",
        "voice_ended","request_unknown","request_id_conflict"};return a[size_t(v)];
}
struct Command {
    char request_id[37]{};
    Operation op=Operation::Play;
    bool enabled=false;
    Style style=Style::V05;
    State state=State::Idle;
    Mode mode=Mode::Once;
    uint32_t loop_for_ms=0;
};
struct SubmitResult { bool accepted=false; uint32_t request_seq=0; Error error=Error::None; bool duplicate=false; };
struct RequestResult {
    char request_id[37]{}; uint32_t request_seq=0;
    Operation operation=Operation::Play;
    RequestStatus status=RequestStatus::Unknown;
    Error error=Error::None;
};
struct Stats {
    uint64_t load_us=0, decode_us=0, max_decode_us=0, max_lateness_us=0;
    uint32_t decoded=0, skipped=0, failures=0, allocated_bytes=0, internal_low_water_bytes=0;
};
struct Snapshot {
    bool supported=true, ready=false, enabled=false, storage_ready=false, memory_ready=false, visible=false, natural_cycle=false;
    Style requested_style=Style::V05, selected_style=Style::V05, active_style=Style::Invalid;
    State state=State::Idle; Mode mode=Mode::Once; Source source=Source::None;
    Phase phase=Phase::Legacy; Error suspended_reason=Error::None, last_error=Error::None;
    uint32_t generation=0, frame_index=0, submitted_seq=0, rendered_seq=0, last_error_seq=0, next_change_ms=0;
    RequestResult last_request{}; Stats stats{};
};
bool valid_request_id(const char* id);
bool valid_command(const Command& command);
} // namespace collector::eyes
