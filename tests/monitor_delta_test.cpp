#include "monitor_delta.h"
#include <cassert>
#include <string>
using namespace ArduinoJson;
int main(){
 JsonDocument a,b,p;deserializeJson(a,R"({"seq":1,"power":{"charging":true,"level":80},"faces":[1,2],"optional":1})");
 deserializeJson(b,R"({"seq":2,"power":{"charging":true,"level":79},"faces":[3],"nullable":null})");
 collector::monitor::difference(a.as<JsonObjectConst>(),b.as<JsonObjectConst>(),p.to<JsonObject>());
 std::string out;serializeJson(p,out);
 assert(out==R"({"seq":2,"power":{"level":79},"faces":[3],"nullable":null,"optional":null})");
 p.clear();collector::monitor::difference(b.as<JsonObjectConst>(),b.as<JsonObjectConst>(),p.to<JsonObject>());
 assert(p.size()==0);
}
