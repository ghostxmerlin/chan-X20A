/* SPDX-License-Identifier: MIT */
#pragma once
#include <cmath>
#include <string>

namespace collector {
inline bool discovery_nonce_valid(const std::string& value)
{
    return value.size() == 32 && value.find_first_not_of("0123456789abcdef") == std::string::npos;
}

inline std::string discovery_proof_message(const std::string& device, const std::string& nonce)
{
    return "chan-discovery-v1\n" + device + "\n" + nonce;
}

// Reject unrepresentable values instead of silently changing the requested angle.
inline bool pitch_request_tenths(double degrees, int& tenths)
{
    if (!std::isfinite(degrees) || degrees < 5 || degrees > 85) return false;
    const double scaled = degrees * 10;
    const double rounded = std::round(scaled);
    if (std::abs(scaled - rounded) > 0.000001) return false;
    tenths = static_cast<int>(rounded);
    return true;
}
} // namespace collector
