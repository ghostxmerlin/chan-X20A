#pragma once
#include "control_reply.h"
#include <string>
namespace collector {
struct MonitorHooks {
    void (*info)(ArduinoJson::JsonDocument&);
    void (*state)(ArduinoJson::JsonDocument&,bool servo_diagnostics);
    void (*events)(ArduinoJson::JsonDocument&,uint32_t,uint32_t);
    void (*control)(ArduinoJson::JsonObject,ControlReply&);
    bool (*proof)(const std::string&,ArduinoJson::JsonDocument&);
};
void serial_monitor_start(const std::string& device,const std::string& boot,const std::string& token,MonitorHooks hooks);
// Preview demand expires with the authenticated USB lease; never changes NVS.
bool serial_monitor_camera_requested();
void serial_monitor_stats(ArduinoJson::JsonObject out);
}
