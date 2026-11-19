#include "copus.h"
int copus_set_bitrate(OpusEncoder *e, opus_int32 bitrate) { return opus_encoder_ctl(e, OPUS_SET_BITRATE(bitrate)); }
int copus_set_vbr(OpusEncoder *e, opus_int32 on) { return opus_encoder_ctl(e, OPUS_SET_VBR(on)); }
int copus_set_complexity(OpusEncoder *e, opus_int32 c) { return opus_encoder_ctl(e, OPUS_SET_COMPLEXITY(c)); }
