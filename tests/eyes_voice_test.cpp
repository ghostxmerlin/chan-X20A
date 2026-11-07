#include "../upstream/StackChan/firmware/main/collector/eyes/voice_cue.h"
#include "../upstream/StackChan/firmware/main/collector/playback_session.h"
#include "../upstream/StackChan/firmware/main/collector/voice_policy.h"
#include <cassert>
struct Output {
    collector::eyes::VoiceCue cue;int begins=0,writes=0,cues=0,fail_begin=0,fail_write=0;int64_t clock=0;
    int64_t now(){return clock;}void pause(){clock+=10000;}void drain(){}void state(const char*){}
    int begin(){++begins;return fail_begin;}int finish(){return 0;}
    int write(const int16_t*,size_t){return ++writes==fail_write?1:0;}
    void submitted(size_t bytes){if(cue.submitted(bytes))++cues;}
};
int main(){
    int16_t pcm[1000]{};collector::PlaybackUpload upload(sizeof(pcm));assert(upload.append(sizeof(pcm)));
    Output asr{{true}};collector::wait_voice_start(800000,asr);assert(asr.begins==0 && asr.cues==0 && asr.clock>=800000);
    assert(!collector::run_pcm_playback(pcm,upload,asr).error);assert(asr.cues==1);
    Output web{{false}};collector::run_pcm_playback(pcm,upload,web);assert(!web.cues);
    Output start_failed{{true}};start_failed.fail_begin=1;collector::run_pcm_playback(pcm,upload,start_failed);assert(!start_failed.cues);
    Output first_failed{{true}};first_failed.fail_write=1;collector::run_pcm_playback(pcm,upload,first_failed);assert(first_failed.writes>1 && !first_failed.cues); // Drain silence must not trigger.
    Output later_failed{{true}};later_failed.fail_write=2;collector::run_pcm_playback(pcm,upload,later_failed);assert(later_failed.cues==1);
}
