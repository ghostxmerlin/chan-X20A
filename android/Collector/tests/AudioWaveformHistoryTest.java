package dev.stackchan.collector;

import java.io.ByteArrayInputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.IntBinaryOperator;

/** Exercises actual SCA1 decoding and bounded real-PCM envelopes without Android. */
public final class AudioWaveformHistoryTest {
    private static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    private static void near(double actual,double expected,double tolerance,String message){check(Math.abs(actual-expected)<=tolerance,message+": "+actual);}
    private static AudioBlock block(long seq,long timestamp,int frames,int rate,int channels,IntBinaryOperator sample)throws Exception{
        ByteBuffer bytes=ByteBuffer.allocate(32+frames*channels*2).order(ByteOrder.LITTLE_ENDIAN);
        bytes.put(new byte[]{'S','C','A','1'}).putInt((int)seq).putLong(timestamp).putInt(rate).putInt(frames)
             .putShort((short)channels).putShort((short)16).putInt(frames*channels*2);
        for(int frame=0;frame<frames;frame++)for(int channel=0;channel<channels;channel++)bytes.putShort((short)sample.applyAsInt(frame,channel));
        return AudioBlock.read(new ByteArrayInputStream(bytes.array()));
    }
    private static AudioBlock constant(long seq,long timestamp,int value)throws Exception{
        return block(seq,timestamp,480,24000,2,(frame,channel)->channel==0?value:32767);
    }
    private static void bounded(AudioWaveformHistory.Snapshot s){
        check(s.size()<=512,"bounded bucket count");
        for(int i=0;i<s.size();i++){
            check(s.startsUs[i]<s.endsUs[i],"positive measured bin duration");
            check(s.minimum[i]>=-1&&s.maximum[i]<=1&&s.minimum[i]<=s.maximum[i],"finite ordered full-scale envelope");
            check(s.endsUs[i]>s.endUs-AudioWaveformHistory.WINDOW_US,"no retained expired buckets");
        }
    }
    public static void main(String[] args)throws Exception{
        AudioWaveformHistory h=new AudioWaveformHistory();
        check(h.snapshot().size()==0&&h.snapshot().stale(0),"empty is stale");
        check(!h.levelSnapshot().hasSample&&h.levelSnapshot().stale(0)&&Double.isNaN(h.levelSnapshot().rmsDb),"empty level is unavailable");
        AudioBlock extrema=block(1,1000000,480,24000,2,(frame,channel)->channel==0?(frame%2==0?-32768:32767):0);
        check(h.add("boot-a",extrema,1000),"first sample accepted");
        AudioWaveformHistory.Snapshot s=h.snapshot();
        check(s.size()==4,"20ms input yields four 5ms bins");
        for(int i=0;i<s.size();i++){near(s.minimum[i],-1,0,"signed LE minimum");near(s.maximum[i],32767/32768.0,0,"signed LE maximum");}
        check(s.peak==1&&!s.stale(1999)&&s.stale(2000)&&!s.stale(999),"full-scale and freshness boundaries; negative age is not evidence of interruption");
        AudioWaveformHistory.LevelSnapshot originalLevel=h.levelSnapshot();
        check(originalLevel.hasSample&&originalLevel.blockDurationUs==20000&&originalLevel.lastSequence==1&&originalLevel.acceptedBlocks==1,
              "lightweight snapshot describes the actual latest 20 ms block");
        near(originalLevel.rmsDb,s.rmsDb,0,"lightweight level uses the same exact first-channel RMS");
        check(!originalLevel.stale(1999)&&originalLevel.stale(2000)&&!originalLevel.stale(999),"level shares monotonic freshness boundaries");
        s.minimum[0]=0;check(h.snapshot().minimum[0]==-1,"snapshot ownership");

        h.clear();AudioBlock silence=constant(2,1000000,0);h.add("boot-a",silence,1000);
        check(h.snapshot().rmsDb==Double.NEGATIVE_INFINITY&&h.snapshot().peak==0,"loud reference channel must not alter silent mic");
        check(h.levelSnapshot().rmsDb==Double.NEGATIVE_INFINITY,"level preserves digital silence instead of the AudioBlock -96 dBFS floor");
        java.util.Arrays.fill(silence.pcm,(byte)127);
        for(float v:h.snapshot().maximum)check(v==0,"producer PCM not retained");
        h.clear();h.add("boot-a",block(1,1000000,480,24000,2,(frame,channel)->channel==0?(frame%2==0?32:-32):32767),1000);
        s=h.snapshot();near(s.peak,32/32768.0,0,"weak mic retains absolute amplitude");near(s.rmsDb,-60.205999132796244,1e-9,"mic-only RMS dBFS");
        for(float v:s.maximum)near(v,32/32768.0,0,"no automatic normalization");
        check(!h.add("boot-a",constant(1,1020000,100),1500),"duplicate ignored");
        check(!h.add("boot-a",constant(0,1020000,100),1500),"backward sequence ignored");
        check(h.snapshot().lastReceiveElapsedMs==1000&&h.snapshot().stale(2000),"duplicates/backward packets cannot keep stopped audio fresh");
        check(h.levelSnapshot().lastReceiveElapsedMs==1000&&h.levelSnapshot().stale(2000),"duplicates/backward packets cannot keep level fresh");
        // A UI tick precedes the next producer packet. The tick's old clock
        // value is not the draw-time age of the subsequently copied snapshot.
        long oldUiTick=1500;
        h.add("boot-a",constant(2,1020000,32),1520);s=h.snapshot();
        check(!s.stale(oldUiTick)&&!s.stale(1521),"new packet after UI tick is fresh, not falsely expired by negative age");
        check(!s.stale(2519)&&s.stale(2520),"real one-second interruption still becomes stale at the exact boundary");
        h.add("boot-a",constant(3,1040000,32),3000);
        check(!h.snapshot().stale(3001)&&s.stale(3001),"recovery refreshes new snapshot without rewriting older snapshot age");
        h.clear();h.add("boot-a",block(1,1000000,480,24000,1,(frame,channel)->frame==183?32767:0),1000);
        check(h.snapshot().maximum[1]==32767/32768f,"one-frame transient retained by extrema");

        h.clear();h.add("boot-a",constant(0xffffffffL,1000000,100),1000);h.add("boot-a",constant(0,1020000,100),1020);
        check(h.snapshot().acceptedBlocks==2&&h.snapshot().recentGaps==0,"unsigned sequence wrap is contiguous");
        h.add("boot-a",constant(2,1040000,200),1040);s=h.snapshot();
        check(s.recentGaps==1&&s.breakBefore[8]&&s.size()==12,"sequence gap separates real bins without fabricated filler");
        h.add("boot-a",constant(3,1160000,300),1160);s=h.snapshot();
        check(s.recentGaps==2&&s.breakBefore[12]&&s.size()==16,"timestamp gap separates bins");
        check(s.startsUs[12]-s.endsUs[11]==100000,"timestamp gap is left empty");
        h.clear();h.add("boot-a",constant(1,1000000,100),1000);h.add("boot-a",constant(2,1019000,200),1020);
        check(h.snapshot().recentGaps==0,"sub-5ms timestamp jitter does not fabricate a missing packet");bounded(h.snapshot());
        h.add("boot-a",constant(3,500000,300),1040);s=h.snapshot();
        check(s.size()==4&&s.recentGaps==1&&s.startsUs[0]==500000,"clock regression starts a separate timeline");
        h.add("boot-b",constant(0,20000,400),1060);s=h.snapshot();
        check(s.size()==4&&s.acceptedBlocks==1&&s.recentGaps==0&&s.lastSequence==0,"new boot starts clean");
        h.add("boot-b",block(1,40000,160,8000,1,(frame,channel)->-100),1080);
        check(h.snapshot().recentGaps==1&&h.snapshot().sampleRate==8000&&h.snapshot().channels==1,"format change is separated");

        h.clear();h.add("latest",constant(1,1000000,-32768),1000);
        AudioWaveformHistory.LevelSnapshot fullScale=h.levelSnapshot();near(fullScale.rmsDb,0,0,"signed minimum is exact full-scale RMS");
        h.add("latest",constant(2,1020000,1024),1020);
        AudioWaveformHistory.LevelSnapshot latest=h.levelSnapshot();
        near(latest.rmsDb,20*Math.log10(1024/32768.0),1e-12,"latest weak block replaces loud level with no window averaging or peak hold");
        check(fullScale.rmsDb==0&&fullScale.lastReceiveElapsedMs==1000&&latest.lastSequence==2,"level snapshots are immutable values");
        check(h.snapshot().peak==1024/32768.0&&h.snapshot().size()==8,"level access preserves existing waveform history and latest peak");

        h.clear();for(int i=0;i<500;i++)h.add("long",constant(i,1000000+i*20000L,32),1000+i*20);
        s=h.snapshot();bounded(s);check(s.size()==400&&s.endUs-s.startsUs[0]==2000000,"ring retains exactly two seconds for fixed-format stream");
        check(s.recentGaps==0,"expired gaps do not persist");
        AtomicReference<Throwable> failure=new AtomicReference<>();
        Thread producer=new Thread(()->{try{for(int i=500;i<1000;i++)h.add("long",constant(i,1000000+i*20000L,i%32768),1000+i*20);}catch(Throwable e){failure.set(e);}});
        producer.start();for(int i=0;i<1000;i++){
            bounded(h.snapshot());AudioWaveformHistory.LevelSnapshot level=h.levelSnapshot();
            check(level.hasSample&&level.acceptedBlocks==level.lastSequence+1&&level.blockDurationUs==20000,"concurrent level snapshot is one coherent accepted block");
            near(level.rmsDb,20*Math.log10((level.lastSequence<500?32:level.lastSequence%32768)/32768.0),1e-9,"concurrent level RMS matches its sequence");
        }producer.join();
        if(failure.get()!=null)throw new AssertionError("producer",failure.get());
        check(h.snapshot().acceptedBlocks==1000,"concurrent snapshots do not lose producer blocks");
        h.clear();check(h.snapshot().size()==0&&Double.isNaN(h.snapshot().rmsDb),"clear removes level and history");
        check(!h.levelSnapshot().hasSample&&h.levelSnapshot().acceptedBlocks==0&&Double.isNaN(h.levelSnapshot().rmsDb),"clear removes lightweight current level");
        System.out.println("PASS: real first-channel PCM extrema/RMS, silence/reference isolation, fixed gain, gaps/wrap/boot/clock/format, monotonic draw-time freshness/true interruption/recovery, bounded ownership and producer/UI concurrency");
    }
}
