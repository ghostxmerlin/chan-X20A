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
    assert(next_m12_request(0, M12Function::detect) == 5);
    assert(next_m12_request(5, M12Function::recognize) == 10);
    assert(next_m12_request(10, M12Function::standby) == 12);
    assert(m12_requested_view(10) && !m12_requested_view(12));
    assert(m12_requested_function(10) == M12Function::recognize);
    assert(m12_switch_required(M12Function::recognize, M12Function::recognize, true));
    assert(!m12_switch_required(M12Function::recognize, M12Function::recognize));
    { // Official recognition is silent without faces; startup report is an ACK.
        const auto report = parse_m12_line(R"({"msg":"Running Face Recognition, Copyright 2021","running":"Face Recognition"})");
        assert(m12_recognition_report(report));
        assert(!m12_recognition_report(parse_m12_line(R"({"error":"failed","running":"Face Recognition"})")));
        assert(!m12_recognition_unanswered(100, 0, 110));
        assert(m12_recognition_unanswered(200, 0, 110)); // explicit retry needs its own ACK
    }
    { // Identity recognition is explicit, and late/empty results never retain a person.
        M12Policy p; p.start(0); p.select(M12Function::recognize, 100);
        assert(p.function() == M12Function::recognize);
        p.result(200, 1, true, "papa", .83f);
        assert(p.identity().known && std::strcmp(p.identity().name, "papa") == 0);
        p.result(300, 0, true, nullptr, 0);
        assert(!p.identity().known && p.identity().at_us == 0);
        p.result(400, 1, true, "unknown", .9f);
        assert(!p.identity().known);
        p.result(500, 1, true, "papa", .4f);
        assert(!p.identity().known);
        p.view(false, 600); p.result(700, 1, true, "papa", .9f);
        assert(p.function() == M12Function::standby && p.identity().at_us == 0);
        p.select(M12Function::recognize, 800); p.view(true, 900);
        assert(p.function() == M12Function::detect && p.identity().at_us == 0);
    }
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
    {   // Boot and unrelated events never start the camera or identity recognition.
        constexpr int64_t second = 1000000;
        M12Policy p;
        p.start(0);
        assert(p.function() == M12Function::standby);
        p.wake(second); // ordinary ASR, touch or remote input
        p.result(2 * second, 1, false, nullptr, 0);
        p.result(3 * second, 1, true, "papa", 0.9f);
        p.tick(120 * second);
        assert(p.function() == M12Function::standby && p.identity().at_us == 0);
    }
    {   // Explicit turn on remains detection despite absence, faces and late recognition.
        constexpr int64_t second = 1000000;
        M12Policy p;
        p.start(0);
        p.view(true, second);
        assert(p.function() == M12Function::detect);
        p.tick(61 * second); // no M12 replies: keep requesting detection
        assert(p.function() == M12Function::detect);
        p.result(62 * second, 1, false, nullptr, 0);
        assert(p.function() == M12Function::detect); // a face must not start recognition
        p.result(63 * second, 1, true, "papa", 0.9f);
        p.wake(64 * second);
        p.tick(1000 * second);
        assert(p.function() == M12Function::detect && p.identity().at_us == 0);
        p.view(false, 1001 * second); // turn off or leave the face page: immediate standby
        assert(p.function() == M12Function::standby);
        p.result(1002 * second, 1, false, nullptr, 0);
        p.result(1003 * second, 1, true, "papa", 0.9f);
        p.wake(1004 * second);
        p.tick(2000 * second);
        assert(p.function() == M12Function::standby);
        p.view(true, 2001 * second); // the next explicit turn on still works
        assert(p.function() == M12Function::detect);
        p.view(true, 2002 * second);
        assert(p.function() == M12Function::detect);
        p.view(false, 2003 * second);
        p.view(false, 2004 * second);
        assert(p.function() == M12Function::standby);
    }
    {   // Command selection must send detector after a quick off/on, despite trailing frames.
        M12Function active = M12Function::none;
        std::vector<M12Function> commands;
        auto request = [&](M12Function want) {
            if (m12_switch_required(active, want)) commands.push_back(want);
            active = want;
        };
        request(M12Function::standby);
        request(M12Function::detect);
        request(M12Function::standby);
        // Face timestamps are deliberately absent from the command selector:
        // even a trailing detector frame cannot suppress this command.
        request(M12Function::detect);
        request(M12Function::detect); // an unchanged request does not restart detection
        assert(commands == std::vector<M12Function>({M12Function::standby, M12Function::detect,
                                                    M12Function::standby, M12Function::detect}));
    }
    std::cout << "PASS: M12 commands, face results, clipping, mirror mapping, replies/errors, line assembly, 3000 random streams, recognition fields, explicit standby/detector/recognition policy, quick mode changes and manual retry\n";
}
