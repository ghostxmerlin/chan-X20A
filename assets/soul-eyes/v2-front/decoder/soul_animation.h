#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// No Arduino, allocation, filesystem, packed structs or native-endian dependency.
namespace soul {
constexpr uint16_t kWidth=320, kHeight=240, kMaxFrames=64;
constexpr size_t kPixels=size_t(kWidth)*kHeight;
enum class Error { Ok, Read, Header, Dimensions, FrameCount, FileSize, Table,
                   Duration, FrameIndex, OutputSize, RleTruncated, RleOverflow,
                   RleSize, Crc };
inline const char* errorName(Error error) {
  switch (error) {
    case Error::Ok:return "ok"; case Error::Read:return "SD read failed";
    case Error::Header:return "invalid SOULAN1 header";
    case Error::Dimensions:return "expected 320x240";
    case Error::FrameCount:return "invalid frame count";
    case Error::FileSize:return "file size mismatch";
    case Error::Table:return "invalid frame table";
    case Error::Duration:return "invalid frame duration";
    case Error::FrameIndex:return "invalid frame index";
    case Error::OutputSize:return "invalid output/scratch buffer";
    case Error::RleTruncated:return "truncated RLE packet";
    case Error::RleOverflow:return "RLE output overflow";
    case Error::RleSize:return "wrong RLE pixel count";
    case Error::Crc:return "frame CRC mismatch";
  }
  return "unknown";
}
class Reader {
 public:
  virtual ~Reader()=default;
  virtual size_t size() const=0;
  virtual bool readAt(uint32_t offset,uint8_t* out,size_t length)=0;
};
inline uint16_t read16(const uint8_t* b) { return uint16_t(b[0])|(uint16_t(b[1])<<8); }
inline uint32_t read32(const uint8_t* b) {
  return uint32_t(b[0])|(uint32_t(b[1])<<8)|(uint32_t(b[2])<<16)|(uint32_t(b[3])<<24);
}
inline uint32_t crc32Byte(uint32_t crc,uint8_t byte) {
  crc ^= byte;
  for (unsigned b=0;b<8;++b) crc=(crc>>1)^(0xedb88320u & (0u-(crc&1u)));
  return crc;
}
inline uint32_t crc32Pixels(const uint16_t* pixels,size_t count) {
  uint32_t crc=0xffffffffu;
  for (size_t i=0;i<count;++i) {
    crc=crc32Byte(crc,uint8_t(pixels[i]));
    crc=crc32Byte(crc,uint8_t(pixels[i]>>8));
  }
  return ~crc;
}
struct Frame { uint32_t durationMs=0, offset=0, length=0, crc32=0; };
class Animation {
 public:
  Error open(Reader& reader) {
    count_=0;
    uint8_t h[32];
    if (reader.size()<sizeof h || !reader.readAt(0,h,sizeof h)) return Error::Read;
    if (memcmp(h,"SOULAN1\0",8) || read16(h+8)!=1 || read32(h+16) ||
        read32(h+20)!=32 || read32(h+28)) return Error::Header;
    if (read16(h+10)!=kWidth || read16(h+12)!=kHeight) return Error::Dimensions;
    const uint16_t count=read16(h+14);
    if (!count || count>kMaxFrames) return Error::FrameCount;
    const uint32_t fileSize=read32(h+24);
    if (reader.size()!=fileSize) return Error::FileSize;
    uint32_t next=32u+16u*count;
    if (next>fileSize) return Error::Table;
    for (uint16_t i=0;i<count;++i) {
      uint8_t b[16];
      if (!reader.readAt(32u+16u*i,b,sizeof b)) return Error::Read;
      Frame& f=frames_[i];
      f.durationMs=read32(b); f.offset=read32(b+4); f.length=read32(b+8); f.crc32=read32(b+12);
      if (!f.durationMs || f.durationMs>60000) return Error::Duration;
      // This v1 encoder writes contiguous packets; reject gaps, overlaps and wraparound.
      if (f.offset!=next || f.offset>fileSize || f.length<4 || (f.length&1) ||
          f.length>kPixels*4 || f.length>fileSize-f.offset) return Error::Table;
      next+=f.length;
    }
    if (next!=fileSize) return Error::Table;
    count_=count; return Error::Ok;
  }
  uint16_t frameCount() const { return count_; }
  const Frame& frame(uint16_t index) const { return frames_[index]; } // index < frameCount()
  Error decode(Reader& reader,uint16_t index,uint16_t* pixels,size_t pixelCapacity,
               uint8_t* scratch,size_t scratchSize) const {
    if (index>=count_) return Error::FrameIndex;
    if (!pixels || pixelCapacity<kPixels || !scratch || !scratchSize) return Error::OutputSize;
    const Frame& f=frames_[index];
    Cursor in(reader,f.offset,f.length,scratch,scratchSize);
    size_t written=0;
    while (in.remaining()) {
      uint16_t control;
      if (!in.word(control)) return in.ioFailed()?Error::Read:Error::RleTruncated;
      const size_t count=(control&0x7fff)+1u;
      if (count>kPixels-written) return Error::RleOverflow;
      if (control&0x8000) {
        uint16_t value;
        if (!in.word(value)) return in.ioFailed()?Error::Read:Error::RleTruncated;
        for (size_t j=0;j<count;++j) pixels[written++]=value;
      } else {
        for (size_t j=0;j<count;++j) {
          uint16_t value;
          if (!in.word(value)) return in.ioFailed()?Error::Read:Error::RleTruncated;
          pixels[written++]=value;
        }
      }
    }
    if (written!=kPixels) return Error::RleSize;
    return crc32Pixels(pixels,kPixels)==f.crc32 ? Error::Ok : Error::Crc;
  }
 private:
  class Cursor {
   public:
    Cursor(Reader& r,uint32_t offset,uint32_t length,uint8_t* buffer,size_t capacity)
      : reader_(r),offset_(offset),remaining_(length),buffer_(buffer),capacity_(capacity) {}
    uint32_t remaining() const { return remaining_; }
    bool ioFailed() const { return ioFailed_; }
    bool word(uint16_t& word) {
      uint8_t lo,hi;
      if (!byte(lo)||!byte(hi)) return false;
      word=uint16_t(lo)|(uint16_t(hi)<<8); return true;
    }
   private:
    bool byte(uint8_t& value) {
      if (!remaining_) return false;
      if (pos_==loaded_) {
        loaded_=remaining_<capacity_?remaining_:capacity_; pos_=0;
        if (!reader_.readAt(offset_,buffer_,loaded_)) { ioFailed_=true; return false; }
        offset_+=static_cast<uint32_t>(loaded_);
      }
      value=buffer_[pos_++]; --remaining_; return true;
    }
    Reader& reader_; uint32_t offset_,remaining_; uint8_t* buffer_; size_t capacity_;
    size_t pos_=0,loaded_=0; bool ioFailed_=false;
  };
  uint16_t count_=0;
  Frame frames_[kMaxFrames];
};
} // namespace soul
