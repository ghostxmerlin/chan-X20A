// libopus from Homebrew (brew install opus), linked statically (Package.swift). opus_encoder_ctl is variadic, which
// Swift cannot call: these wrappers set the encoder options the robot uses.
#pragma once
#include "/opt/homebrew/include/opus/opus.h"
int copus_set_bitrate(OpusEncoder *e, opus_int32 bitrate);
int copus_set_vbr(OpusEncoder *e, opus_int32 on);
int copus_set_complexity(OpusEncoder *e, opus_int32 c);
