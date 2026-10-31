package dev.stackchan.collector;

import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/** Wire framing only. Kept independent of Android for host-side boundary tests. */
public final class AudioBlock {
    public final long seq, timestampUs;
    public final int sampleRate, frames, channels, bits;
    public final byte[] pcm;
    private AudioBlock(long seq, long timestampUs, int rate, int frames, int channels, int bits, byte[] pcm) {
        this.seq=seq; this.timestampUs=timestampUs; this.sampleRate=rate; this.frames=frames;
        this.channels=channels; this.bits=bits; this.pcm=pcm;
    }
    public static AudioBlock read(InputStream in) throws IOException {
        byte[] header = new byte[32];
        readFully(in, header);
        if (header[0]!='S' || header[1]!='C' || header[2]!='A' || header[3]!='1') throw new IOException("音频块标识不匹配");
        ByteBuffer b=ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN);
        long seq=b.getInt(4)&0xffffffffL, timestamp=b.getLong(8);
        int rate=b.getInt(16), frames=b.getInt(20), channels=b.getShort(24)&0xffff, bits=b.getShort(26)&0xffff;
        int bytes=b.getInt(28);
        // Defend allocation and multiplication before reading an untrusted LAN payload.
        if (rate<8000 || rate>96000 || channels<1 || channels>8 || bits!=16 || frames<1 || frames>rate ||
                bytes<1 || bytes>1048576 || (long)frames*channels*2!=bytes || timestamp<0) throw new IOException("音频块参数无效");
        byte[] pcm=new byte[bytes]; readFully(in,pcm);
        return new AudioBlock(seq,timestamp,rate,frames,channels,bits,pcm);
    }
    public static void readFully(InputStream in, byte[] dst) throws IOException {
        int offset=0;
        while (offset<dst.length) {
            int n=in.read(dst,offset,dst.length-offset);
            if(n<0) throw new EOFException("音频流已结束");
            if(n==0) continue;
            offset+=n;
        }
    }
    public double rmsDb() {
        double sum=0; int samples=0;
        for(int i=0;i+1<pcm.length;i+=channels*2){int v=(short)((pcm[i]&255)|(pcm[i+1]<<8));sum+=(double)v*v;samples++;}
        return Math.max(-96,20*Math.log10(Math.max(1e-9,Math.sqrt(sum/Math.max(1,samples))/32768.0)));
    }
    public byte[] firstChannelPcm(){
        byte[] mono=new byte[frames*2];
        for(int i=0;i<frames;i++){int source=i*channels*2;mono[i*2]=pcm[source];mono[i*2+1]=pcm[source+1];}
        return mono;
    }
}
