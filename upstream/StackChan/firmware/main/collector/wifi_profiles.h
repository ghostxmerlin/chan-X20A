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

// Operator-managed network list stored as one collector NVS value. Index 0 is
// tried first; the factory list stays an untouched fallback behind it.
constexpr size_t kWifiProfileLimit = 6;
struct WifiProfile { std::string ssid, password; };
class WifiProfileList {
public:
    const std::vector<WifiProfile>& items() const { return items_; }
    int index_of(const std::string& ssid) const
    {
        for (size_t i = 0; i < items_.size(); ++i) if (items_[i].ssid == ssid) return static_cast<int>(i);
        return -1;
    }
    // Replace a same-named entry's password; a new entry needs a free slot.
    // preferred moves the entry to the front.
    bool add(const std::string& ssid, const std::string& password, bool preferred)
    {
        if (!wifi_credentials_valid(ssid, password)) return false;
        const int existing = index_of(ssid);
        if (existing < 0 && items_.size() >= kWifiProfileLimit) return false;
        if (existing >= 0) items_.erase(items_.begin() + existing);
        const WifiProfile profile{ssid, password};
        if (preferred || items_.empty()) items_.insert(items_.begin(), profile);
        else items_.push_back(profile);
        return true;
    }
    bool remove(const std::string& ssid)
    {
        const int index = index_of(ssid);
        if (index < 0) return false;
        items_.erase(items_.begin() + index);
        return true;
    }
    bool prefer(const std::string& ssid)
    {
        const int index = index_of(ssid);
        if (index < 0) return false;
        const WifiProfile profile = items_[index];
        items_.erase(items_.begin() + index);
        items_.insert(items_.begin(), profile);
        return true;
    }
    void clear() { items_.clear(); }
private:
    std::vector<WifiProfile> items_;
};
}
