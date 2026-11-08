#include "../upstream/StackChan/firmware/main/collector/eyes/protocol.h"
#include <cassert>
#include <string>
using namespace collector::eyes;
int main() {
    // The document outlives the snapshot/query result used by HTTP handlers.
    // Reuse its backing buffer before serialization to expose borrowed storage.
    for (const char* expected : {"", "12345678-1234-4234-8234-123456789abc"}) {
        JsonDocument doc;
        {
            RequestResult result;
            std::strcpy(result.request_id,expected);
            request_json(doc.to<JsonObject>(),result);
            std::memset(result.request_id,'x',36);result.request_id[36]='\0';
        }
        std::string wire;serializeJson(doc,wire);
        JsonDocument parsed;assert(!deserializeJson(parsed,wire));
        assert(parsed["request_id"].as<std::string>()==expected);
    }
    const std::string id="\"request_id\":\"12345678-1234-4234-8234-123456789abc\"";
    Command c;
    auto parse=[&](std::string s){return parse_command(s.data(),s.size(),c);};
    assert(parse("{\"op\":\"configure\","+id+",\"enabled\":true,\"style\":\"v14\"}"));
    assert(c.op==Operation::Configure && c.enabled && c.style==Style::V14);
    assert(parse("{\"op\":\"play\","+id+",\"state\":\"happy\",\"mode\":\"once\"}"));
    assert(!parse("{\"op\":\"play\","+id+",\"state\":\"happy\",\"mode\":\"once\",\"loop_for_ms\":1000}"));
    assert(parse("{\"op\":\"play\","+id+",\"state\":\"happy\",\"mode\":\"loop\",\"loop_for_ms\":30000}"));
    assert(!parse("{\"op\":\"play\","+id+",\"state\":\"happy\",\"mode\":\"loop\",\"loop_for_ms\":999}"));
    assert(!parse("{\"op\":\"play\","+id+",\"state\":\"happy\",\"mode\":\"loop\",\"loop_for_ms\":1000.5}"));
    assert(!parse("{\"op\":\"configure\","+id+",\"enabled\":1,\"style\":\"v05\"}"));
    assert(!parse("{\"op\":\"configure\","+id+",\"enabled\":true,\"style\":\"../v05\"}"));
    assert(parse("{\"op\":\"stop\","+id+"} \n"));
    assert(!parse("{\"op\":\"stop\","+id+"}{}"));
    assert(!parse("{\"op\":\"stop\","+id+",\"state\":\"happy\"}"));
    assert(!parse("{\"op\":\"stop\",\"op\":\"stop\","+id+"}"));
    assert(!parse("{\"op\":\"stop\",\"request_id\":\"invalid\"}"));
    assert(!parse(std::string(385,' ')));
    assert(!parse("{'op':'stop','request_id':'12345678-1234-4234-8234-123456789abc'}"));
    assert(!parse("{op:\"stop\","+id+"}"));
    assert(!parse("{\"op\":\"stop\","+id+",}"));
}
