/* SPDX-License-Identifier: MIT */
#pragma once
// SD assets over /mouth (collector-0.4.96): the ear pushes one catalog file at a time on the live /mouth link, so
// the font parts (and the eyes / voice files) can be installed without pulling the card and without the robot's
// HTTP server (not started in this build). Protocol: docs/collector-protocol.md "0.4.96".
//
//   ear  -> robot  {"type":"asset.begin","id":N,"part":"cjk16.0","bytes":262144,"sha256":"<64 hex>"}
//   robot -> ear   {"type":"asset.ack","id":N,"bytes":0,"window":32768}         ready (or asset.result unchanged)
//   ear  -> robot  binary "AST1" | u32 id | u32 offset | payload (<= 1588 B)     at most `window` bytes unacked
//   robot -> ear   {"type":"asset.ack","id":N,"bytes":received,"window":32768}  every 8 KiB and at the end
//   ear  -> robot  {"type":"asset.end","id":N}                                   (or {"type":"asset.abort","id":N})
//   robot -> ear   {"type":"asset.result","id":N,"part":"..","ok":true|false,"unchanged":bool,"bytes":N[,"reason":".."]}
//
// The robot takes only what its own catalog names: the part id resolves to a firmware path, size and SHA-256 (the
// same whitelist as POST /v1/assets) and begin's bytes / sha256 must equal them, so the server can never pick a
// path or a size. One transfer at a time. The file is collected in one PSRAM buffer of exactly the catalog size;
// the SD work (an "unchanged" check first, then .part + read-back verify + rename) runs on the asset install worker
// (eyes/http.cpp), never on the /mouth task (SD I/O takes the LVGL lock and drains LCD DMA).
//
// Pure logic, no FreeRTOS: driven by tests/mouth_assets_test.cpp. mouth_assets.cpp owns one Transfer in PSRAM
// behind its own mutex (never the audio or the mode lock) and does the socket sends and the worker hand-off.
#include <ArduinoJson.hpp>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace collector::mouth::assets {
constexpr size_t kHeader = 12;                 // "AST1" | u32 id | u32 offset, little endian
constexpr size_t kMessageMax = 1600;           // = mouth_stream.h kMaxMessage: the largest /mouth message
constexpr size_t kChunkMax = kMessageMax - kHeader;
constexpr size_t kWindow = 32 * 1024;          // bytes the ear may have in flight past the last ack
constexpr size_t kAckEvery = 8 * 1024;
constexpr size_t kMaxBytes = 256 * 1024;       // the largest catalog file (font part / eyes asset)
constexpr int64_t kIdleUs = 20LL * 1000000;    // no chunk or end for 20 s (while hello is quiet): timeout
constexpr int64_t kTotalUs = 300LL * 1000000;  // a whole transfer, pauses for speech included
constexpr size_t kPartMax = 31;                // longest part id ("eyes.v05.affectionate" is 21)
constexpr size_t kLineMax = 200;

inline uint32_t le32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
// An asset chunk ("AST1"), never confusable with a TTS packet ("SPK1", mouth_stream.h).
inline bool is_chunk(const uint8_t* d, size_t n) { return d && n >= kHeader && !std::memcmp(d, "AST1", 4); }

// What the firmware catalog says about one part. font: a ui_font part (reload the font after it lands).
struct Spec { const char* path = nullptr; size_t bytes = 0; const uint8_t* sha256 = nullptr; bool font = false; };

// Part ids are short ASCII words: [a-z0-9._-], 1..kPartMax. Anything else is never looked up (nor echoed).
inline bool part_word(const char* s)
{
    if (!s || !*s) return false;
    size_t n = 0;
    for (; s[n]; ++n) {
        const char c = s[n];
        if (n >= kPartMax || !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) return false;
    }
    return true;
}

inline bool hex_digest(const char* s, uint8_t out[32])
{
    if (!s || std::strlen(s) != 64) return false;
    for (int i = 0; i < 64; ++i) {
        const char c = s[i];
        const int v = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (v < 0) return false;
        if (i % 2) out[i / 2] = uint8_t(out[i / 2] | v); else out[i / 2] = uint8_t(v << 4);
    }
    return true;
}

struct Line {
    char text[kLineMax] = {};
    size_t n = 0;
    const char* c_str() const { return text; }
    size_t size() const { return n; }
};

// The worker's hand-off: what to do with which file. Check: is the file on SD already this one? Install: write it.
enum class Job : uint8_t { None, Check, Install };
struct JobView { Job job = Job::None; Spec spec{}; const uint8_t* bytes = nullptr; size_t length = 0; };

struct Hooks {
    bool (*resolve)(const char* part, Spec& out) = nullptr;
    void* (*alloc)(size_t bytes) = nullptr;
    void (*release)(void* p) = nullptr;
};

class Transfer {
public:
    enum class Phase : uint8_t { Idle, Checking, Receiving, Installing };
    explicit Transfer(const Hooks& h) : hooks_(h) {}
    Transfer(const Transfer&) = delete;
    Transfer& operator=(const Transfer&) = delete;

    // asset.begin. storage: the SD card is mounted.
    void begin(ArduinoJson::JsonObjectConst m, int64_t now_us, bool storage)
    {
        const uint32_t id = m["id"].is<uint32_t>() ? m["id"].as<uint32_t>() : 0;
        const char* part = m["part"] | "";
        const char* echo = part_word(part) ? part : "";
        if (!id) { ++stats_.refused; result(0, echo, false, false, 0, "bad_message"); return; }
        if (phase_ != Phase::Idle || running_ != Job::None) { ++stats_.refused; result(id, echo, false, false, 0, "busy"); return; }
        Spec spec;
        if (!*echo || !hooks_.resolve || !hooks_.resolve(part, spec) || !spec.path || !spec.sha256) {
            ++stats_.refused; result(id, echo, false, false, 0, "unknown_part"); return;
        }
        const size_t bytes = m["bytes"].is<uint32_t>() ? m["bytes"].as<uint32_t>() : 0;
        uint8_t digest[32];
        const char* reason = nullptr;
        if (spec.bytes == 0 || spec.bytes > kMaxBytes) reason = "too_large";
        else if (bytes != spec.bytes) reason = "size_mismatch";
        else if (!hex_digest(m["sha256"] | "", digest) || std::memcmp(digest, spec.sha256, 32)) reason = "sha256_mismatch";
        else if (!storage) reason = "storage_unavailable";
        if (reason) { ++stats_.refused; result(id, echo, false, false, bytes, reason); return; }
        uint8_t* buf = hooks_.alloc ? static_cast<uint8_t*>(hooks_.alloc(spec.bytes)) : nullptr;
        if (!buf) { ++stats_.refused; result(id, echo, false, false, bytes, "no_memory"); return; }
        id_ = id; spec_ = spec; buf_ = buf; received_ = acked_ = 0;
        std::snprintf(part_, sizeof part_, "%s", part);
        began_us_ = last_us_ = now_us;
        phase_ = Phase::Checking; pending_ = Job::Check;
        ++stats_.begun;
    }

    // One binary "AST1" message. Chunks of an aborted or finished transfer are dropped quietly.
    void chunk(const uint8_t* d, size_t n, int64_t now_us)
    {
        if (!is_chunk(d, n)) return;
        const uint32_t id = le32(d + 4), offset = le32(d + 8);
        if (phase_ != Phase::Receiving || id != id_) { ++stats_.stray; return; }
        const size_t len = n - kHeader;
        if (offset != received_) { fail("out_of_order"); return; }
        if (len > spec_.bytes - received_) { fail("too_long"); return; }
        std::memcpy(buf_ + received_, d + kHeader, len);
        received_ += len; last_us_ = now_us;
        if (received_ - acked_ >= kAckEvery || received_ == spec_.bytes) ack_ = true;
    }

    void end(ArduinoJson::JsonObjectConst m, int64_t now_us)
    {
        if (!matches(m) || phase_ != Phase::Receiving) return;
        if (received_ != spec_.bytes) { fail("incomplete"); return; }
        last_us_ = now_us; phase_ = Phase::Installing; pending_ = Job::Install; ack_ = false;
    }

    // asset.abort: a check or a receive stops at once; a write already on the worker finishes and reports.
    void abort(ArduinoJson::JsonObjectConst m)
    {
        if (!matches(m) || phase_ == Phase::Idle || phase_ == Phase::Installing) return;
        ++stats_.aborted;
        fail("aborted");
    }

    // Each pass of the /mouth loop. speaking: a /mouth stream is playing (the ear holds the chunks meanwhile).
    void tick(int64_t now_us, bool speaking)
    {
        if (phase_ != Phase::Receiving) return;
        if (speaking) last_us_ = now_us;
        if (now_us - last_us_ > kIdleUs || now_us - began_us_ > kTotalUs) fail("timeout");
    }

    // The link dropped: nothing more can be sent about this transfer. A job on the worker keeps the buffer until it
    // returns (job_done frees it); begin answers busy until then.
    void link_down()
    {
        ack_ = false; lines_n_ = 0;
        if (phase_ != Phase::Idle) { ++stats_.dropped; std::snprintf(last_reason_, sizeof last_reason_, "link_down"); }
        cleanup();
    }

    // The /mouth task: a job to hand to the worker (once). The view stays valid until job_done.
    Job take_job(JobView& v)
    {
        if (pending_ == Job::None) return Job::None;
        v.job = pending_; v.spec = spec_; v.bytes = buf_; v.length = spec_.bytes;
        running_ = pending_; pending_ = Job::None;
        return v.job;
    }
    // The worker would not take it (an HTTP upload holds it).
    void job_refused() { running_ = Job::None; fail("installer_busy"); }

    // The worker finished. Check: flag = the file on SD already matches. Install: flag = unchanged. Returns true when
    // a font part is now on SD (the caller reloads the font).
    bool job_done(const char* error, bool flag, int64_t now_us)
    {
        const Job job = running_;
        running_ = Job::None;
        if (job == Job::None) return false;
        if (abandoned_) { abandoned_ = false; release(); return false; }
        const bool font = spec_.font && !error;
        if (error) { fail(error); return false; }
        if (job == Job::Check) {
            if (flag) { finish(true); return font; }
            phase_ = Phase::Receiving; last_us_ = now_us; ack_ = true;
            return false;
        }
        finish(flag);
        return font;
    }

    // Next line to send: the ack first (only the latest counts), then results in order.
    bool next(Line& out)
    {
        if (ack_) {
            ack_ = false; acked_ = received_;
            out.n = size_t(std::snprintf(out.text, sizeof out.text, "{\"type\":\"asset.ack\",\"id\":%lu,\"bytes\":%lu,\"window\":%lu}",
                                         (unsigned long)id_, (unsigned long)received_, (unsigned long)kWindow));
            return true;
        }
        if (!lines_n_) return false;
        out = lines_[0];
        for (size_t i = 1; i < lines_n_; ++i) lines_[i - 1] = lines_[i];
        --lines_n_;
        return true;
    }

    Phase phase() const { return phase_; }
    bool busy() const { return phase_ != Phase::Idle || running_ != Job::None; }
    size_t received() const { return received_; }
    struct Stats { uint32_t begun = 0, installed = 0, unchanged = 0, failed = 0, refused = 0, aborted = 0, dropped = 0, stray = 0; };
    const Stats& stats() const { return stats_; }

    void json(ArduinoJson::JsonObject o) const
    {
        static constexpr const char* kPhases[] = {"idle", "checking", "receiving", "installing"};
        o["phase"] = kPhases[size_t(phase_)];
        if (phase_ != Phase::Idle) { o["id"] = id_; o["part"] = part_; o["received"] = received_; o["bytes"] = spec_.bytes; }
        o["worker_busy"] = running_ != Job::None;
        o["begun"] = stats_.begun; o["installed"] = stats_.installed; o["unchanged"] = stats_.unchanged;
        o["failed"] = stats_.failed; o["refused"] = stats_.refused; o["aborted"] = stats_.aborted;
        o["dropped"] = stats_.dropped; o["stray"] = stats_.stray;
        if (*last_reason_) o["last_reason"] = last_reason_;
        if (*last_part_) o["last_part"] = last_part_;
    }

private:
    bool matches(ArduinoJson::JsonObjectConst m) const { return m["id"].is<uint32_t>() && m["id"].as<uint32_t>() == id_ && id_; }

    void result(uint32_t id, const char* part, bool ok, bool unchanged, size_t bytes, const char* reason)
    {
        if (!ok) std::snprintf(last_reason_, sizeof last_reason_, "%s", reason ? reason : "error");
        if (*part) std::snprintf(last_part_, sizeof last_part_, "%s", part);
        if (lines_n_ == kLines) {                    // never more than one transfer's worth: drop the oldest
            for (size_t i = 1; i < lines_n_; ++i) lines_[i - 1] = lines_[i];
            --lines_n_;
        }
        Line& l = lines_[lines_n_++];
        int n = std::snprintf(l.text, sizeof l.text,
                              "{\"type\":\"asset.result\",\"id\":%lu,\"part\":\"%s\",\"ok\":%s,\"unchanged\":%s,\"bytes\":%lu",
                              (unsigned long)id, part, ok ? "true" : "false", unchanged ? "true" : "false", (unsigned long)bytes);
        if (!ok && n > 0 && size_t(n) < sizeof l.text)
            n += std::snprintf(l.text + n, sizeof l.text - size_t(n), ",\"reason\":\"%s\"", reason ? reason : "error");
        if (n > 0 && size_t(n) < sizeof l.text - 1) { l.text[n++] = '}'; l.text[n] = 0; }
        l.n = std::strlen(l.text);
    }

    void finish(bool unchanged)
    {
        if (unchanged) ++stats_.unchanged; else ++stats_.installed;
        result(id_, part_, true, unchanged, spec_.bytes, nullptr);
        cleanup();
    }

    void fail(const char* reason)
    {
        ++stats_.failed;
        ack_ = false;
        result(id_, part_, false, false, spec_.bytes, reason);
        cleanup();
    }

    void release() { if (buf_ && hooks_.release) hooks_.release(buf_); buf_ = nullptr; }

    void cleanup()
    {
        pending_ = Job::None;
        if (running_ != Job::None) abandoned_ = true;   // the worker still reads the buffer: job_done frees it
        else release();
        phase_ = Phase::Idle; id_ = 0; received_ = acked_ = 0; part_[0] = 0;
    }

    static constexpr size_t kLines = 4;
    Hooks hooks_;
    Phase phase_ = Phase::Idle;
    Job pending_ = Job::None, running_ = Job::None;
    bool abandoned_ = false, ack_ = false;
    uint32_t id_ = 0;
    char part_[kPartMax + 1] = {};
    Spec spec_{};
    uint8_t* buf_ = nullptr;
    size_t received_ = 0, acked_ = 0;
    int64_t began_us_ = 0, last_us_ = 0;
    Line lines_[kLines];
    size_t lines_n_ = 0;
    char last_reason_[24] = {}, last_part_[kPartMax + 1] = {};
    Stats stats_;
};
}
