// Build: c++ -std=c++17 -I upstream/StackChan/firmware/components/ArduinoJson/src tests/m12_protocol_test.cpp
#include "../upstream/StackChan/firmware/main/collector/m12_protocol.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <random>
#include <vector>
using namespace collector;

static std::vector<std::string> lines_of(const std::string& stream)
{
    M12LineBuffer buffer;
    std::vector<std::string> lines;
    std::string line;
    for (char c : stream) if (buffer.push(c, line)) lines.push_back(line);
    return lines;
}

int main()
{
    // Commands end in CRLF and carry the display names the device expects.
    assert(std::strstr(kM12FaceDetectorCommand, "\"Face Detector\"") && std::strstr(kM12StandbyCommand, "\"Audio FFT\""));
    assert(std::strcmp(kM12FaceDetectorCommand + std::strlen(kM12FaceDetectorCommand) - 2, "\r\n") == 0);
    {   // A face detector result in the documented format (two faces, landmarks ignored).
        const std::string doc = R"({"running":"Face Detector","num":2,"face":[)"
                                R"({"x":100,"y":80,"w":120,"h":140,"prob":0.98,"mark":[{"x":130,"y":120},{"x":190,"y":120}]},)"
                                R"({"x":600,"y":400,"w":100,"h":100,"prob":0.6}]})";
        auto lines = lines_of(doc + "\r\n");
        assert(lines.size() == 1);
        auto r = parse_m12_line(lines[0]);
        assert(r.kind == M12LineKind::faces && r.num == 2 && r.count == 2);
        assert(r.faces[0].x == 100 && r.faces[0].w == 120 && r.faces[0].prob > 0.97f);
        assert(r.faces[1].x == 600 && r.faces[1].w == 40 && r.faces[1].h == 80); // clipped to the 640x480 frame
        auto b = m12_to_screen(r.faces[0]);              // mirrored: 640 - 100 - 120 = 420 -> 210
        assert(b.x == 210 && b.y == 40 && b.w == 60 && b.h == 70);
        auto raw = m12_to_screen(r.faces[0], false);
        assert(raw.x == 50 && raw.y == 40);
    }
    {   // Real output captured from the dev M12 (2026-11-07): float coordinates, "running" last.
        const std::string doc = R"({"num":1,"face":[{"x":168.9219513,"y":92.53930664,"w":206.7189789,"h":292.3893738,)"
                                R"("prob":0.994655788,"mark":[{"x":220,"y":194},{"x":306,"y":222},{"x":238,"y":260},)"
                                R"({"x":225,"y":310},{"x":300,"y":330}]}],"running":"Face Detector"})";
        auto r = parse_m12_line(doc);
        assert(r.kind == M12LineKind::faces && r.num == 1 && r.count == 1);
        assert(r.faces[0].x == 169 && r.faces[0].y == 93 && r.faces[0].w == 207 && r.faces[0].h == 292);
        assert(r.faces[0].prob > 0.99f);
        auto b = m12_to_screen(r.faces[0]);              // mirrored: 640 - 169 - 207 = 264 -> 132
        assert(b.x == 132 && b.y == 46 && b.w == 103 && b.h == 146);
    }
    {   // No face, switch reply, error, other function, garbage.
        auto none = parse_m12_line(R"({"running":"Face Detector","num":0,"face":[]})");
        assert(none.kind == M12LineKind::faces && none.count == 0 && none.num == 0);
        auto reply = parse_m12_line(R"({"msg":"function switched to Face Detector."})");
        assert(reply.kind == M12LineKind::reply && std::strstr(reply.text, "Face Detector"));
        auto error = parse_m12_line(R"({"error":"invalid json format."})");
        assert(error.kind == M12LineKind::error && std::strcmp(error.text, "invalid json format.") == 0);
        auto other = parse_m12_line(R"({"running":"Code Detector","num":1,"code":[]})");
        assert(other.kind == M12LineKind::other && std::strcmp(other.running, "Code Detector") == 0);
        assert(parse_m12_line("port opened").kind == M12LineKind::invalid);
        assert(parse_m12_line("{\"running\":\"Face Det").kind == M12LineKind::invalid);
    }
    {   // More faces than we keep; zero-size boxes are skipped.
        std::string doc = R"({"running":"Face Detector","num":9,"face":[)";
        for (int i = 0; i < 9; ++i) doc += std::string(i ? "," : "") + R"({"x":)" + std::to_string(i * 10) + R"(,"y":0,"w":)" +
                                           (i == 0 ? "0" : "20") + R"(,"h":20,"prob":0.9})";
        doc += "]}";
        auto r = parse_m12_line(doc);
        assert(r.kind == M12LineKind::faces && r.num == 9 && r.count == kM12MaxFaces && r.faces[0].x == 10);
    }
    {   // Line assembly: split reads, CR/LF, blank lines, an overlong line dropped whole.
        auto lines = lines_of("{\"msg\":\"a\"}\r\n\r\n{\"msg\":\"b\"}\n" + std::string(kM12MaxLine + 10, 'x') + "\n{\"msg\":\"c\"}\n");
        assert(lines.size() == 3 && lines[2] == "{\"msg\":\"c\"}");
        M12LineBuffer buffer;
        std::string line;
        for (char c : std::string(kM12MaxLine + 1, 'y')) assert(!buffer.push(c, line));
        assert(buffer.overflows() == 1 && !buffer.push('\n', line));
    }
    {   // Random bytes never crash and never yield out-of-frame boxes.
        std::mt19937 random(7);
        const std::string alphabet = "{}[]\":,0123456789.-xywhprobnumfaceruning FDetctor\r\n";
        for (int round = 0; round < 3000; ++round) {
            std::string s(random() % 300, ' ');
            for (auto& c : s) c = alphabet[random() % alphabet.size()];
            for (const auto& line : lines_of(s + "\n")) {
                auto r = parse_m12_line(line);
                for (size_t i = 0; i < r.count; ++i) {
                    const auto& f = r.faces[i];
                    assert(f.x >= 0 && f.y >= 0 && f.w > 0 && f.h > 0 && f.x + f.w <= kM12FrameWidth && f.y + f.h <= kM12FrameHeight);
                    auto b = m12_to_screen(f);
                    assert(b.x >= 0 && b.y >= 0 && b.x + b.w <= 320 && b.y + b.h <= 240);
                }
            }
        }
    }
    {   // Face Recognition result: name and match probability per face.
        auto r = parse_m12_line(R"({"num":1,"face":[{"x":100.4,"y":60.6,"w":80,"h":90,"prob":0.97,"match_prob":0.83,"name":"papa"}],"running":"Face Recognition"})");
        assert(r.kind == M12LineKind::faces && r.recognition && r.count == 1);
        assert(std::strcmp(r.faces[0].name, "papa") == 0 && r.faces[0].match_prob > 0.82f && r.faces[0].x == 100);
        auto d = parse_m12_line(R"({"num":1,"face":[{"x":1,"y":1,"w":9,"h":9,"prob":0.9}],"running":"Face Detector"})");
        assert(!d.recognition && d.faces[0].name[0] == 0);
    }
    {   // Policy: look around after boot, identify a new face, keep watching, doze when idle.
        constexpr int64_t s = 1000000;
        M12Policy p;
        assert(p.function() == M12Function::none);
        p.start(0);
        assert(p.phase() == M12Phase::looking && p.function() == M12Function::detect);
        p.tick(30 * s);
        assert(p.function() == M12Function::detect);                  // M12 still booting: keep looking
        p.result(35 * s, 1, false, nullptr, 0);                        // first face: who is it?
        assert(p.phase() == M12Phase::identifying && p.function() == M12Function::recognize);
        p.result(36 * s, 1, false, nullptr, 0);                        // trailing detector result: ignored
        p.result(38 * s, 1, true, "", 0.2f);                           // not matched yet
        assert(p.phase() == M12Phase::identifying);
        p.result(39 * s, 1, true, "papa", 0.81f);
        assert(p.phase() == M12Phase::watching && p.function() == M12Function::detect);
        assert(p.identity().known && std::strcmp(p.identity().name, "papa") == 0 && p.identity().at_us == 39 * s);
        p.result(41 * s, 1, false, nullptr, 0);
        p.result(70 * s, 1, false, nullptr, 0);                        // new after 29 s, but cooldown: no burst
        assert(p.phase() == M12Phase::watching);
        p.tick(95 * s);                                                // look window from first result (35+60) is over
        assert(p.phase() == M12Phase::watching);
        p.tick(129 * s);
        assert(p.function() == M12Function::detect);                   // 59 s since last face
        p.tick(130 * s);
        assert(p.phase() == M12Phase::dozing && p.function() == M12Function::standby);
        p.result(131 * s, 1, false, nullptr, 0);                       // stray result while switching: stays dozing
        assert(p.phase() == M12Phase::dozing);
        p.wake(200 * s);                                               // voice / touch / remote
        assert(p.phase() == M12Phase::looking && p.function() == M12Function::detect);
        p.result(205 * s, 1, false, nullptr, 0);                       // new face, cooldown over: identify again
        assert(p.phase() == M12Phase::identifying);
        for (int i = 0; i < M12Policy::kUnknownAfter; ++i) p.result((206 + i) * s, 1, true, "", 0.1f);
        assert(p.phase() == M12Phase::watching && !p.identity().known);   // nobody enrolled matched
        p.view(true, 220 * s);                                         // face page open: never doze
        p.tick(1000 * s);
        assert(p.function() == M12Function::detect);
        p.view(false, 1000 * s);
        p.tick(1059 * s);
        assert(p.function() == M12Function::detect);
        p.tick(1060 * s);
        assert(p.function() == M12Function::standby);
        p.view(true, 1100 * s);                                        // "turn on" from standby wakes it
        assert(p.phase() == M12Phase::looking);
        p.result(1105 * s, 1, false, nullptr, 0);
        assert(p.phase() == M12Phase::identifying);
        p.tick(1115 * s);                                              // no recognition answer within 10 s
        assert(p.phase() == M12Phase::watching && !p.identity().known);
    }
    {   // No M12 at all: look for 60 s, then doze.
        M12Policy p;
        p.start(0);
        p.tick(59999999);
        assert(p.function() == M12Function::detect);
        p.tick(60000000);
        assert(p.function() == M12Function::standby);
    }
    std::cout << "PASS: M12 commands, face results, clipping, mirror mapping, replies/errors, line assembly, 3000 random streams, recognition fields, look/identify/watch/doze policy\n";
}
