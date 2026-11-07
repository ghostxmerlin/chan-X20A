#include <collector/screen.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

size_t flushed_pixels = 0, flush_calls = 0;
std::vector<bool> backlight_requests;
namespace hal_bridge {
void board_set_display_backlight_enabled(bool enabled) { backlight_requests.push_back(enabled); }
}

std::vector<uint8_t> snapshot(const std::string& path = {})
{
    lv_obj_update_layout(lv_screen_active());
    auto buf = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_RGB888);
    assert(buf && buf->header.w == 320 && buf->header.h == 240);
    std::vector<uint8_t> rgb;
    rgb.reserve(320 * 240 * 3);
    for (int y = 0; y < 240; ++y) for (int x = 0; x < 320; ++x) {
        const auto p = buf->data + y * buf->header.stride + x * 3;
        rgb.insert(rgb.end(), {p[2], p[1], p[0]});
    }
    if (!path.empty()) {
        auto file = fopen(path.c_str(), "wb");
        assert(file);
        fprintf(file, "P6\n320 240\n255\n");
        fwrite(rgb.data(), 1, rgb.size(), file);
        fclose(file);
    }
    lv_draw_buf_destroy(buf);
    return rgb;
}
lv_obj_t* find_label(lv_obj_t* parent, const char* starts_with)
{
    if (lv_obj_check_type(parent, &lv_label_class) &&
        std::strncmp(lv_label_get_text(parent), starts_with, std::strlen(starts_with)) == 0) return parent;
    for (uint32_t i = 0; i < lv_obj_get_child_count(parent); ++i)
        if (auto* found = find_label(lv_obj_get_child(parent, i), starts_with)) return found;
    return nullptr;
}
void tap(uint32_t now)
{
    collector::screen_touch(true, now, 160, 120);
    collector::screen_touch(false, now + 100);
}
void swipe(uint32_t now, int x1, int y1, int x2, int y2)
{
    collector::screen_touch(true, now, x1, y1);
    collector::screen_touch(true, now + 80, x2, y2);
    collector::screen_touch(false, now + 100);
}
int main(int argc, char** argv)
{
    assert(argc == 2);
    const std::string output = argv[1];
    lv_init();
    auto display = lv_display_create(320, 240);
    std::vector<uint8_t> framebuffer(320 * 20 * 2);
    lv_display_set_buffers(display, framebuffer.data(), nullptr, framebuffer.size(), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, [](lv_display_t* d, const lv_area_t* area, uint8_t*) {
        flushed_pixels += lv_area_get_size(area); ++flush_calls; lv_display_flush_ready(d);
    });
    collector::screen_create(lv_screen_active());
    collector::ScreenStatus status;
    status.version = "collector-0.3.7";
    status.ip = "192.168.1.255";
    status.token = "0123456789abcdef0123456789abcdef";
    status.channel = 6;
    status.frame = 123456;
    status.audio_ready = status.audio_streaming = true;
    status.wifi_connected = status.camera_working = true;
    status.camera_fps = 7.3f;
    status.remote_ready = status.remote_recent = true;
    status.remote_last_age_ms = 100;
    status.remote_received = 207;
    status.power_valid = true;
    status.battery_percent = 87;
    status.charging = true;
    status.imu_valid = true;
    status.accel[0] = 0.20f; status.accel[1] = -0.10f; status.accel[2] = 9.71f;
    status.gyro[0] = 0.2f; status.gyro[1] = -0.4f; status.gyro[2] = 0.0f;
    status.yaw_valid = true; status.yaw_deg = -12.3f;
    status.pitch_valid = false; status.pitch_deg = 13.0f; // Never display an invalid feedback value.
    collector::screen_update(status);
    assert(!collector::screen_showing_status());
    collector::screen_tick(0);
    auto emotion_label = find_label(lv_screen_active(), "情绪：");
    assert(emotion_label && std::strcmp(lv_label_get_text(emotion_label), "情绪：未连接") == 0);
    status.emotion = {true, collector::Emotion::Happy, collector::SttState::Listening, collector::EmotionEvidence::Both};
    collector::screen_update(status);
    assert(std::strcmp(lv_label_get_text(emotion_label), "推测：开心") == 0);
    snapshot(output + "/emotion-happy.ppm");
    status.emotion = {};
    collector::screen_update(status);
    const auto home = snapshot(output + "/home.ppm");
    lv_tick_inc(100); lv_timer_handler();
    flushed_pixels = flush_calls = 0;
    collector::screen_tick(3000);
    lv_tick_inc(100); lv_timer_handler();
    assert(flushed_pixels > 0 && flushed_pixels <= 14000 && flush_calls <= 28);
    printf("PASS: one animation redraw flushes %zu pixels in %zu regions\n", flushed_pixels, flush_calls);
    snapshot(output + "/home-quarter.ppm");
    collector::screen_tick(6000); snapshot(output + "/home-half.ppm");
    collector::screen_tick(9000); snapshot(output + "/home-three-quarter.ppm");
    collector::screen_tick(12000);
    assert(snapshot(output + "/home-lap.ppm") == home);
    for (uint32_t frame = 0; frame < 120; ++frame) {
        collector::screen_tick(12000 + frame * 100);
        char name[64]; snprintf(name, sizeof(name), "/animation-%03lu.ppm", static_cast<unsigned long>(frame));
        snapshot(output + name);
    }
    tap(25000);
    assert(collector::screen_showing_status() && !collector::screen_showing_log());
    const auto summary = snapshot(output + "/summary.ppm");
    collector::screen_tick(25800);
    assert(snapshot(output + "/summary-paused.ppm") == summary);
    // Unchanged sensor snapshots must not animate or redraw the new icons.
    lv_tick_inc(100); lv_timer_handler();
    flushed_pixels = flush_calls = 0;
    collector::screen_update(status);
    lv_tick_inc(100); lv_timer_handler();
    assert(flushed_pixels == 0);
    auto angle_label = find_label(lv_screen_active(), "Pitch ");
    assert(angle_label && std::strstr(lv_label_get_text(angle_label), "--"));
    assert(!std::strstr(lv_label_get_text(angle_label), "13.0"));
    // Home-only input does not replace the summary or queue a late reaction.
    collector::screen_show_reaction(collector::ScreenReaction::Heart, 25800);
    assert(snapshot() == summary);
    collector::screen_touch(true, 26000, 100, 100);
    collector::screen_touch(false, 26005);
    assert(collector::screen_showing_status());
    collector::screen_touch(true, 26300, 100, 100);
    collector::screen_touch(true, 27199, 100, 100);
    assert(!collector::screen_showing_pairing());
    collector::screen_touch(true, 27200, 100, 100);
    assert(collector::screen_showing_pairing());
    snapshot(output + "/pairing.ppm");
    collector::screen_touch(true, 27250, 100, 100);
    collector::screen_touch(false, 27300);
    assert(collector::screen_showing_pairing());
    tap(27400);
    assert(collector::screen_showing_status() && !collector::screen_showing_pairing());
    // Vertical movement and a horizontal out-and-back drag are not taps.
    swipe(27600, 160, 40, 158, 180);
    assert(collector::screen_showing_status() && !collector::screen_showing_log() && !collector::screen_showing_pairing());
    collector::screen_touch(true, 28000, 160, 120);
    collector::screen_touch(true, 28050, 240, 120);
    collector::screen_touch(true, 28100, 160, 120);
    collector::screen_touch(false, 28150);
    assert(collector::screen_showing_status() && !collector::screen_showing_log());
    collector::screen_touch(true, 28400, 160, 120);
    collector::screen_cancel_touch();
    collector::screen_touch(true, 28450, 161, 120);
    collector::screen_touch(false, 28500);
    assert(collector::screen_showing_status());
    std::string logs;
    for (int i = 0; i < 18; ++i) {
        char line[100]; std::snprintf(line, sizeof(line), "I (%05d) Camera: frame %d received\n", i * 100, i);
        logs += line;
    }
    logs += "I (01900) Remote: newest line";
    collector::screen_update_log(logs.c_str(), 1);
    swipe(29000, 270, 120, 50, 124);
    assert(collector::screen_showing_log());
    const auto first_log = snapshot(output + "/syslog.ppm");
    auto log_label = find_label(lv_screen_active(), "I (00000)");
    assert(log_label);
    auto log_viewport = lv_obj_get_parent(log_label);
    assert(lv_obj_get_scroll_y(log_viewport) > 0);
    assert(lv_obj_get_scroll_bottom(log_viewport) == 0);
    logs += "\nW (02000) Servo: feedback missing";
    collector::screen_update_log(logs.c_str(), 2);
    assert(snapshot(output + "/syslog-next.ppm") != first_log);
    assert(lv_obj_get_scroll_bottom(log_viewport) == 0);
    // Identical sequence/text should cause no invalidation or scrolling work.
    lv_tick_inc(100); lv_timer_handler();
    flushed_pixels = flush_calls = 0;
    collector::screen_update_log(logs.c_str(), 2);
    lv_tick_inc(100); lv_timer_handler();
    assert(flushed_pixels == 0);
    swipe(29400, 40, 100, 260, 102);
    assert(collector::screen_showing_status() && !collector::screen_showing_log());
    status.wifi_connected = status.camera_working = false;
    status.remote_recent = false; status.remote_last_age_ms = -1;
    status.audio_ready = status.audio_streaming = false;
    status.power_valid = status.imu_valid = status.yaw_valid = false;
    status.camera_fps = 0;
    collector::screen_update(status);
    snapshot(output + "/summary-unavailable.ppm");
    // Maximum plausible sensor magnitudes and address must remain in bounds.
    status.ip = "255.255.255.255"; status.wifi_connected = true;
    status.imu_valid = true;
    status.accel[0] = -156.91f; status.accel[1] = 156.91f; status.accel[2] = -156.91f;
    status.gyro[0] = -2000; status.gyro[1] = 2000; status.gyro[2] = -2000;
    collector::screen_update(status);
    snapshot(output + "/summary-limits.ppm");
    auto gyro_label = find_label(lv_screen_active(), "-2000.0");
    lv_point_t text_size;
    lv_text_get_size(&text_size, lv_label_get_text(gyro_label), lv_obj_get_style_text_font(gyro_label, LV_PART_MAIN),
                     0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    assert(text_size.x <= 214);
    auto accel_label = find_label(lv_screen_active(), "-156.9");
    lv_text_get_size(&text_size, lv_label_get_text(accel_label), lv_obj_get_style_text_font(accel_label, LV_PART_MAIN),
                     0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    assert(text_size.x <= 214);
    tap(30000);
    assert(!collector::screen_showing_status());
    // Reaction duration is exactly 1000 ms at the screen clock, no hidden queue.
    collector::screen_tick(30200);
    const auto before_reaction = snapshot(output + "/home-resumed.ppm");
    collector::screen_show_reaction(collector::ScreenReaction::Heart, 30200);
    const auto heart = snapshot(output + "/heart.ppm");
    assert(heart != before_reaction);
    collector::screen_tick(31199);
    assert(snapshot() == heart);
    lv_tick_inc(100); lv_timer_handler();
    flushed_pixels = flush_calls = 0;
    collector::screen_tick(31199);
    lv_tick_inc(100); lv_timer_handler();
    assert(flushed_pixels == 0);
    collector::screen_tick(31200);
    assert(snapshot(output + "/heart-expired.ppm") == before_reaction);
    collector::screen_show_reaction(collector::ScreenReaction::Reject, 31200);
    const auto reject = snapshot(output + "/reject.ppm");
    assert(reject != heart);
    // Reactions are opaque solid glyphs with no surrounding circle. Check a
    // substantial interior region so antialiased triangle seams cannot pass.
    for (const auto* pixels : {&heart, &reject}) {
        for (int y = 116; y <= 124; ++y) for (int x = 153; x <= 167; ++x) {
            const size_t at = (y * 320 + x) * 3;
            assert((*pixels)[at] >= 118 && (*pixels)[at + 1] == 255 && (*pixels)[at + 2] >= 148);
        }
        for (const auto point : {lv_point_t{62, 120}, lv_point_t{257, 120}, lv_point_t{160, 22}}) {
            const size_t at = (point.y * 320 + point.x) * 3;
            assert((*pixels)[at] == 0 && (*pixels)[at + 1] == 0 && (*pixels)[at + 2] == 0);
        }
    }
    collector::screen_tick(32199);
    assert(snapshot() == reject);
    collector::screen_show_reaction(collector::ScreenReaction::Heart, 32199);
    collector::screen_tick(33198);
    assert(snapshot() == heart);
    collector::screen_tick(33199);
    assert(snapshot() != heart);
    collector::screen_touch(true, 34300, 100, 100);
    collector::screen_touch(true, 35300, 100, 100);
    collector::screen_touch(false, 35320);
    assert(!collector::screen_showing_status() && !collector::screen_showing_pairing());
    tap(35600);
    collector::screen_touch(true, 36000, 100, 100);
    collector::screen_touch(true, 36100, 160, 100);
    collector::screen_touch(true, 37000, 100, 100);
    collector::screen_touch(false, 37020);
    assert(collector::screen_showing_status() && !collector::screen_showing_pairing());
    collector::screen_touch(true, 37400, 100, 100);
    collector::screen_cancel_touch();
    collector::screen_touch(true, 38400, 100, 100);
    collector::screen_touch(false, 38420);
    assert(collector::screen_showing_status() && !collector::screen_showing_pairing());
    // The actual LVGL partial-buffer flush path must stay idle while the
    // display is gated, but unrelated timers (including remote wake) must run.
    assert(collector::screen_enabled());
    unsigned service_ticks = 0;
    auto service_timer = lv_timer_create([](lv_timer_t* timer) {
        ++*static_cast<unsigned*>(lv_timer_get_user_data(timer));
    }, 20, &service_ticks);
    lv_tick_inc(100); lv_timer_handler();
    flushed_pixels = flush_calls = 0;
    collector::screen_touch(true, 38900, 100, 100);
    collector::screen_set_enabled(false, 39000);
    assert(!collector::screen_enabled() && !lv_display_is_invalidation_enabled(display));
    assert(backlight_requests == std::vector<bool>{false});
    collector::screen_set_enabled(false, 39020);
    {
        // The next foreground update owns no caller strings. Mutate/free the
        // inputs before wake to prove the hidden snapshot is self-contained.
        std::string temporary_ip = "192.0.2.33", temporary_version = "collector-0.3.9";
        std::string temporary_token = "fedcba9876543210fedcba9876543210";
        status.ip = temporary_ip.c_str(); status.version = temporary_version.c_str();
        status.token = temporary_token.c_str(); status.camera_working = true;
        status.camera_fps = 8.1f; status.power_valid = true; status.battery_percent = 63;
        collector::screen_update(status);
        temporary_ip.assign(temporary_ip.size(), 'x');
        temporary_token.assign(temporary_token.size(), 'x');
    }
    status.ip = "192.0.2.33"; status.version = "collector-0.3.9";
    status.token = "fedcba9876543210fedcba9876543210";
    assert(find_label(lv_screen_active(), "IP 255.255.255.255"));
    logs += "\nI (39000) Camera: newest while dark";
    collector::screen_update_log(logs.c_str(), 3);
    swipe(39200, 270, 120, 50, 120);
    tap(39600);
    collector::screen_touch(true, 40000, 100, 100);
    collector::screen_touch(true, 43000, 100, 100);
    collector::screen_show_reaction(collector::ScreenReaction::Heart, 43000);
    for (uint32_t now = 39000; now < 44000; now += 100) {
        collector::screen_tick(now);
        lv_tick_inc(100); lv_timer_handler();
    }
    assert(service_ticks >= 50 && flushed_pixels == 0 && flush_calls == 0);
    assert(collector::screen_showing_status() && !collector::screen_showing_log() && !collector::screen_showing_pairing());
    assert(backlight_requests == std::vector<bool>{false});
    collector::screen_set_enabled(true, 44000);
    assert(collector::screen_enabled() && lv_display_is_invalidation_enabled(display));
    assert((backlight_requests == std::vector<bool>{false, true}));
    assert(find_label(lv_screen_active(), "IP 192.0.2.33"));
    assert(find_label(lv_screen_active(), "63%"));
    assert(find_label(lv_screen_active(), "collector-0.3.9\nhttp://192.0.2.33:8080\n\nPairing token\nfedcba9876543210"));
    collector::screen_touch(true, 44020, 100, 100);
    collector::screen_touch(true, 45020, 100, 100);
    collector::screen_touch(false, 45040);
    assert(collector::screen_showing_status() && !collector::screen_showing_pairing());
    lv_tick_inc(100); lv_timer_handler();
    assert(flushed_pixels > 0);
    snapshot(output + "/summary-wake.ppm");
    swipe(45400, 270, 120, 50, 120);
    assert(collector::screen_showing_log());
    assert(std::strstr(lv_label_get_text(log_label), "newest while dark"));
    lv_tick_inc(100); lv_timer_handler();
    flushed_pixels = flush_calls = 0;
    collector::screen_set_enabled(false, 45600);
    logs += "\nI (45600) Remote: still receiving";
    collector::screen_update_log(logs.c_str(), 4);
    lv_tick_inc(100); lv_timer_handler();
    assert(flushed_pixels == 0 && collector::screen_showing_log());
    assert(!std::strstr(lv_label_get_text(log_label), "still receiving"));
    collector::screen_set_enabled(true, 45700);
    assert(collector::screen_showing_log());
    assert(std::strstr(lv_label_get_text(log_label), "still receiving"));
    assert(lv_obj_get_scroll_bottom(log_viewport) == 0);
    collector::screen_touch(false, 45720);
    // Rapid transitions before the next LVGL service keep invalidation
    // balanced and issue both hardware gate changes, including the wake.
    collector::screen_set_enabled(false, 45721);
    collector::screen_set_enabled(true, 45722);
    assert(lv_display_is_invalidation_enabled(display));
    assert(backlight_requests.size() == 6 && !backlight_requests[4] && backlight_requests[5]);
    collector::screen_touch(false, 45740);
    swipe(46000, 40, 100, 260, 100);
    collector::screen_touch(true, 46400, 100, 100);
    collector::screen_touch(true, 47300, 100, 100);
    collector::screen_touch(false, 47320);
    assert(collector::screen_showing_pairing());
    collector::screen_set_enabled(false, 47400);
    collector::screen_set_enabled(true, 57400);
    collector::screen_touch(false, 57420);
    assert(collector::screen_showing_pairing());
    tap(57800); tap(58200);
    assert(!collector::screen_showing_status());
    collector::screen_tick(58300);
    const auto before_dark_reaction = snapshot();
    collector::screen_show_reaction(collector::ScreenReaction::Heart, 58300);
    assert(snapshot() != before_dark_reaction);
    lv_tick_inc(100); lv_timer_handler();
    flushed_pixels = flush_calls = 0;
    collector::screen_set_enabled(false, 58320);
    collector::screen_show_reaction(collector::ScreenReaction::Reject, 58350);
    collector::screen_tick(68320);
    lv_tick_inc(100); lv_timer_handler();
    assert(flushed_pixels == 0);
    collector::screen_set_enabled(true, 68320);
    collector::screen_tick(68320);
    assert(snapshot() == before_dark_reaction); // no reaction replay or ring jump
    collector::screen_tick(68420);
    assert(snapshot() != before_dark_reaction);
    lv_tick_inc(100); lv_timer_handler();
    lv_timer_delete(service_timer);
    puts("PASS: off has zero partial flushes, service timers keep running, all pages/latest owned status/log resume, rapid gates balance, touch waits for release, reactions drop, ring phase pauses");
    // M12 face view: opens from home, draws boxes, ignores reactions, voice or tap returns home.
    collector::screen_show_faces(true, 68500);
    assert(collector::screen_showing_faces() && collector::screen_showing_status());
    collector::ScreenFace faces[2];
    faces[0].x = 40; faces[0].y = 50; faces[0].w = 60; faces[0].h = 70;
    faces[1].x = 200; faces[1].y = 100; faces[1].w = 90; faces[1].h = 100;
    collector::screen_update_faces(faces, 2, "faces 2   (turn off: home)");
    const auto face_view = snapshot(output + "/faces.ppm");
    auto pixel = [&](int x, int y) { return &face_view[(y * 320 + x) * 3]; };
    assert(pixel(41, 85)[1] > 200 && pixel(41, 85)[0] < 0x90);   // green 3 px border of box 0
    assert(pixel(70, 85)[0] == 0 && pixel(70, 85)[1] == 0);       // inside stays black
    assert(pixel(201, 150)[1] > 200);                             // box 1
    collector::screen_show_reaction(collector::ScreenReaction::Heart, 68600);
    assert(snapshot() == face_view);                              // reactions are home-only
    collector::screen_update_faces(nullptr, 0, "no face   (turn off: home)");
    assert(snapshot() != face_view);
    collector::screen_show_faces(false, 68700);
    assert(!collector::screen_showing_faces() && !collector::screen_showing_status());
    collector::screen_show_faces(true, 68800);
    assert(collector::screen_showing_faces());
    collector::screen_touch(false, 68900); // the earlier screen on/off waits for one release
    tap(69000);
    assert(!collector::screen_showing_faces() && !collector::screen_showing_status());
    puts("PASS: M12 face view boxes, home-only reactions, voice and tap exits");
    // Every sample uses true black in all four corners.
    for (const auto* pixels : {&home, &heart, &reject})
        for (size_t pos : {size_t(0), size_t(319 * 3), size_t(239 * 320 * 3), size_t((240 * 320 - 1) * 3)})
            assert((*pixels)[pos] == 0 && (*pixels)[pos + 1] == 0 && (*pixels)[pos + 2] == 0);
    puts("PASS: 120 animation frames, summary-first, gestures/cancel, hold-to-pair, live log tail, invalid values, 1000 ms solid reactions without a ring, graphical summary, black background");
    lv_deinit();
}
