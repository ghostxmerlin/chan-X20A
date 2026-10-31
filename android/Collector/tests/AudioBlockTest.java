package dev.stackchan.collector;
import java.io.*;
import java.nio.*;
public final class AudioBlockTest {
    private static byte[] packet(int frames,int channels,int bytes){ByteBuffer b=ByteBuffer.allocate(32+Math.max(0,bytes)).order(ByteOrder.LITTLE_ENDIAN);b.put(new byte[]{'S','C','A','1'}).putInt(-1).putLong(123456789L).putInt(24000).putInt(frames).putShort((short)channels).putShort((short)16).putInt(bytes);return b.array();}
    private static void rejects(byte[] b)throws Exception{try{AudioBlock.read(new ByteArrayInputStream(b));throw new AssertionError("invalid input accepted");}catch(IOException expected){}}
    public static void main(String[] args)throws Exception{
        byte[] raw=packet(480,2,1920);InputStream fragmented=new FilterInputStream(new ByteArrayInputStream(raw)){public int read(byte[] b,int off,int len)throws IOException{return super.read(b,off,Math.min(7,len));}};
        AudioBlock a=AudioBlock.read(fragmented);if(a.seq!=4294967295L||a.timestampUs!=123456789L||a.frames!=480||a.pcm.length!=1920||a.rmsDb()!=-96)throw new AssertionError("valid fragmented frame");
        byte[] channelRaw=packet(2,2,8);ByteBuffer.wrap(channelRaw).order(ByteOrder.LITTLE_ENDIAN).putShort(32,(short)1234).putShort(34,(short)-999).putShort(36,(short)-4321).putShort(38,(short)888);
        byte[] mono=AudioBlock.read(new ByteArrayInputStream(channelRaw)).firstChannelPcm();ByteBuffer m=ByteBuffer.wrap(mono).order(ByteOrder.LITTLE_ENDIAN);
        if(mono.length!=4||m.getShort()!=1234||m.getShort()!=-4321)throw new AssertionError("first channel extraction mixed reference channel");
        rejects(new byte[0]);rejects(java.util.Arrays.copyOf(raw,raw.length-1));byte[] bad=raw.clone();bad[0]='X';rejects(bad);rejects(packet(480,0,1));rejects(packet(480,2,5));
        byte[] huge=packet(1,1,0);ByteBuffer.wrap(huge).order(ByteOrder.LITTLE_ENDIAN).putInt(28,Integer.MAX_VALUE);rejects(huge);
        System.out.println("AudioBlockTest: fragmented reads, unsigned sequence, first-channel isolation, EOF, invalid magic, dimensions and bounded allocation PASS");
    }
}
