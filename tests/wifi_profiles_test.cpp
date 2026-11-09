/* SPDX-License-Identifier: MIT */
#include "../upstream/StackChan/firmware/main/collector/wifi_profiles.h"
#include <cassert>
#include <iostream>
struct Profile { std::string ssid, password; };
int main()
{
    using namespace collector;
    std::vector<Profile> profiles;
    assert(append_wifi_profile(profiles, Profile{"preferred-example", "new-pass-example"}));
    assert(append_wifi_profile(profiles, Profile{"legacy-example", "old-pass-example"}));
    assert(!append_wifi_profile(profiles, Profile{"preferred-example", "stale-pass-example"}));
    assert(append_wifi_profile(profiles, Profile{"factory-example", "factory-pass-example"}));
    assert(profiles.size() == 3 && profiles[0].ssid == "preferred-example" && profiles[0].password == "new-pass-example");
    assert(profiles[1].ssid == "legacy-example" && profiles[2].ssid == "factory-example");
    assert(!wifi_credentials_valid("", "valid-pass"));
    assert(!wifi_credentials_valid(std::string(33, 's'), "valid-pass"));
    assert(!wifi_credentials_valid("example", "short"));
    assert(!wifi_credentials_valid("example", std::string(64, 'z')));
    assert(wifi_credentials_valid("example", std::string(64, 'a')));
    assert(wifi_credentials_valid("open-example", ""));
    assert(!wifi_credentials_valid(std::string("bad\0ssid", 8), "valid-pass"));
    assert(!wifi_credentials_valid("example", std::string("bad\0password", 12)));
    // Placeholder credentials only; never real network secrets.
    WifiProfileList list;
    assert(list.items().empty() && list.index_of("home-example") < 0 && !list.remove("home-example") && !list.prefer("home-example"));
    assert(!list.add("", "placeholder-1", true) && !list.add("home-example", "short", true));
    assert(list.add("home-example", "placeholder-1", false) && list.items().size() == 1 && list.index_of("home-example") == 0);
    assert(list.add("office-example", "placeholder-2", false) && list.items()[1].ssid == "office-example");
    assert(list.add("phone-example", "placeholder-3", true) && list.items()[0].ssid == "phone-example" && list.items().size() == 3);
    assert(list.add("office-example", "placeholder-4", false) && list.items().size() == 3 && list.items()[2].ssid == "office-example" && list.items()[2].password == "placeholder-4");
    assert(list.prefer("office-example") && list.items()[0].ssid == "office-example" && list.items()[1].ssid == "phone-example");
    assert(list.add("open-example", "", false) && list.items().back().ssid == "open-example");
    assert(list.add("fifth-example", "placeholder-5", false) && list.add("sixth-example", "placeholder-6", false) && list.items().size() == 6);
    assert(!list.add("seventh-example", "placeholder-7", true)); // limit reached
    assert(list.add("phone-example", "placeholder-8", true) && list.items().size() == 6 && list.items()[0].ssid == "phone-example"); // replace keeps the slot
    assert(list.remove("open-example") && list.items().size() == 5 && list.index_of("open-example") < 0);
    list.clear(); assert(list.items().empty());
    std::cout << "PASS: priority ordering, retained fallbacks, duplicate password precedence, invalid/open/PSK boundaries, profile list limits\n";
}
