#pragma once
#include "control_reply.h"
#include <string>
namespace collector {
struct MonitorHooks {
    void (*info)(ArduinoJson::JsonDocument&);
    void (*state)(ArduinoJson::JsonDocument&);
    void (*events)(ArduinoJson::JsonDocument&,uint32_t,uint32_t);
    void (*control)(ArduinoJson::JsonObject,ControlReply&);
    bool (*proof)(const std::string&,ArduinoJson::JsonDocument&);
};
void serial_monitor_start(const std::string& device,const std::string& boot,const std::string& token,MonitorHooks hooks);
void serial_monitor_stats(ArduinoJson::JsonObject out);
}
