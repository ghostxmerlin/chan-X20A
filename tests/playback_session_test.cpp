/* SPDX-License-Identifier: MIT */
#include "../upstream/StackChan/firmware/main/collector/playback_session.h"
#include "../upstream/StackChan/firmware/main/collector/reaction_sound.h"
#include <cassert>
#include <iostream>
#include <limits>
#include <string>
#include <thread>
#include <vector>

using namespace collector;

struct FakeOutput {
    int begin_error = 0, finish_error = 0;
    size_t fail_write = 0;
    unsigned begins = 0, finishes = 0, pauses = 0, drains = 0;
    bool active = false;
    std::vector<std::string> states;
    std::vector<std::vector<int16_t>> writes;
    std::vector<size_t> progress;
    void state(const char* value) { states.emplace_back(value); }
    int begin() { ++begins; active = begin_error == 0; return begin_error; }
    int finish() { ++finishes; active = false; return finish_error; }
    void pause() { ++pauses; }
    void drain() { assert(active); ++drains; }
    void submitted(size_t count) { progress.push_back(count); }
    int write(const int16_t* samples, size_t count)
    {
        assert(active && count > 0 && count <= kPlaybackChunkFrames);
        writes.emplace_back(samples, samples + count);
        return writes.size() == fail_write ? -32 : 0;
    }
};

static PlaybackUpload complete_upload(size_t bytes)
{
    PlaybackUpload upload(bytes);
    // HTTP reads need not align with samples: only the final body must do so.
    while (!upload.ready()) assert(upload.append(std::min<size_t>(997, bytes - upload.received())));
    return upload;
}

static void length_and_upload_checks()
{
    assert(!playback_length_valid(0));
    assert(!playback_length_valid(1));
    assert(playback_length_valid(2));
    assert(playback_length_valid(kPlaybackMaxBytes));
    assert(!playback_length_valid(kPlaybackMaxBytes + 1));
    assert(!playback_length_valid(kPlaybackMaxBytes + 2));
    assert(!playback_length_valid(std::numeric_limits<size_t>::max()));
    for (size_t bytes : {size_t(0), size_t(1), size_t(3), kPlaybackMaxBytes + 2}) {
        PlaybackUpload invalid(bytes);
        assert(!invalid.append(1) && !invalid.ready());
    }
    const int16_t pcm = 123;
    PlaybackUpload partial(2);
    assert(!partial.append(0) && !partial.append(3));
    assert(partial.append(1) && !partial.ready());
    FakeOutput untouched;
    auto result = run_pcm_playback(&pcm, partial, untouched);
    assert(result.error && std::string(result.stage) == "incomplete_upload");
    assert(!untouched.begins && !untouched.finishes && untouched.states.empty());
    assert(partial.append(1) && partial.ready() && !partial.append(1));
    result = run_pcm_playback(nullptr, partial, untouched);
    assert(result.error && !untouched.begins);
}

static void successful_streams()
{
    for (size_t bytes : {size_t(2), size_t(960), size_t(962), kPlaybackMaxBytes}) {
        std::vector<int16_t> pcm(bytes / 2);
        for (size_t i = 0; i < pcm.size(); ++i) pcm[i] = int16_t(1 + i % 32766);
        const auto original = pcm;
        FakeOutput output;
        auto result = run_pcm_playback(pcm.data(), complete_upload(bytes), output);
        assert(!result.error && !result.cleanup_error && result.submitted == bytes);
        assert(pcm == original && !output.active);
        assert(output.begins == 1 && output.finishes == 1 && output.drains == 1);
        const size_t payload_blocks = (pcm.size() + 479) / 480;
        assert(output.writes.size() == payload_blocks + 5);
        assert(output.pauses == output.writes.size());
        assert(output.progress.size() == payload_blocks && output.progress.back() == bytes);
        assert((output.states == std::vector<std::string>{"starting", "playing", "draining", "closing"}));
        std::vector<int16_t> sent;
        for (size_t i = 0; i < payload_blocks; ++i)
            sent.insert(sent.end(), output.writes[i].begin(), output.writes[i].end());
        assert(sent == pcm);
        for (size_t i = payload_blocks; i < output.writes.size(); ++i) {
            assert(output.writes[i].size() == 480);
            for (auto sample : output.writes[i]) assert(sample == 0);
        }
    }
}

static void failure_cleanup()
{
    std::vector<int16_t> pcm(1100, 123);
    const auto upload = complete_upload(pcm.size() * 2);
    for (int error : {-1, -2, -3, -4}) {
        FakeOutput output;
        output.begin_error = error;
        output.finish_error = -77;
        auto result = run_pcm_playback(pcm.data(), upload, output);
        assert(result.error == error && result.cleanup_error == -77 && !result.submitted);
        assert(std::string(result.stage) == "start");
        assert(output.begins == 1 && output.finishes == 1 && !output.active);
        assert(output.writes.empty() && !output.drains && !output.pauses);
        assert((output.states == std::vector<std::string>{"starting", "closing"}));
    }
    // Inject failure at every payload/tail write. Payload failure must still
    // attempt silence and cleanup, and never count unwritten bytes as submitted.
    for (size_t fail = 1; fail <= 8; ++fail) {
        FakeOutput output;
        output.fail_write = fail;
        auto result = run_pcm_playback(pcm.data(), upload, output);
        assert(result.error == -32 && !result.cleanup_error && !output.active);
        assert(output.begins == 1 && output.finishes == 1);
        assert(output.pauses == output.writes.size());
        assert(output.states.back() == "closing");
        if (fail <= 3) {
            assert(std::string(result.stage) == "write");
            assert(result.submitted == (fail - 1) * 960);
            assert(output.progress.size() == fail - 1);
            assert(output.writes.size() == fail + 5 && output.drains == 1);
            for (size_t i = fail; i < output.writes.size(); ++i)
                for (auto sample : output.writes[i]) assert(sample == 0);
        } else {
            assert(std::string(result.stage) == "silence" && result.submitted == upload.bytes());
            assert(output.writes.size() == fail && output.drains == 0);
        }
    }
    FakeOutput output;
    output.finish_error = -77;
    auto result = run_pcm_playback(pcm.data(), upload, output);
    assert(result.error == -77 && result.cleanup_error == -77 && std::string(result.stage) == "cleanup");
    assert(result.submitted == upload.bytes() && output.finishes == 1 && !output.active);
    FakeOutput double_failure;
    double_failure.fail_write = 1; double_failure.finish_error = -77;
    result = run_pcm_playback(pcm.data(), upload, double_failure);
    assert(result.error == -32 && result.cleanup_error == -77 && std::string(result.stage) == "write");
}

static void concurrent_gate()
{
    PlaybackGate gate;
    for (unsigned round = 0; round < 20; ++round) {
        std::atomic<unsigned> ready{0}, winners{0};
        std::atomic_bool start{false};
        std::vector<std::thread> clients;
        for (unsigned i = 0; i < 16; ++i) clients.emplace_back([&] {
            ++ready;
            while (!start.load()) std::this_thread::yield();
            if (gate.acquire()) ++winners;
        });
        while (ready.load() != 16) std::this_thread::yield();
        start = true;
        for (auto& client : clients) client.join();
        assert(winners == 1 && gate.busy() && !gate.acquire());
        gate.release();
        assert(!gate.busy());
    }
    assert(gate.acquire()); gate.release();
}

static void reaction_cues()
{
    std::vector<int16_t> heart(kReactionFrames), reject(kReactionFrames);
    synthesize_reaction(RemoteReaction::Heart, heart.data(), heart.size());
    synthesize_reaction(RemoteReaction::Reject, reject.data(), reject.size());
    assert(heart != reject && heart.front() == 0 && heart.back() == 0);
    double heart_energy = 0, reject_energy = 0;
    for (size_t i = 0; i < heart.size(); ++i) { heart_energy += double(heart[i]) * heart[i]; reject_energy += double(reject[i]) * reject[i]; }
    assert(reject_energy > heart_energy * 2); // Rejection is audibly stronger at unchanged codec volume.
    PendingReaction pending;
    assert(pending.take(0) == RemoteReaction::None);
    assert(!pending.put(RemoteReaction::Heart, 100));
    assert(pending.put(RemoteReaction::Reject, 200));
    assert(pending.take(700000) == RemoteReaction::Reject);
    assert(pending.take(700001) == RemoteReaction::None);
    pending.put(RemoteReaction::Heart, 100);
    assert(pending.take(750101) == RemoteReaction::None);
    pending.put(RemoteReaction::Heart, 100);
    assert(pending.take(99) == RemoteReaction::None);
    pending.put(RemoteReaction::Heart, 100); assert(pending.clear() && !pending.clear());
    for (const auto* cue : {&heart, &reject}) {
        assert(cue->front() == 0 && cue->back() == 0);
        int peak = 0, jump = 0;
        for (size_t i = 0; i < cue->size(); ++i) {
            peak = std::max(peak, std::abs(int((*cue)[i])));
            if (i) jump = std::max(jump, std::abs(int((*cue)[i]) - int((*cue)[i - 1])));
        }
        assert(peak > 5000 && peak <= (cue == &heart ? 14000 : 26000) && jump < 9000);
        FakeOutput output;
        const auto result = run_pcm_playback(cue->data(), complete_upload(cue->size() * 2), output);
        assert(!result.error && result.submitted == cue->size() * 2 && output.finishes == 1);
    }
    synthesize_reaction(RemoteReaction::None, heart.data(), heart.size());
    for (auto sample : heart) assert(sample == 0);
}

int main()
{
    reaction_cues();
    length_and_upload_checks(); successful_streams(); failure_cleanup(); concurrent_gate();
    std::cout << "Playback length/upload, all write failures, cleanup and 20x16 concurrent gate checks passed\n";
}
