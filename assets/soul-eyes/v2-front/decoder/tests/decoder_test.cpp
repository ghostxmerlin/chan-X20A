#include "soul_animation.h"
#include <cassert>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>

struct MemoryReader : soul::Reader {
  std::vector<uint8_t> data;
  uint32_t failAt=0xffffffffu;
  size_t size() const override { return data.size(); }
  bool readAt(uint32_t offset, uint8_t* out, size_t length) override {
    if (offset>=failAt || offset > data.size() || length > data.size() - offset) return false;
    std::memcpy(out, data.data() + offset, length); return true;
  }
};
static void put16(std::vector<uint8_t>& b, size_t n, uint16_t v) { b[n]=v; b[n+1]=v>>8; }
static void put32(std::vector<uint8_t>& b, size_t n, uint32_t v) {
  for (int i=0;i<4;++i) b[n+i]=v>>(i*8);
}
static MemoryReader fixture() {
  MemoryReader r; r.data.resize(60, 0);
  std::memcpy(r.data.data(), "SOULAN1\0", 8);
  put16(r.data,8,1); put16(r.data,10,320); put16(r.data,12,240); put16(r.data,14,1);
  put32(r.data,20,32); put32(r.data,24,60);
  put32(r.data,32,100); put32(r.data,36,48); put32(r.data,40,12);
  // 32768 red, 32768 green, 11264 blue: full frame, no native endian assumptions.
  put16(r.data,48,0xffff); put16(r.data,50,0xf800);
  put16(r.data,52,0xffff); put16(r.data,54,0x07e0);
  put16(r.data,56,0xabff); put16(r.data,58,0x001f);
  std::vector<uint16_t> p(76800,0x001f);
  std::fill(p.begin(),p.begin()+32768,0xf800);
  std::fill(p.begin()+32768,p.begin()+65536,0x07e0);
  put32(r.data,44,soul::crc32Pixels(p.data(),p.size())); return r;
}
int main(int argc, char** argv) {
  std::vector<uint16_t> pixels(76800+2,0xdead);
  uint8_t scratch[7]; // Deliberately odd, forces packet and word boundaries across refills.
  auto r=fixture(); soul::Animation a;
  assert(a.open(r)==soul::Error::Ok);
  assert(a.decode(r,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::Ok);
  assert(pixels[0]==0xdead && pixels.back()==0xdead);
  assert(pixels[1]==0xf800 && pixels[32769]==0x07e0 && pixels[65537]==0x001f);
  assert(a.decode(r,1,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::FrameIndex);
  assert(a.decode(r,0,pixels.data()+1,76799,scratch,sizeof scratch)==soul::Error::OutputSize);
  auto bad=r; bad.data.pop_back(); assert(a.open(bad)!=soul::Error::Ok);
  bad=r; put16(bad.data,10,65535); assert(a.open(bad)==soul::Error::Dimensions);
  bad=r; put16(bad.data,14,65); assert(a.open(bad)!=soul::Error::Ok);
  bad=r; put32(bad.data,36,0xffffffff); assert(a.open(bad)!=soul::Error::Ok);
  bad=r; put32(bad.data,32,0); assert(a.open(bad)==soul::Error::Duration);
  bad=r; put32(bad.data,16,1); assert(a.open(bad)!=soul::Error::Ok);
  bad=r; put16(bad.data,56,0xffff); assert(a.open(bad)==soul::Error::Ok);
  assert(a.decode(bad,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::RleOverflow);
  bad=r; put16(bad.data,56,0x7fff); assert(a.open(bad)==soul::Error::Ok);
  assert(a.decode(bad,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::RleOverflow);
  bad=r; bad.data[50]^=1; assert(a.open(bad)==soul::Error::Ok);
  assert(a.decode(bad,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::Crc);
  bad=r; put16(bad.data,56,0x8000); assert(a.open(bad)==soul::Error::Ok);
  assert(a.decode(bad,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::RleSize);
  bad=r; put16(bad.data,56,0); assert(a.open(bad)==soul::Error::Ok);
  assert(a.decode(bad,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::RleSize);
  bad=r; put16(bad.data,56,2); assert(a.open(bad)==soul::Error::Ok);
  assert(a.decode(bad,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::RleTruncated);
  bad=r; bad.failAt=48; assert(a.open(bad)==soul::Error::Ok);
  assert(a.decode(bad,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::Read);
  bad=r; bad.data.resize(62,0); put32(bad.data,24,62); put32(bad.data,40,14);
  assert(a.open(bad)==soul::Error::Ok);
  assert(a.decode(bad,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::RleOverflow);
  // A literal RGB565 packet, followed by a repeat completing the last block.
  r=fixture(); r.data.resize(66); put32(r.data,24,66); put32(r.data,40,18);
  put16(r.data,56,1); put16(r.data,58,0x001f); put16(r.data,60,0x001f);
  put16(r.data,62,0xabfd); put16(r.data,64,0x001f);
  assert(a.open(r)==soul::Error::Ok);
  assert(a.decode(r,0,pixels.data()+1,76800,scratch,sizeof scratch)==soul::Error::Ok);
  size_t files=0, frames=0;
  for (int i=1;i<argc;++i) {
    std::ifstream f(argv[i],std::ios::binary); assert(f.good());
    MemoryReader asset; asset.data.assign(std::istreambuf_iterator<char>(f),{});
    auto err=a.open(asset);
    if (err!=soul::Error::Ok) { std::cerr<<argv[i]<<": "<<soul::errorName(err)<<"\n"; return 1; }
    for (uint16_t j=0;j<a.frameCount();++j) {
      err=a.decode(asset,j,pixels.data()+1,76800,scratch,sizeof scratch);
      if (err!=soul::Error::Ok) { std::cerr<<argv[i]<<" #"<<j<<": "<<soul::errorName(err)<<"\n"; return 1; }
      assert(pixels[0]==0xdead && pixels.back()==0xdead); ++frames;
    }
    ++files;
  }
  std::cout<<"Decoder corruption/bounds/color tests passed; real assets: "<<files<<" files, "<<frames<<" frames\n";
}
