/* SPDX-License-Identifier: MIT */
#pragma once
#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace collector {
constexpr size_t kPlaybackMaxBytes = 24000 * sizeof(int16_t) * 8;
constexpr size_t kPlaybackChunkFrames = 480;

inline bool playback_length_valid(size_t bytes)
{
    return bytes > 0 && bytes <= kPlaybackMaxBytes && bytes % sizeof(int16_t) == 0;
}

class PlaybackGate {
public:
    bool acquire() { bool expected = false; return busy_.compare_exchange_strong(expected, true); }
    void release() { busy_ = false; }
    bool busy() const { return busy_.load(); }
private:
    std::atomic_bool busy_{false};
};

class PlaybackUpload {
public:
    explicit PlaybackUpload(size_t bytes) : expected_(bytes) {}
    bool append(size_t count)
    {
        if (!playback_length_valid(expected_) || count == 0 || count > expected_ - received_) return false;
        received_ += count;
        return true;
    }
    bool ready() const { return playback_length_valid(expected_) && received_ == expected_; }
    size_t received() const { return received_; }
    size_t bytes() const { return expected_; }
private:
    size_t expected_, received_ = 0;
};

struct PlaybackResult {
    int error = 0, cleanup_error = 0;
    const char* stage = "none";
    size_t submitted = 0;
};

// Output owns hardware setup/cleanup. This runner is shared with host tests;
// an incomplete upload cannot reach begin(), and every begin attempt ends.
template<class Output>
PlaybackResult run_pcm_playback(const int16_t* pcm, const PlaybackUpload& upload, Output& output)
{
    PlaybackResult result;
    if (!pcm || !upload.ready()) { result.error = -1; result.stage = "incomplete_upload"; return result; }
    output.state("starting");
    result.error = output.begin();
    if (result.error) result.stage = "start";
    else {
        output.state("playing");
        const size_t frames = upload.bytes() / sizeof(int16_t);
        for (size_t offset = 0; offset < frames;) {
            const size_t count = std::min(kPlaybackChunkFrames, frames - offset);
            const int error = output.write(pcm + offset, count);
            output.pause(); // At least one scheduler tick, including errors.
            if (error) { result.error = error; result.stage = "write"; break; }
            offset += count;
            result.submitted = offset * sizeof(int16_t);
            output.submitted(result.submitted);
        }
        output.state("draining");
        std::array<int16_t, kPlaybackChunkFrames> silence{};
        bool silence_ok = true;
        for (unsigned block = 0; block < 5; ++block) { // 100 ms at 24 kHz.
            const int error = output.write(silence.data(), silence.size());
            output.pause();
            if (error) {
                if (!result.error) { result.error = error; result.stage = "silence"; }
                silence_ok = false;
                break;
            }
        }
        if (silence_ok) output.drain();
    }
    output.state("closing");
    result.cleanup_error = output.finish();
    if (!result.error && result.cleanup_error) { result.error = result.cleanup_error; result.stage = "cleanup"; }
    return result;
}
}
