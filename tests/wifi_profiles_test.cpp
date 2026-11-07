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
    std::cout << "PASS: priority ordering, retained fallbacks, duplicate password precedence, invalid/open/PSK boundaries\n";
}
