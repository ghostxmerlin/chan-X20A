#include "../upstream/StackChan/firmware/main/collector/face_pixels.h"
#include <array>
#include <cassert>
#include <cstdio>
int main()
{
    using namespace collector;
    std::array<uint8_t,kFaceWidth*kFaceHeight*3> output{};
    const uint8_t yuyv[] = {16,128,235,128};
    assert(face_pixels(yuyv,sizeof(yuyv),2,1,FacePixelFormat::Yuyv,output.data()));
    assert(output[0]==0&&output[1]==0&&output[2]==0);
    assert(output[80*3]==255&&output[80*3+1]==255&&output[80*3+2]==255);
    const uint8_t rgb565[] = {0,0xf8,0xe0,7};
    assert(face_pixels(rgb565,4,2,1,FacePixelFormat::Rgb565,output.data()));
    assert(output[0]==255&&output[1]==0&&output[2]==0);
    assert(output[80*3]==0&&output[80*3+1]==255&&output[80*3+2]==0);
    const uint8_t rgb[] = {255,0,0,0,255,0,0,0,255,255,255,255};
    assert(face_pixels(rgb,12,2,2,FacePixelFormat::Rgb888,output.data()));
    assert(output[0]==255&&output[(60*160)*3+2]==255);
    assert(output.back()==255);
    assert(!face_pixels(rgb,11,2,2,FacePixelFormat::Rgb888,output.data()));
    assert(!face_pixels(rgb,12,3,2,FacePixelFormat::Yuyv,output.data()));
    assert(!face_pixels(rgb,12,-1,2,FacePixelFormat::Rgb888,output.data()));
    assert(!face_pixels(rgb,12,2,2,FacePixelFormat::Unsupported,output.data()));
    assert(!face_pixels(nullptr,12,2,2,FacePixelFormat::Rgb888,output.data()));
    puts("PASS: full-field sampling, YUYV levels, RGB565 byte order, RGB channels and bounds");
}
