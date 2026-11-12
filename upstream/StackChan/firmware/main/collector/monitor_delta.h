#pragma once
#include <ArduinoJson.hpp>
namespace collector::monitor {
// Null denotes either an unavailable value or a removed optional field.
inline void difference(ArduinoJson::JsonObjectConst before,ArduinoJson::JsonObjectConst after,ArduinoJson::JsonObject patch){
    using namespace ArduinoJson;
    for(JsonPairConst pair:after){
        const auto key=pair.key();const JsonVariantConst old=before[key],value=pair.value();
        if(!old.isUnbound()&&old==value)continue;
        if(old.is<JsonObjectConst>()&&value.is<JsonObjectConst>())difference(old.as<JsonObjectConst>(),value.as<JsonObjectConst>(),patch[key].to<JsonObject>());
        else patch[key].set(value);
    }
    for(JsonPairConst pair:before)if(after[pair.key()].isUnbound())patch[pair.key()]=nullptr;
}
}
