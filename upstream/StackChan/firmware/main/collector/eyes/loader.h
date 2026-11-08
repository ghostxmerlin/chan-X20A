/* SPDX-License-Identifier: MIT */
#pragma once
#include "types.h"
#include "soul_animation.h"
namespace collector::eyes {
class Backend {
public:
    virtual ~Backend()=default;
    virtual int64_t now_us() const=0;
    virtual bool available() const=0;
    virtual void* allocate(size_t bytes)=0;
    virtual void release(void* p)=0;
    virtual Error read(const Asset&,uint8_t* destination,size_t capacity)=0;
    virtual bool sha256(const uint8_t* bytes,size_t length,uint8_t digest[32])=0;
    virtual uint32_t internal_low_water() const { return 0; }
    virtual uint32_t random_seed() const { return uint32_t(now_us()); }
};
class MemoryReader : public soul::Reader {
public:
    const uint8_t* bytes=nullptr;size_t length=0;
    size_t size() const override { return length; }
    bool readAt(uint32_t offset,uint8_t* destination,size_t count) override {
        if (!bytes || offset>length || count>length-offset) return false;
        std::memcpy(destination,bytes+offset,count);return true;
    }
};
class Cache {
public:
    uint8_t* bytes=nullptr;
    const Asset* metadata=nullptr;
    MemoryReader reader;
    soul::Animation animation;
    Error load(Backend& backend,const Asset& a) {
        if (metadata==&a) return Error::None;
        metadata=nullptr;reader.length=0;
        const auto error=backend.read(a,bytes,kAssetCapacity);if (error!=Error::None) return error;
        uint8_t digest[32];
        if (!backend.sha256(bytes,a.bytes,digest) || std::memcmp(digest,a.sha256,32)) return Error::AssetInvalid;
        reader.bytes=bytes;reader.length=a.bytes;
        if (animation.open(reader)!=soul::Error::Ok || animation.frameCount()!=8) return Error::AssetInvalid;
        for (size_t i=0;i<8;++i) if (animation.frame(i).durationMs!=a.durations[i]) return Error::AssetInvalid;
        metadata=&a;return Error::None;
    }
    Error decode(uint16_t frame,uint16_t* output,uint8_t* scratch) {
        if (!metadata) return Error::AssetInvalid;
        return animation.decode(reader,frame,output,kPixels,scratch,4096)==soul::Error::Ok?Error::None:Error::AssetInvalid;
    }
};
}
