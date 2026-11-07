package dev.stackchan.collector;

/** Real first-channel PCM extrema in bounded device-time buckets; no gain or interpolation. */
public final class AudioWaveformHistory {
    public static final long WINDOW_US=2000000, BIN_US=5000, STALE_AFTER_MS=1000;
    private static final int CAPACITY=512;
    private static final long TIMESTAMP_TOLERANCE_US=5000;
    private final long[] starts=new long[CAPACITY],ends=new long[CAPACITY],bins=new long[CAPACITY],segments=new long[CAPACITY];
    private final short[] lows=new short[CAPACITY],highs=new short[CAPACITY];
    private final boolean[] breaks=new boolean[CAPACITY],gaps=new boolean[CAPACITY];
    private int count,next,sampleRate,channels;
    private long lastSequence,lastTimestamp,lastEnd,lastElapsedMs,lastDurationUs,segment,acceptedBlocks;
    private double rmsDb=Double.NaN,peak=Double.NaN;
    private String boot="";
    private boolean initialized;

    /** Independent copy: UI mutation cannot affect the producer's ring. */
    public static final class Snapshot {
        public final long[] startsUs,endsUs;
        public final float[] minimum,maximum;
        public final boolean[] breakBefore;
        public final long endUs,windowStartUs,lastReceiveElapsedMs,lastSequence,acceptedBlocks;
        public final double rmsDb,peak;
        /** Sequence jumps, timestamp discontinuities or format changes, not a packet-loss count. */
        public final int recentGaps,sampleRate,channels;
        private Snapshot(int size,long end,long received,long sequence,long accepted,double db,double peak,int gaps,int rate,int channels){
            startsUs=new long[size];endsUs=new long[size];minimum=new float[size];maximum=new float[size];breakBefore=new boolean[size];
            endUs=end;windowStartUs=Math.max(0,end-WINDOW_US);lastReceiveElapsedMs=received;lastSequence=sequence;acceptedBlocks=accepted;
            rmsDb=db;this.peak=peak;recentGaps=gaps;sampleRate=rate;this.channels=channels;
        }
        public int size(){return startsUs.length;}
        /** Sample the same monotonic clock after taking this snapshot, never at an earlier UI tick. */
        public boolean stale(long nowElapsedMs){return size()==0||nowElapsedMs-lastReceiveElapsedMs>=STALE_AFTER_MS;}
    }

    /** Latest accepted PCM block only; no ring scan, array copy, averaging or peak hold. */
    public static final class LevelSnapshot {
        public final boolean hasSample;
        public final long lastReceiveElapsedMs,lastSequence,acceptedBlocks,blockDurationUs;
        public final double rmsDb;
        private LevelSnapshot(boolean hasSample,long received,long sequence,long accepted,long duration,double db){
            this.hasSample=hasSample;lastReceiveElapsedMs=received;lastSequence=sequence;acceptedBlocks=accepted;
            blockDurationUs=duration;rmsDb=db;
        }
        /** Read the same monotonic clock after taking this immutable snapshot. */
        public boolean stale(long nowElapsedMs){return !hasSample||nowElapsedMs-lastReceiveElapsedMs>=STALE_AFTER_MS;}
    }

    public synchronized void clear(){reset();boot="";}
    private void reset(){count=next=0;initialized=false;segment=acceptedBlocks=0;lastSequence=lastTimestamp=lastEnd=lastElapsedMs=lastDurationUs=0;rmsDb=peak=Double.NaN;sampleRate=channels=0;}

    /** Producer receive time is Android elapsedRealtime(), not wall time or device sample time. */
    public synchronized boolean add(String bootId,AudioBlock block,long receivedElapsedMs){
        if(bootId==null||bootId.isEmpty()||block==null||block.bits!=16||block.sampleRate<=0||block.frames<=0||block.channels<=0||
           (long)block.frames*block.channels*2!=block.pcm.length||block.timestampUs<0)return false;
        long duration=(long)block.frames*1000000/block.sampleRate;
        if(duration<=0||block.timestampUs>Long.MAX_VALUE-duration)return false;
        if(!bootId.equals(boot)){reset();boot=bootId;}
        long delta=initialized?(block.seq-lastSequence)&0xffffffffL:1;
        if(initialized&&(delta==0||delta>=0x80000000L))return false;
        boolean gap=initialized&&(delta!=1||block.sampleRate!=sampleRate||block.channels!=channels||
                                  Math.abs(block.timestampUs-lastEnd)>TIMESTAMP_TOLERANCE_US);
        if(initialized&&(block.timestampUs<=lastTimestamp||block.timestampUs<lastEnd-TIMESTAMP_TOLERANCE_US)){
            // A reset clock cannot share an x axis with the previous timeline.
            count=next=0;gap=true;
        }
        if(gap)segment++;
        double squares=0;int absolutePeak=0;
        for(int frame=0;frame<block.frames;frame++){
            int offset=frame*block.channels*2;
            short value=(short)((block.pcm[offset]&255)|((block.pcm[offset+1]&255)<<8));
            squares+=(double)value*value;absolutePeak=Math.max(absolutePeak,Math.abs((int)value));
            long start=block.timestampUs+(long)frame*1000000/block.sampleRate;
            long end=block.timestampUs+(long)(frame+1)*1000000/block.sampleRate;
            addSample(start,end,value,gap&&frame==0);
        }
        lastSequence=block.seq;lastTimestamp=block.timestampUs;lastEnd=block.timestampUs+duration;lastElapsedMs=receivedElapsedMs;lastDurationUs=duration;
        sampleRate=block.sampleRate;channels=block.channels;initialized=true;acceptedBlocks++;
        rmsDb=squares==0?Double.NEGATIVE_INFINITY:20*Math.log10(Math.sqrt(squares/block.frames)/32768.0);
        peak=absolutePeak/32768.0;
        long cutoff=lastEnd-WINDOW_US;
        while(count>0&&ends[(next-count+CAPACITY)%CAPACITY]<=cutoff)count--;
        return true;
    }

    private void addSample(long start,long end,short value,boolean gap){
        long bin=start/BIN_US;
        // Read-completion timestamps can overlap slightly. Merge only measured
        // samples in the same time bucket and segment, never across a gap.
        if(!gap)for(int n=0;n<count;n++){
            int i=(next-1-n+CAPACITY)%CAPACITY;
            if(segments[i]!=segment||bins[i]<bin)break;
            if(bins[i]==bin){lows[i]=(short)Math.min(lows[i],value);highs[i]=(short)Math.max(highs[i],value);
                starts[i]=Math.min(starts[i],start);ends[i]=Math.max(ends[i],end);return;}
        }
        starts[next]=start;ends[next]=end;bins[next]=bin;segments[next]=segment;lows[next]=highs[next]=value;
        breaks[next]=count==0||gap;gaps[next]=gap;
        next=(next+1)%CAPACITY;count=Math.min(CAPACITY,count+1);
    }

    public synchronized Snapshot snapshot(){
        int gapCount=0;for(int n=0;n<count;n++)if(gaps[(next-count+n+CAPACITY)%CAPACITY])gapCount++;
        Snapshot copy=new Snapshot(count,lastEnd,lastElapsedMs,lastSequence,acceptedBlocks,rmsDb,peak,gapCount,sampleRate,channels);
        for(int n=0;n<count;n++){
            int i=(next-count+n+CAPACITY)%CAPACITY;
            copy.startsUs[n]=starts[i];copy.endsUs[n]=ends[i];copy.minimum[n]=lows[i]/32768f;copy.maximum[n]=highs[i]/32768f;
            copy.breakBefore[n]=n==0||breaks[i];
        }
        return copy;
    }

    public synchronized LevelSnapshot levelSnapshot(){
        return new LevelSnapshot(initialized,lastElapsedMs,lastSequence,acceptedBlocks,lastDurationUs,rmsDb);
    }
}
