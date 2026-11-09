#include <collector/screen.h>
#include <collector/eyes/eyes.h>
#include <collector/eyes/loader.h>
#include <openssl/sha.h>
#include <cassert>
#include <fstream>
#include <string>
#include <vector>
#include <cstdlib>
std::vector<uint8_t> display_pixels(320*240*3);
namespace hal_bridge {void board_set_display_backlight_enabled(bool) {}}
using namespace collector::eyes;
struct Host:Backend {
    std::string root;int64_t time=1000;
    int64_t now_us() const override{return time;}
    bool available() const override{return true;}
    void* allocate(size_t n) override{return std::malloc(n);}
    void release(void* p) override{std::free(p);}
    Error read(const Asset& a,uint8_t* out,size_t) override {
        std::ifstream f(root+"/"+style_id(a.style)+"/"+state_id(a.state)+".soul",std::ios::binary);
        return f.read(reinterpret_cast<char*>(out),a.bytes)?Error::None:Error::AssetMissing;
    }
    bool sha256(const uint8_t* b,size_t n,uint8_t* out) override{return SHA256(b,n,out)!=nullptr;}
};
Command command(unsigned id,Operation op) {
    Command c;c.op=op;snprintf(c.request_id,37,"12345678-1234-4234-8234-%012x",id);return c;
}
void capture(const std::string& path) {
    lv_refr_now(lv_display_get_default());
    std::ofstream out(path,std::ios::binary);out<<"P6\n320 240\n255\n";
    out.write(reinterpret_cast<const char*>(display_pixels.data()),display_pixels.size());
    const auto* green=&display_pixels[(238*320+224)*3];
    // Capture includes the top layer, so a full-screen eye frame cannot erase it.
    const bool visible=snapshot().visible;
    if(visible)assert(green[0]==0x36 && green[1]==0xe8 && green[2]==0x74);
}
int main(int argc,char** argv) {
    assert(argc==3);Host host;host.root=argv[1];assert(initialize(host));
    // Decode every shipped frame with its CRC, independent of UI timing.
    std::vector<uint8_t> bytes(kAssetCapacity),scratch(4096);std::vector<uint16_t> pixels(kPixels);
    Cache cache;cache.bytes=bytes.data();
    for(const auto& a:kAssets) {assert(cache.load(host,a)==Error::None);for(int f=0;f<8;++f)assert(cache.decode(f,pixels.data(),scratch.data())==Error::None);}
    lv_init();auto* d=lv_display_create(320,240);std::vector<uint8_t> draw(320*20*3);
    lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB888);
    lv_display_set_buffers(d,draw.data(),nullptr,draw.size(),LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(d,[](lv_display_t* display,const lv_area_t* area,uint8_t* pixels){
        const auto stride=lv_draw_buf_width_to_stride(lv_area_get_width(area),LV_COLOR_FORMAT_RGB888);
        for(int y=area->y1;y<=area->y2;++y)for(int x=area->x1;x<=area->x2;++x) {
            auto* p=pixels+(y-area->y1)*stride+(x-area->x1)*3;
            auto* out=&display_pixels[(y*320+x)*3];out[0]=p[2];out[1]=p[1];out[2]=p[0];
        }
        lv_display_flush_ready(display);
    });
    collector::screen_create(lv_screen_active());collector::screen_tick(1);
    unsigned id=1;
    auto refresh=[&] {host.time+=10000;collector::screen_tick(host.time/1000);lv_refr_now(d);};
    for(size_t i=0;i<kStyleCount;++i) {
        auto c=command(id++,Operation::Configure);c.enabled=true;c.style=Style(i);
        assert(submit(c,Source::Manual).accepted);step();refresh();assert(snapshot().visible);
        capture(std::string(argv[2])+"/eyes-"+style_id(c.style)+"-idle.ppm");
        c=command(id++,Operation::Play);c.state=State::Happy;
        assert(submit(c,Source::Manual).accepted);step();refresh();assert(snapshot().phase==Phase::Playing);
        capture(std::string(argv[2])+"/eyes-"+style_id(Style(i))+"-happy.ppm");
        // A heart clears the entire 320px eye image, including the margins.
        collector::screen_show_reaction(collector::ScreenReaction::Heart,host.time/1000);
        assert(!show_bound_frame() && query(c.request_id).status==RequestStatus::Cancelled);lv_refr_now(d);
        capture(std::string(argv[2])+"/eyes-"+style_id(Style(i))+"-heart.ppm");
        host.time+=1100000;refresh();step();refresh();assert(snapshot().state==State::Idle);
    }
    voice_started(17,1,5000);step();refresh();assert(snapshot().source==Source::Voice);
    collector::screen_set_enabled(false,host.time/1000);assert(!show_bound_frame() && snapshot().state==State::Idle);
    host.time+=3000000;step();assert(!take_frame().pixels);
    collector::screen_set_enabled(true,host.time/1000);lv_refr_now(d);step();refresh();assert(snapshot().visible && snapshot().state==State::Idle);
    auto c=command(id++,Operation::Configure);assert(submit(c,Source::Manual).accepted);refresh();assert(!snapshot().visible);
    capture(std::string(argv[2])+"/eyes-disabled.ppm");
    printf("720 asset frames decoded; 3 styles rendered; heart/off/disable cancellation passed\n");
}
