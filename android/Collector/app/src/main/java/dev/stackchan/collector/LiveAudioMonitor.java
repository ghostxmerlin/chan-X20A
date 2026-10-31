package dev.stackchan.collector;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

/** Optional local speaker output. It has no ownership of the recorder or network stream. */
final class LiveAudioMonitor {
    interface Listener { void error(String message); }
    private final ArrayBlockingQueue<AudioBlock> queue=new ArrayBlockingQueue<>(4);
    private volatile boolean active=true;
    private final Thread worker;
    LiveAudioMonitor(Listener listener){
        worker=new Thread(()->{
            AudioTrack track=null;int rate=0;
            try{
                while(active){
                    AudioBlock block=queue.poll(250,TimeUnit.MILLISECONDS);if(block==null)continue;
                    if(track==null||rate!=block.sampleRate){
                        if(track!=null){track.pause();track.flush();track.release();}
                        rate=block.sampleRate;
                        int minimum=AudioTrack.getMinBufferSize(rate,AudioFormat.CHANNEL_OUT_MONO,AudioFormat.ENCODING_PCM_16BIT);
                        if(minimum<=0)throw new IllegalStateException("系统不支持此采样率");
                        track=new AudioTrack.Builder()
                            .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                            .setAudioFormat(new AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                            .setTransferMode(AudioTrack.MODE_STREAM).setBufferSizeInBytes(Math.max(minimum,rate/5))
                            .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY).build();
                        if(track.getState()!=AudioTrack.STATE_INITIALIZED)throw new IllegalStateException("音频播放初始化失败");
                        track.play();
                    }
                    byte[] mono=block.firstChannelPcm();int offset=0;
                    long deadline=android.os.SystemClock.elapsedRealtime()+100;
                    while(active&&offset<mono.length&&android.os.SystemClock.elapsedRealtime()<deadline){
                        int n=track.write(mono,offset,mono.length-offset,AudioTrack.WRITE_NON_BLOCKING);
                        if(n<0)throw new IllegalStateException("系统音频写入失败："+n);
                        if(n==0)Thread.sleep(3);else offset+=n;
                    }
                }
            }catch(InterruptedException ignored){Thread.currentThread().interrupt();}
            catch(Exception e){if(active)listener.error(e.getMessage()==null?"声音监听不可用":e.getMessage());}
            finally{active=false;queue.clear();if(track!=null){try{track.pause();track.flush();}catch(Exception ignored){}track.release();}}
        },"collector-audio-monitor");
        worker.start();
    }
    void offer(AudioBlock block){if(!active)return;if(!queue.offer(block)){queue.poll();queue.offer(block);}}
    void close(){active=false;queue.clear();worker.interrupt();}
}
