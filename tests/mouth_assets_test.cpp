// Host test for collector/mouth_assets.h (0.4.96: SD assets over /mouth). The worker is played by the test.
#include "mouth_assets.h"
#include "mouth_modes.h"
#include <ArduinoJson.hpp>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

using namespace collector::mouth;
using namespace collector::mouth::assets;
using ArduinoJson::JsonDocument;

namespace {
uint8_t kDigest[32];
uint8_t kSmallDigest[32];
int allocs = 0, frees = 0;
bool alloc_fails = false;
void* test_alloc(size_t n) { if (alloc_fails) return nullptr; ++allocs; return std::malloc(n); }
void test_free(void* p) { ++frees; std::free(p); }
constexpr size_t kBig = 40000;   // > window: needs acks to finish
constexpr size_t kSmall = 100;
bool resolve(const char* part, Spec& out)
{
    if (!std::strcmp(part, "cjk16.0")) { out = {"/sdcard/soul/font/cjk16.0.bin", kBig, kDigest, true}; return true; }
    if (!std::strcmp(part, "license")) { out = {"/sdcard/soul/font/LICENSE-OFL.txt", kSmall, kSmallDigest, true}; return true; }
    if (!std::strcmp(part, "voice.1")) { out = {"/sdcard/soul/voice/1.ima", kSmall, kSmallDigest, false}; return true; }
    if (!std::strcmp(part, "huge")) { out = {"/sdcard/x", kMaxBytes + 1, kDigest, false}; return true; }
    return false;
}
const Hooks kHooks{resolve, test_alloc, test_free};

std::string hex(const uint8_t* d)
{
    std::string s;
    char b[3];
    for (int i = 0; i < 32; ++i) { std::snprintf(b, sizeof b, "%02x", d[i]); s += b; }
    return s;
}

JsonDocument parse(const std::string& text)
{
    JsonDocument doc;
    assert(!ArduinoJson::deserializeJson(doc, text));
    return doc;
}

std::string begin_text(uint32_t id, const char* part, size_t bytes, const std::string& sha)
{
    char t[300];
    std::snprintf(t, sizeof t, "{\"type\":\"asset.begin\",\"id\":%u,\"part\":\"%s\",\"bytes\":%zu,\"sha256\":\"%s\"}", id, part, bytes, sha.c_str());
    return t;
}

void begin(Transfer& t, const std::string& text, int64_t now = 0, bool storage = true)
{
    JsonDocument doc;
    assert(read_text(text.data(), text.size(), doc) == Text::AssetBegin);
    t.begin(doc.as<ArduinoJson::JsonObjectConst>(), now, storage);
}

void ctl(Transfer& t, const char* type, uint32_t id, int64_t now = 0)
{
    char text[80];
    std::snprintf(text, sizeof text, "{\"type\":\"%s\",\"id\":%u}", type, id);
    JsonDocument doc;
    const Text k = read_text(text, std::strlen(text), doc);
    if (k == Text::AssetEnd) t.end(doc.as<ArduinoJson::JsonObjectConst>(), now);
    else { assert(k == Text::AssetAbort); t.abort(doc.as<ArduinoJson::JsonObjectConst>()); }
}

std::vector<uint8_t> frame(uint32_t id, uint32_t offset, size_t n, uint8_t fill = 0x5a)
{
    std::vector<uint8_t> f(kHeader + n, fill);
    std::memcpy(f.data(), "AST1", 4);
    for (int i = 0; i < 4; ++i) { f[4 + i] = uint8_t(id >> (8 * i)); f[8 + i] = uint8_t(offset >> (8 * i)); }
    return f;
}

std::vector<JsonDocument> drain(Transfer& t)
{
    std::vector<JsonDocument> out;
    Line l;
    while (t.next(l)) { assert(l.size() < kLineMax && l.size() == std::strlen(l.c_str())); out.push_back(parse(l.c_str())); }
    return out;
}

// The worker: takes the pending job, answers it.
Job work(Transfer& t, const char* error, bool flag, bool* font = nullptr, int64_t now = 0)
{
    JobView v;
    const Job j = t.take_job(v);
    if (j == Job::None) return j;
    assert(v.bytes && v.spec.path && v.length == v.spec.bytes);
    const bool f = t.job_done(error, flag, now);
    if (font) *font = f;
    return j;
}

// Sends the whole file respecting the window; returns the number of acks seen.
int send_all(Transfer& t, uint32_t id, size_t total, int64_t now = 0)
{
    size_t sent = 0, acked = 0;
    int acks = 0;
    while (acked < total) {
        while (sent < total && sent - acked + kChunkMax <= kWindow) {
            const size_t n = std::min(kChunkMax, total - sent);
            auto f = frame(id, uint32_t(sent), n);
            t.chunk(f.data(), f.size(), now);
            sent += n;
        }
        bool progress = false;
        for (auto& m : drain(t)) {
            assert(!std::strcmp(m["type"] | "", "asset.ack"));
            assert(m["id"] == id && m["window"] == kWindow);
            const size_t b = m["bytes"];
            assert(b >= acked && b <= sent);
            acked = b; ++acks; progress = true;
        }
        assert(progress);   // the window would stall without an ack
    }
    return acks;
}
}

int main()
{
    for (int i = 0; i < 32; ++i) { kDigest[i] = uint8_t(i * 7 + 1); kSmallDigest[i] = uint8_t(0xa0 + i); }
    const std::string sha = hex(kDigest), small_sha = hex(kSmallDigest);

    // Magic: AST1 chunks never look like TTS packets and vice versa.
    {
        auto f = frame(1, 0, 4);
        assert(is_chunk(f.data(), f.size()));
        Header h;
        assert(!parse_header(f.data(), f.size(), h));
        const uint8_t spk[16] = {'S', 'P', 'K', '1'};
        assert(!is_chunk(spk, sizeof spk));
        assert(!is_chunk(f.data(), kHeader - 1));
        assert(kHeader + kChunkMax == kMaxMessage);
    }
    // Part words.
    assert(part_word("cjk16.0") && part_word("eyes.v05.affectionate") && part_word("voice.12"));
    assert(!part_word("") && !part_word("../x"));   // dots and letters are fine, the slash is not
    assert(!part_word("a/b") && !part_word("Cjk") && !part_word("a b") && !part_word("x\"y"));
    assert(!part_word("abcdefghijklmnopqrstuvwxyz0123456"));   // 33 > kPartMax

    // Full transfer: begin -> check (missing) -> ack 0 -> chunks with a sliding window -> end -> install -> result.
    {
        Transfer t(kHooks);
        begin(t, begin_text(7, "cjk16.0", kBig, sha));
        assert(t.phase() == Transfer::Phase::Checking && drain(t).empty());
        assert(work(t, nullptr, false) == Job::Check);
        assert(t.phase() == Transfer::Phase::Receiving);
        const int acks = send_all(t, 7, kBig);
        assert(acks >= 2);                                              // coalesced: one ack per pass
        assert(t.received() == kBig);
        ctl(t, "asset.end", 7);
        assert(t.phase() == Transfer::Phase::Installing);
        // A second begin while busy is refused without touching the running one.
        begin(t, begin_text(8, "license", kSmall, small_sha));
        auto busy = drain(t);
        assert(busy.size() == 1 && busy[0]["id"] == 8 && busy[0]["ok"] == false && !std::strcmp(busy[0]["reason"], "busy"));
        bool font = false;
        assert(work(t, nullptr, false, &font) == Job::Install && font);
        auto r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["type"], "asset.result") && r[0]["id"] == 7 && r[0]["ok"] == true);
        assert(r[0]["unchanged"] == false && r[0]["bytes"] == kBig && !std::strcmp(r[0]["part"], "cjk16.0") && r[0]["reason"].isNull());
        assert(t.phase() == Transfer::Phase::Idle && !t.busy() && t.stats().installed == 1 && allocs == frees);
    }
    // Unchanged: the file on SD already matches -> result at once, no data asked for.
    {
        Transfer t(kHooks);
        begin(t, begin_text(9, "license", kSmall, small_sha));
        bool font = false;
        assert(work(t, nullptr, true, &font) == Job::Check && font);   // a font part is there: reload too
        auto r = drain(t);
        assert(r.size() == 1 && r[0]["ok"] == true && r[0]["unchanged"] == true && r[0]["id"] == 9);
        assert(!t.busy() && t.stats().unchanged == 1 && allocs == frees);
        auto f = frame(9, 0, 10);                                      // late chunks are stray, not an error
        t.chunk(f.data(), f.size(), 0);
        assert(drain(t).empty() && t.stats().stray == 1);
    }
    // Install reports unchanged (install_file's own check).
    {
        Transfer t(kHooks);
        begin(t, begin_text(10, "voice.1", kSmall, small_sha));
        work(t, nullptr, false);
        assert(send_all(t, 10, kSmall) == 1);
        ctl(t, "asset.end", 10);
        bool font = true;
        work(t, nullptr, true, &font);
        assert(!font);                                                  // not a font part
        auto r = drain(t);
        assert(r.size() == 1 && r[0]["ok"] == true && r[0]["unchanged"] == true);
    }
    // Rejections at begin: unknown part, wrong size, wrong digest, bad id, no SD, no memory, too large.
    {
        Transfer t(kHooks);
        struct Case { std::string text; bool storage; const char* reason; } cases[] = {
            {begin_text(1, "nope", kSmall, sha), true, "unknown_part"},
            {begin_text(1, "../etc", kSmall, sha), true, "unknown_part"},
            {begin_text(1, "cjk16.0", kBig - 1, sha), true, "size_mismatch"},
            {begin_text(1, "cjk16.0", kBig, small_sha), true, "sha256_mismatch"},
            {begin_text(1, "cjk16.0", kBig, sha.substr(0, 63)), true, "sha256_mismatch"},
            {begin_text(1, "cjk16.0", kBig, sha.substr(0, 63) + "g"), true, "sha256_mismatch"},
            {begin_text(0, "cjk16.0", kBig, sha), true, "bad_message"},
            {begin_text(1, "cjk16.0", kBig, sha), false, "storage_unavailable"},
            {begin_text(1, "huge", kMaxBytes + 1, sha), true, "too_large"},
            {"{\"type\":\"asset.begin\",\"id\":1,\"part\":\"cjk16.0\",\"bytes\":\"40000\",\"sha256\":\"" + sha + "\"}", true, "size_mismatch"},
            {"{\"type\":\"asset.begin\",\"id\":-1,\"part\":\"cjk16.0\"}", true, "bad_message"},
        };
        for (auto& c : cases) {
            begin(t, c.text, 0, c.storage);
            auto r = drain(t);
            assert(r.size() == 1 && r[0]["ok"] == false && !std::strcmp(r[0]["reason"], c.reason));
            assert(!t.busy());
        }
        // An unsafe part id is never echoed.
        begin(t, begin_text(2, "a\\\"b", kSmall, sha));
        auto r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["part"], "") && !std::strcmp(r[0]["reason"], "unknown_part"));
        // Uppercase hex is the same digest.
        std::string upper = sha;
        for (auto& ch : upper) ch = char(std::toupper(ch));
        begin(t, begin_text(3, "cjk16.0", kBig, upper));
        assert(drain(t).empty() && t.phase() == Transfer::Phase::Checking);
        JobView v;
        assert(t.take_job(v) == Job::Check);                            // the check is on the worker
        ctl(t, "asset.abort", 3);
        r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "aborted"));
        assert(t.busy());                                               // the check is still on the worker
        begin(t, begin_text(4, "license", kSmall, small_sha));
        r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "busy"));
        assert(!t.job_done(nullptr, false, 0) && !t.busy() && drain(t).empty());   // abandoned: freed, no line
        alloc_fails = true;
        begin(t, begin_text(5, "license", kSmall, small_sha));
        r = drain(t);
        alloc_fails = false;
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "no_memory") && allocs == frees);
    }
    // Abort mid-transfer, out-of-order chunk, too many bytes, early end, timeout, worker errors and refusal.
    {
        Transfer t(kHooks);
        auto start = [&](uint32_t id) {
            begin(t, begin_text(id, "cjk16.0", kBig, sha), 0);
            work(t, nullptr, false);
            auto a = drain(t);
            assert(a.size() == 1 && !std::strcmp(a[0]["type"], "asset.ack") && a[0]["bytes"] == 0);
        };
        start(20);
        auto f = frame(20, 0, 1000);
        t.chunk(f.data(), f.size(), 0);
        ctl(t, "asset.abort", 999);                                     // another id: ignored
        assert(t.phase() == Transfer::Phase::Receiving);
        ctl(t, "asset.abort", 20);
        auto r = drain(t);
        assert(r.size() == 1 && r[0]["ok"] == false && !std::strcmp(r[0]["reason"], "aborted") && !t.busy() && allocs == frees);

        start(21);
        f = frame(21, 500, 100);
        t.chunk(f.data(), f.size(), 0);
        r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "out_of_order") && !t.busy());

        start(22);
        f = frame(22, 0, 1500);
        size_t at = 0;
        while (at + 1500 <= kBig) { auto g = frame(22, uint32_t(at), 1500); t.chunk(g.data(), g.size(), 0); at += 1500; }
        f = frame(22, uint32_t(at), kBig - at + 1);
        t.chunk(f.data(), f.size(), 0);
        r = drain(t);
        assert(!r.empty() && !std::strcmp(r.back()["reason"], "too_long") && !t.busy());

        start(23);
        f = frame(23, 0, 1000);
        t.chunk(f.data(), f.size(), 0);
        ctl(t, "asset.end", 23);
        r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "incomplete") && !t.busy());

        start(24);
        t.tick(kIdleUs - 1, false);
        assert(t.phase() == Transfer::Phase::Receiving);
        t.tick(kIdleUs * 3, true);                                      // speaking: the idle clock waits
        assert(t.phase() == Transfer::Phase::Receiving);
        t.tick(kIdleUs * 4 + 1, false);
        r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "timeout") && !t.busy());

        start(25);                                                      // the total cap holds even while speaking
        for (int64_t now = 0; now <= kTotalUs; now += kIdleUs / 2) t.tick(now, true);
        t.tick(kTotalUs + 1, true);
        r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "timeout"));

        begin(t, begin_text(26, "cjk16.0", kBig, sha));
        work(t, "display_bus_unavailable", false);                      // check failed on the worker
        r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "display_bus_unavailable") && !t.busy());

        start(27);
        send_all(t, 27, kBig);
        ctl(t, "asset.end", 27);
        ctl(t, "asset.abort", 27);                                      // the write is not stopped
        assert(t.phase() == Transfer::Phase::Installing);
        bool font = true;
        work(t, "io_error", false, &font);
        r = drain(t);
        assert(!font && r.size() == 1 && !std::strcmp(r[0]["reason"], "io_error") && allocs == frees);

        begin(t, begin_text(28, "cjk16.0", kBig, sha));
        JobView v;
        assert(t.take_job(v) == Job::Check);
        t.job_refused();
        r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "installer_busy") && !t.busy() && allocs == frees);
    }
    // Link drop: mid-receive frees at once; during an install the buffer lives until the worker returns.
    {
        Transfer t(kHooks);
        begin(t, begin_text(30, "cjk16.0", kBig, sha));
        work(t, nullptr, false);
        auto f = frame(30, 0, 1000);
        t.chunk(f.data(), f.size(), 0);
        t.link_down();
        assert(drain(t).empty() && !t.busy() && allocs == frees && t.stats().dropped == 1);

        begin(t, begin_text(31, "cjk16.0", kBig, sha));
        work(t, nullptr, false);
        send_all(t, 31, kBig);
        ctl(t, "asset.end", 31);
        JobView v;
        assert(t.take_job(v) == Job::Install);
        t.link_down();
        assert(t.busy() && allocs == frees + 1);                         // still owned by the worker
        begin(t, begin_text(32, "license", kSmall, small_sha));
        auto r = drain(t);
        assert(r.size() == 1 && !std::strcmp(r[0]["reason"], "busy"));
        drain(t);
        assert(!t.job_done(nullptr, false, 0) && !t.busy() && allocs == frees && drain(t).empty());
        begin(t, begin_text(33, "license", kSmall, small_sha));         // a fresh transfer works again
        assert(t.phase() == Transfer::Phase::Checking);
    }
    // Telemetry shape.
    {
        Transfer t(kHooks);
        JsonDocument doc;
        t.json(doc.to<ArduinoJson::JsonObject>());
        assert(!std::strcmp(doc["phase"], "idle") && doc["begun"] == 0);
        begin(t, begin_text(40, "cjk16.0", kBig, sha));
        t.json(doc.to<ArduinoJson::JsonObject>());
        assert(!std::strcmp(doc["phase"], "checking") && !std::strcmp(doc["part"], "cjk16.0") && doc["bytes"] == kBig);
        t.link_down();
    }
    std::puts("mouth assets: ok");
    return 0;
}
