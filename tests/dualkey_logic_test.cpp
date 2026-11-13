// DualKey remote: press grouping, joystick direction debounce, signal queue/ack, back-off.
// Pure logic from dualkey/main/dualkey_logic.h, no ESP-IDF.
#include "../dualkey/main/dualkey_logic.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
using namespace dualkey;

namespace {

std::string rating_json(const RatingGroup& g) {
    char buf[kBodyMax];
    int n = format_rating(g, buf, sizeof(buf));
    assert(n > 0 && n < (int)sizeof(buf));
    return buf;
}

void test_debounce() {
    Debouncer d(3);
    assert(d.update(true) == 0 && d.update(true) == 0);
    assert(d.update(true) == +1 && d.pressed());
    // A two-sample bounce does not release.
    assert(d.update(false) == 0 && d.update(false) == 0 && d.update(true) == 0);
    assert(d.pressed());
    assert(d.update(false) == 0 && d.update(false) == 0 && d.update(false) == -1 && !d.pressed());
}

void test_single_press_closes_after_500ms() {
    RatingGrouper g;
    RatingGroup out;
    assert(!g.press(+1, 1000, &out));
    assert(g.is_open());
    assert(!g.poll(1499, &out));
    assert(g.poll(1500, &out));
    assert(!g.is_open());
    assert(out.polarity == 1 && out.count == 1 && !out.capped && out.first_ms == 1000);
    assert(rating_json(out) ==
           "\"kind\":\"rating\",\"value\":{\"polarity\":1,\"count\":1,\"presses_ms\":[0],\"capped\":false}");
}

void test_three_presses_one_group() {
    RatingGrouper g;
    RatingGroup out;
    assert(!g.press(-1, 0, &out));
    assert(!g.press(-1, 180, &out));
    assert(!g.press(-1, 530, &out));  // 350 ms after the previous press: still the same group
    assert(!g.poll(1029, &out));
    assert(g.poll(1030, &out));
    assert(out.polarity == -1 && out.count == 3);
    assert(rating_json(out) ==
           "\"kind\":\"rating\",\"value\":{\"polarity\":-1,\"count\":3,\"presses_ms\":[0,180,530],\"capped\":false}");
}

void test_gap_over_350_starts_new_group() {
    RatingGrouper g;
    RatingGroup out;
    assert(!g.press(+1, 0, &out));
    // 351..499 ms: not the same group, and the old group closes right now (no undefined zone).
    assert(g.press(+1, 420, &out));
    assert(out.count == 1 && out.first_ms == 0);
    assert(g.is_open() && g.current().count == 1 && g.current().first_ms == 420);
    assert(g.poll(920, &out) && out.count == 1 && out.first_ms == 420);
}

void test_other_key_closes_group() {
    RatingGrouper g;
    RatingGroup out;
    assert(!g.press(+1, 0, &out));
    assert(!g.press(+1, 100, &out));
    assert(g.press(-1, 200, &out));
    assert(out.polarity == 1 && out.count == 2);
    assert(g.current().polarity == -1 && g.current().count == 1);
}

void test_cap_at_five() {
    RatingGrouper g;
    RatingGroup out;
    for (int i = 0; i < 7; ++i) assert(!g.press(+1, (uint32_t)(i * 200), &out));
    // Extra presses still extend the group: closes 500 ms after the 7th press (1200).
    assert(!g.poll(1699, &out));
    assert(g.poll(1700, &out));
    assert(out.count == 5 && out.capped);
    assert(rating_json(out) ==
           "\"kind\":\"rating\",\"value\":{\"polarity\":1,\"count\":5,\"presses_ms\":[0,200,400,600,800],\"capped\":true}");
}

void test_cancel_and_wraparound() {
    RatingGrouper g;
    RatingGroup out;
    assert(!g.press(+1, 0xFFFFFF00u, &out));
    assert(!g.press(+1, 0xFFFFFF00u + 300, &out));  // wraps past zero
    assert(g.poll(0xFFFFFF00u + 800, &out) && out.count == 2 && out.presses_ms[1] == 300);
    assert(!g.press(-1, 10, &out));
    g.cancel();
    assert(!g.is_open() && !g.poll(10000, &out));
}

void test_direction_basic() {
    DirDetector d;
    uint32_t start = 0;
    // Not armed until the stick has been seen at centre.
    assert(d.update(0, 127, 0, &start) == Dir::None);
    assert(d.update(0, 127, 500, &start) == Dir::None);
    assert(d.update(0, 0, 600, &start) == Dir::None && d.armed());
    assert(d.update(0, 100, 700, &start) == Dir::None);
    assert(d.update(0, 100, 840, &start) == Dir::None);
    assert(d.update(0, 100, 850, &start) == Dir::Up && start == 700);
    // Held: no second event, even after a long time.
    assert(d.update(0, 127, 3000, &start) == Dir::None);
    // Must return to centre to re-arm; hovering at 40 is not centre.
    assert(d.update(0, 40, 3100, &start) == Dir::None && !d.armed());
    assert(d.update(0, 100, 3200, &start) == Dir::None);
    assert(d.update(0, 100, 3400, &start) == Dir::None);
    assert(d.update(5, -5, 3500, &start) == Dir::None && d.armed());
    assert(d.update(-90, 10, 3600, &start) == Dir::None);
    assert(d.update(-90, 10, 3760, &start) == Dir::Left && start == 3600);
    assert(d.update(0, 0, 3800, &start) == Dir::None);
    assert(d.update(90, 0, 3900, &start) == Dir::None);
    assert(d.update(90, 0, 4050, &start) == Dir::Right);
    assert(d.update(0, 0, 4100, &start) == Dir::None);
    assert(d.update(0, -90, 4200, &start) == Dir::None);
    assert(d.update(0, -90, 4350, &start) == Dir::Down);
    assert(std::string(dir_name(Dir::Down)) == "down" && std::string(dir_name(Dir::Up)) == "up");
}

void test_direction_short_flick_and_hysteresis() {
    DirDetector d;
    uint32_t start = 0;
    d.update(0, 0, 0, &start);
    // A 100 ms flick does not count.
    assert(d.update(0, 100, 100, &start) == Dir::None);
    assert(d.update(0, 0, 200, &start) == Dir::None);
    assert(d.update(0, 0, 400, &start) == Dir::None);
    // Push past 76, then sag to 65 (above the 60 exit level): still counts.
    assert(d.update(0, 80, 1000, &start) == Dir::None);
    assert(d.update(0, 65, 1100, &start) == Dir::None);
    assert(d.update(0, 65, 1150, &start) == Dir::Up && start == 1000);
    d.update(0, 0, 1200, &start);
    // Below 76 never starts a candidate.
    assert(d.update(0, 70, 1300, &start) == Dir::None);
    assert(d.update(0, 70, 1600, &start) == Dir::None);
    // Sag below 60 drops the candidate; the timer restarts at the next push past 76.
    assert(d.update(0, 90, 1700, &start) == Dir::None);
    assert(d.update(0, 50, 1800, &start) == Dir::None);
    assert(d.update(0, 90, 1820, &start) == Dir::None);
    assert(d.update(0, 90, 1900, &start) == Dir::None);
    assert(d.update(0, 90, 1970, &start) == Dir::Up && start == 1820);
}

void test_direction_diagonal_and_switch() {
    DirDetector d;
    uint32_t start = 0;
    d.update(0, 0, 0, &start);
    // Diagonal push never fires.
    for (uint32_t t = 100; t < 1000; t += 20) assert(d.update(90, 90, t, &start) == Dir::None);
    d.update(0, 0, 1000, &start);
    // Swinging from up to right restarts the hold timer.
    assert(d.update(0, 100, 1100, &start) == Dir::None);
    assert(d.update(100, 10, 1200, &start) == Dir::None);
    assert(d.update(100, 10, 1340, &start) == Dir::None);
    assert(d.update(100, 10, 1350, &start) == Dir::Right && start == 1200);
}

void test_rotation() {
    int x = 100, y = 0;  // reading points right
    rotate_reading(90, x, y);
    assert(x == 0 && y == 100);  // 90: right becomes up
    x = 0; y = 100; rotate_reading(180, x, y); assert(x == 0 && y == -100);
    x = 0; y = 100; rotate_reading(270, x, y); assert(x == 100 && y == 0);
    x = 3; y = 4; rotate_reading(0, x, y); assert(x == 3 && y == 4);
}

// User test 2026-11-13 (mock server): with no correction, pushing up arrived as "right", down as "left",
// left as "up", right as "down". The built-in mount correction must turn those raw readings back into
// the physical direction; joystick_rotate (NVS) adds on top of it.
dualkey::Dir physical(int raw_x, int raw_y, int user_rotate = 0) {
    int x = raw_x, y = raw_y;
    mount_correct(user_rotate, x, y);
    DirDetector d;
    uint32_t start = 0;
    d.update(0, 0, 0, &start);
    d.update(x, y, 10, &start);
    return d.update(x, y, 200, &start);
}

void test_mount_correction() {
    assert(kMountRotate == 90);
    assert(physical(100, 0) == Dir::Up);      // arrived "right" <- pushed up
    assert(physical(-100, 0) == Dir::Down);   // arrived "left"  <- pushed down
    assert(physical(0, 100) == Dir::Left);    // arrived "up"    <- pushed left
    assert(physical(0, -100) == Dir::Right);  // arrived "down"  <- pushed right
    // joystick_rotate is an extra offset; 270 cancels the built-in 90.
    assert(physical(100, 0, 270) == Dir::Right);
    assert(physical(0, 100, 180) == Dir::Right);
}

void test_queue_ack_and_resend() {
    SignalQueue q;
    uint32_t s1 = q.push("\"kind\":\"click\",\"value\":{\"type\":\"single\"}", 100);
    uint32_t s2 = q.push("\"kind\":\"mode\",\"value\":{\"direction\":\"up\"}", 200);
    assert(s1 == 1 && s2 == 2 && q.size() == 2);
    const Signal* n = q.next_unsent();
    assert(n && n->seq == 1);
    char out[kFrameMax];
    int len = render_signal(*n, 350, out, sizeof(out));
    assert(len > 0);
    assert(std::string(out) ==
           "{\"type\":\"signal\",\"seq\":1,\"age_ms\":250,\"kind\":\"click\",\"value\":{\"type\":\"single\"}}");
    q.mark_sent(1);
    assert(q.next_unsent()->seq == 2);
    q.mark_sent(2);
    assert(q.next_unsent() == nullptr);
    // Ack of the second one first (out of order) leaves the first waiting.
    assert(q.ack(2) && q.size() == 1 && !q.ack(2) && !q.ack(99));
    // Reconnect: everything unacked goes again, oldest first.
    q.mark_unsent();
    assert(q.next_unsent()->seq == 1);
    assert(q.ack(1) && q.size() == 0 && q.next_unsent() == nullptr);
    assert(q.dropped() == 0 && q.last_seq() == 2);
}

void test_queue_drops_oldest() {
    SignalQueue q;
    for (int i = 0; i < 40; ++i) q.push("\"kind\":\"click\",\"value\":{\"type\":\"single\"}", (uint32_t)i);
    assert(q.size() == kQueueCap && q.dropped() == 8);
    assert(q.next_unsent()->seq == 9);  // seqs 1..8 were dropped
    assert(q.at(kQueueCap - 1).seq == 40);
}

void test_mode_and_click_bodies() {
    char buf[kBodyMax];
    format_mode(Dir::Left, buf, sizeof(buf));
    assert(std::string(buf) == "\"kind\":\"mode\",\"value\":{\"direction\":\"left\"}");
    format_click(ClickType::Double, buf, sizeof(buf));
    assert(std::string(buf) == "\"kind\":\"click\",\"value\":{\"type\":\"double\"}");
    format_click(ClickType::Long, buf, sizeof(buf));
    assert(std::string(buf) == "\"kind\":\"click\",\"value\":{\"type\":\"long\"}");
}

void test_backoff() {
    Backoff b;
    assert(b.next() == 1000 && b.next() == 2000 && b.next() == 4000 && b.next() == 8000);
    assert(b.next() == 16000 && b.next() == 30000 && b.next() == 30000);
    b.reset();
    assert(b.next() == 1000);
}

// Joystick long press. The joystick's own firmware only reports "long" after 3 s (its shortest setting);
// a release before 3 s is a single click (user test 2026-11-13: held 1429 ms -> E0 value 0, held 4037 ms ->
// E0 value 2 at 3018 ms). ClickFuser fires our own long press from the polled button level at 1 s and drops
// the joystick's own trailing event for that press.
void test_click_fuser_short_click_passes_through() {
    ClickFuser f;
    ClickType out;
    assert(!f.level(true, 1000, &out));
    assert(!f.level(false, 1250, &out));
    assert(f.joystick_event(ClickType::Single, 1450, &out) && out == ClickType::Single);
    assert(!f.level(true, 2000, &out) && !f.level(false, 2170, &out) && !f.level(true, 2340, &out));
    assert(!f.level(false, 2510, &out));
    assert(f.joystick_event(ClickType::Double, 2520, &out) && out == ClickType::Double);
}

void test_click_fuser_long_at_one_second() {
    ClickFuser f;
    ClickType out;
    assert(!f.level(true, 1000, &out));
    assert(!f.level(true, 1999, &out));
    assert(f.level(true, 2000, &out) && out == ClickType::Long);  // fires while still held
    assert(!f.level(true, 2400, &out));                            // once per press
    assert(!f.level(false, 2429, &out));                            // released at 1.43 s
    // The joystick reports that press as a single ~200 ms later: dropped.
    assert(!f.joystick_event(ClickType::Single, 2620, &out));
    // The next real click is reported normally.
    assert(!f.level(true, 4000, &out) && !f.level(false, 4200, &out));
    assert(f.joystick_event(ClickType::Single, 4400, &out) && out == ClickType::Single);
}

void test_click_fuser_hold_past_three_seconds() {
    ClickFuser f;
    ClickType out;
    f.level(true, 0, &out);
    assert(f.level(true, 1000, &out) && out == ClickType::Long);
    // The joystick's own long arrives at 3 s while still held: dropped, not a second long.
    assert(!f.joystick_event(ClickType::Long, 3018, &out));
    assert(!f.level(false, 4037, &out));
    // Nothing else is pending: the next click right after is reported.
    assert(!f.level(true, 4500, &out) && !f.level(false, 4650, &out));
    assert(f.joystick_event(ClickType::Single, 4850, &out) && out == ClickType::Single);
}

void test_click_fuser_suppression_expires() {
    ClickFuser f;
    ClickType out;
    f.level(true, 0, &out);
    assert(f.level(true, 1000, &out));
    f.level(false, 1500, &out);
    // The joystick's trailing single was lost; a click 2 s later must not be eaten.
    assert(!f.level(true, 3500, &out) && !f.level(false, 3700, &out));
    assert(f.joystick_event(ClickType::Single, 3900, &out) && out == ClickType::Single);
}

void test_click_fuser_joystick_long_without_level() {
    // If level polling missed the hold, the joystick's own long still counts (once).
    ClickFuser f;
    ClickType out;
    assert(f.joystick_event(ClickType::Long, 3000, &out) && out == ClickType::Long);
}

}  // namespace

int main() {
    test_debounce();
    test_single_press_closes_after_500ms();
    test_three_presses_one_group();
    test_gap_over_350_starts_new_group();
    test_other_key_closes_group();
    test_cap_at_five();
    test_cancel_and_wraparound();
    test_direction_basic();
    test_direction_short_flick_and_hysteresis();
    test_direction_diagonal_and_switch();
    test_rotation();
    test_mount_correction();
    test_queue_ack_and_resend();
    test_queue_drops_oldest();
    test_mode_and_click_bodies();
    test_backoff();
    test_click_fuser_short_click_passes_through();
    test_click_fuser_long_at_one_second();
    test_click_fuser_hold_past_three_seconds();
    test_click_fuser_suppression_expires();
    test_click_fuser_joystick_long_without_level();
    std::puts("dualkey_logic_test: all passed");
    return 0;
}
