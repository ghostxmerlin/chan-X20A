#pragma once
#include <ArduinoJson.hpp>
#include <esp_http_server.h>
#include <cstdlib>
#include <cstring>
#include <string>
namespace collector {
// Shared business response. Transport wrappers own authentication and boot checks.
struct ControlReply {
    std::string status="200 OK",type="text/plain",body;
    int code()const{return std::atoi(status.c_str());}
    esp_err_t http(httpd_req_t* r)const {
        ::httpd_resp_set_status(r,status.c_str());::httpd_resp_set_type(r,type.c_str());
        ::httpd_resp_set_hdr(r,"Cache-Control","no-store");return ::httpd_resp_send(r,body.data(),body.size());
    }
};
using ::httpd_resp_set_status;using ::httpd_resp_set_type;using ::httpd_resp_set_hdr;
using ::httpd_resp_send;using ::httpd_resp_sendstr;using ::httpd_resp_send_err;
inline esp_err_t httpd_resp_set_status(ControlReply* r,const char* s){r->status=s;return ESP_OK;}
inline esp_err_t httpd_resp_set_type(ControlReply* r,const char* s){r->type=s;return ESP_OK;}
inline esp_err_t httpd_resp_set_hdr(ControlReply*,const char*,const char*){return ESP_OK;}
inline esp_err_t httpd_resp_send(ControlReply* r,const char* s,ssize_t n){r->body.assign(s,n<0?std::strlen(s):size_t(n));return ESP_OK;}
inline esp_err_t httpd_resp_sendstr(ControlReply* r,const char* s){return httpd_resp_send(r,s,-1);}
inline esp_err_t httpd_resp_send_err(ControlReply* r,httpd_err_code_t e,const char* s){
    const char* status=e==HTTPD_400_BAD_REQUEST?"400 Bad Request":e==HTTPD_404_NOT_FOUND?"404 Not Found":e==HTTPD_401_UNAUTHORIZED?"401 Unauthorized":"500 Internal Server Error";
    r->status=status;return httpd_resp_sendstr(r,s);
}
inline esp_err_t send_json(ControlReply* r,const ArduinoJson::JsonDocument& d){r->type="application/json";serializeJson(d,r->body);return ESP_OK;}
inline esp_err_t unavailable(ControlReply* r,const char* s){r->status="503 Service Unavailable";return httpd_resp_sendstr(r,s);}
}
