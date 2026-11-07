/* SPDX-License-Identifier: MIT */
#pragma once
#include <algorithm>
#include <string>
#include <vector>

namespace collector {
inline bool wifi_credentials_valid(const std::string& ssid, const std::string& password)
{
    if (ssid.empty() || ssid.size() > 32 || ssid.find('\0') != std::string::npos ||
        password.find('\0') != std::string::npos) return false;
    if (password.empty()) return true;
    if (password.size() >= 8 && password.size() <= 63) return true;
    return password.size() == 64 && password.find_first_not_of("0123456789abcdefABCDEF") == std::string::npos;
}

// Call in priority order. The first saved credential for an SSID wins, even
// when the factory list contains an older password for that same network.
template<class Profile>
bool append_wifi_profile(std::vector<Profile>& profiles, const Profile& next)
{
    if (!wifi_credentials_valid(next.ssid, next.password) ||
        std::any_of(profiles.begin(), profiles.end(), [&](const auto& p) { return p.ssid == next.ssid; })) return false;
    profiles.push_back(next);
    return true;
}
}
