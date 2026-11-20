// Global swipes and the two new pages (fw/ui_pages.h, collector-0.4.94): which gesture opens which page from which
// page, the opposite swipe and the back control close it, the idle timeout goes home, and a picker tap asks the
// robot's own mode rules (mode_rules.h) for the right select. Quick settings: volume steps, billing digits 9 / 0,
// the "last:" line.
#include "ui_pages.h"
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace collector::ui;
using collector::modes::rules::Dir;
using collector::modes::rules::Input;
using collector::modes::rules::Mode;
using collector::modes::rules::State;
using collector::modes::rules::transition;

namespace {
Nav at(Page p, uint32_t now = 1000)
{
    Nav n;
    if (p == Page::Wifi || p == Page::WifiKey) n.go(Page::Pairing, now);
    if (p == Page::WifiKey) n.go(Page::Wifi, now);
    n.go(p, now);
    return n;
}

void home_gestures()
{
    const Nav home;
    assert(home.page() == Page::Home);
    assert(home.swipe(Gesture::SwipeDown) == Page::Quick);
    assert(home.swipe(Gesture::SwipeUp) == Page::Picker);
    assert(home.swipe(Gesture::SwipeLeft) == Page::Summary);
    // Right, taps and double taps do not navigate (taps are the mode's).
    assert(home.swipe(Gesture::SwipeRight) == Page::Home);
    assert(home.swipe(Gesture::Tap) == Page::Home && home.swipe(Gesture::DoubleTap) == Page::Home);
}

void opposite_swipes_and_back()
{
    // Each new page closes with the swipe opposite to the one that opened it, and only that one.
    assert(at(Page::Quick).swipe(Gesture::SwipeUp) == Page::Home);
    for (Gesture g : {Gesture::SwipeDown, Gesture::SwipeLeft, Gesture::SwipeRight, Gesture::Tap})
        assert(at(Page::Quick).swipe(g) == Page::Quick);
    assert(at(Page::Picker).swipe(Gesture::SwipeDown) == Page::Home);
    for (Gesture g : {Gesture::SwipeUp, Gesture::SwipeLeft, Gesture::SwipeRight, Gesture::Tap})
        assert(at(Page::Picker).swipe(g) == Page::Picker);
    assert(at(Page::Summary).swipe(Gesture::SwipeRight) == Page::Home);
    // The old moves stay: summary left = log, log right = summary, pairing left = Wi-Fi.
    assert(at(Page::Summary).swipe(Gesture::SwipeLeft) == Page::Log);
    assert(at(Page::Log).swipe(Gesture::SwipeRight) == Page::Summary);
    assert(at(Page::Pairing).swipe(Gesture::SwipeLeft) == Page::Wifi);
    assert(at(Page::Summary).swipe(Gesture::SwipeDown) == Page::Summary);   // no global swipes off home
    assert(at(Page::Log).swipe(Gesture::SwipeUp) == Page::Log);
    // Back controls.
    assert(at(Page::Quick).back() == Page::Home && at(Page::Picker).back() == Page::Home);
    assert(at(Page::WifiKey).back() == Page::Wifi && at(Page::Pairing).back() == Page::Summary);
}

void wifi_returns_to_its_opener()
{
    Nav n;
    n.go(Page::Quick, 0);
    n.go(Page::Wifi, 10);                   // the quick page's Wi-Fi button
    assert(n.wifi_back() == Page::Quick && n.back() == Page::Quick && n.swipe(Gesture::SwipeRight) == Page::Quick);
    n.go(Page::WifiKey, 20);                // into the keypad and back keeps the opener
    n.go(n.back(), 30);
    assert(n.page() == Page::Wifi && n.wifi_back() == Page::Quick);
    n.go(n.back(), 40);
    assert(n.page() == Page::Quick);
    // From pairing (swipe left) it goes back to pairing.
    Nav p;
    p.go(Page::Summary, 0); p.go(Page::Pairing, 1); p.go(p.swipe(Gesture::SwipeLeft), 2);
    assert(p.page() == Page::Wifi && p.back() == Page::Pairing);
}

void idle_timeout()
{
    Nav n;
    assert(!n.idle(1000000));               // home never times out
    n.go(Page::Quick, 1000);
    assert(!n.idle(1000 + kIdleMs - 1) && n.idle(1000 + kIdleMs));
    n.touched(20000);                       // a finger on the page keeps it open
    assert(!n.idle(1000 + kIdleMs) && n.idle(20000 + kIdleMs));
    Nav m = at(Page::Picker, 5000);
    assert(!m.idle(5000 + kIdleMs - 1) && m.idle(5000 + kIdleMs));
    // The pages from before 0.4.94 never time out (the log stays up for live debugging).
    for (Page p : {Page::Summary, Page::Log, Page::Pairing}) assert(!at(p, 5000).idle(5000 + 10 * kTypingIdleMs));
    // Typing a password gets longer; the face page follows its own request.
    Nav w = at(Page::WifiKey, 0);
    assert(!w.idle(kIdleMs) && w.idle(kTypingIdleMs));
    assert(!at(Page::Faces, 0).idle(10 * kTypingIdleMs));
    // Wraps with the millisecond clock.
    Nav z;
    z.go(Page::Picker, 0xFFFFFF00u);
    assert(!z.idle(0x100) && z.idle(0xFFFFFF00u + kIdleMs));
}

void strokes()
{
    assert(stroke(200, 0, 80, 4, 80) == Gesture::SwipeDown);
    assert(stroke(200, 0, -80, 4, 80) == Gesture::SwipeUp);
    assert(stroke(200, -80, 5, 80, 5) == Gesture::SwipeLeft);
    assert(stroke(200, 80, 5, 80, 5) == Gesture::SwipeRight);
    assert(stroke(100, 3, 2, 3, 2) == Gesture::Tap);
    assert(stroke(20, 3, 2, 3, 2) == Gesture::None);        // too short
    assert(stroke(800, 3, 2, 3, 2) == Gesture::None);       // a hold is not a tap
    assert(stroke(200, 60, 60, 60, 60) == Gesture::None);   // diagonal
    assert(stroke(200, 0, 80, 60, 80) == Gesture::None);    // strayed sideways
}

void picker()
{
    // One tile since collector-0.5.19 (owner 2026-11-20: Docking is the only mode), in the old grid's first slot.
    assert(kTiles == 1 && kTileModes[0].mode == Mode::Crowd);
    assert(!std::strcmp(tile_label(0), "DOCK") && !std::strcmp(tile_name_zh(0), "Docking") && tile_color(0) == 0x30D060);
    const Rect r = tile_rect(0);
    assert(picker_hit(r.x + r.w / 2, r.y + r.h / 2) == 0);
    assert(picker_hit(r.x, r.y) == 0 && picker_hit(r.x + r.w - 1, r.y + r.h - 1) == 0);
    assert(r.x >= 0 && r.x + r.w <= 320 && r.y >= kTitleH && r.y + r.h <= 237);   // above the ribbon
    assert(picker_hit(10, 10) == -1 && picker_hit(106, 50) == -1 && picker_hit(160, 130) == -1);   // title, old slots
    // Highlight: Docking is always the current mode.
    assert(tile_current(0, State{}));
    // A tap goes through the robot's own rules as select: Docking again = already, nothing changes.
    const auto o = transition(State{}, Input::Select, tile_value(0), false);
    assert(o.state == State{} && o.code == collector::modes::rules::Code::Already && !o.n_effects);
}

void quick()
{
    assert(quick_hit(20, 10) == Quick::Back);
    assert(quick_hit(150, 50) == Quick::VolumeDown && quick_hit(280, 50) == Quick::VolumeUp);
    assert(quick_hit(260, 90) == Quick::Wifi);
    assert(quick_hit(140, 130) == Quick::AllOn && quick_hit(250, 130) == Quick::AllOff);
    assert(quick_hit(250, 190) == Quick::Hearing);
    assert(quick_hit(60, 60) == Quick::None && quick_hit(300, 10) == Quick::None);   // a label; the badge corner
    for (const Rect& r : {kVolumeDown, kVolumeUp, kWifiButton, kAllOn, kAllOff, kHearing})
        assert(r.x >= 0 && r.x + r.w <= 320 && r.y >= kTitleH && r.y + r.h <= 237);
    // Volume: one step of 10 within 10..100, as the ASR volume words.
    assert(volume_after(Quick::VolumeUp, 80) == 90 && volume_after(Quick::VolumeUp, 100) == 100);
    assert(volume_after(Quick::VolumeDown, 80) == 70 && volume_after(Quick::VolumeDown, 10) == 10);
    assert(volume_after(Quick::VolumeDown, 15) == 10 && volume_after(Quick::Wifi, 50) == -1);
    // Billing: 全开 = 9, 全关 = 0, nothing else.
    assert(billing_digit(Quick::AllOn) == 9 && billing_digit(Quick::AllOff) == 0 && billing_digit(Quick::Hearing) == -1);
    char line[48];
    billing_line(line, sizeof line, -1, "ok", 5);
    assert(!line[0]);
    billing_line(line, sizeof line, 9, "ok", 5);
    assert(!std::strcmp(line, "last: ALL ON  5s ago"));
    billing_line(line, sizeof line, 0, "ok", 125);
    assert(!std::strcmp(line, "last: ALL OFF  2m ago"));
    billing_line(line, sizeof line, 9, "senses_on_page", 7200);
    assert(!std::strcmp(line, "last: ALL ON senses_on_page  2h ago"));
    billing_line(line, sizeof line, 0, nullptr, -3);
    assert(!std::strcmp(line, "last: ALL OFF  0s ago"));
    // 全开 takes a second tap within 2 s; 全关 is one tap and disarms; a lapsed arm reverts once.
    BillingConfirm c;
    assert(c.tap(Quick::AllOn, 1000) == -1 && c.armed(1000) && c.armed(2999) && !c.armed(3000));
    assert(c.tap(Quick::AllOn, 2999) == 9 && !c.armed(2999));
    assert(c.tap(Quick::AllOn, 5000) == -1 && c.tap(Quick::AllOn, 7000) == -1);   // too late: arms again
    assert(c.armed(7000) && c.tap(Quick::AllOff, 7100) == 0 && !c.armed(7100) && !c.lapsed(9100));
    assert(c.tap(Quick::AllOn, 10000) == -1 && !c.lapsed(11999) && c.lapsed(12000) && !c.lapsed(12001));
    assert(c.tap(Quick::AllOn, 12002) == -1);                                     // after a lapse: arm, not send
    assert(c.tap(Quick::Hearing, 12003) == -1 && c.armed(12003));                 // other buttons leave it
    c.reset();
    assert(!c.armed(12004) && c.tap(Quick::AllOff, 12005) == 0);
    Quick none = Quick::None;
    assert(c.tap(none, 0) == -1);
    // Wraps with the millisecond clock.
    BillingConfirm w;
    assert(w.tap(Quick::AllOn, 0xFFFFFF00u) == -1 && w.tap(Quick::AllOn, 0x100) == 9);
    // 0.4.95 Chinese line (CJK font only).
    char zh[96];
    billing_line(zh, sizeof zh, 9, "ok", 125, true);
    assert(!std::strcmp(zh, "上次：全开 2 分钟前"));
    billing_line(zh, sizeof zh, 0, "ok", 5, true);
    assert(!std::strcmp(zh, "上次：全关 5 秒前"));
    billing_line(zh, sizeof zh, 9, "senses_on_page", 7200, true);
    assert(!std::strcmp(zh, "上次：全开 senses_on_page 2 小时前"));
    billing_line(zh, sizeof zh, -1, "ok", 5, true);
    assert(!zh[0]);
}
}

int main()
{
    home_gestures();
    opposite_swipes_and_back();
    wifi_returns_to_its_opener();
    idle_timeout();
    strokes();
    picker();
    quick();
    std::puts("ui pages ok");
}
