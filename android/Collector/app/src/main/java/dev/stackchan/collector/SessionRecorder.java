package dev.stackchan.collector;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/** One writer owns all files. Producers never wait for disk; rejected data is counted. */
public final class SessionRecorder {
    public interface Listener {void finished(File folder, String error);}
    interface Work {void run() throws Exception;}
    public final File folder;
    private final ArrayBlockingQueue<Work> queue=new ArrayBlockingQueue<>(256);
    private final AtomicLong dropped=new AtomicLong(),dropPending=new AtomicLong();
    private final Listener listener;
    private final JSONObject manifest=new JSONObject();
    private final JSONArray segments=new JSONArray();
    private volatile boolean accepting=true;
    private volatile String stopReason;
    private boolean finished=false;
    private BufferedWriter events,frames,telemetry,audioIndex,motionCsv,remoteCsv,servoCsv;
    private long lastImuSeq=-1,lastRemoteSeq=-1,motionSamples=0,remotePackets=0;
    private File segmentFolder;
    private String segmentBoot="";
    private int segmentNo=0,audioNo=0;
    private RandomAccessFile wav;
    private long wavBytes=0,audioFrames=0,frameCount=0,telemetryCount=0,audioBlocks=0,lastAudioEnd=-1,lastWrittenAudioSeq=-1;
    private int rate=0,channels=0;
    private String wavName="";
    private final long startedWall=System.currentTimeMillis();

    public SessionRecorder(File root,JSONObject info,String appVersion,Listener listener)throws Exception{
        this.listener=listener;
        String id=new SimpleDateFormat("yyyyMMdd-HHmmss-SSS",Locale.ROOT).format(new Date());
        folder=new File(root,id);if(!folder.mkdirs())throw new IOException("无法创建记录目录");
        events=writer(new File(folder,"events.jsonl"));
        try{
        manifest.put("format","stackchan-collector");manifest.put("format_version",1);manifest.put("app_version",appVersion);
        manifest.put("started_at",utc(startedWall));manifest.put("started_wall_ms",startedWall);manifest.put("status","recording");
        manifest.put("device_info",info);manifest.put("segments",segments);
        manifest.put("time_basis","device monotonic microseconds per boot_id; receive_elapsed_us is Android elapsedRealtimeNanos/1000; received_wall_ms is host wall time");
        manifest.put("audio_note","PCM16LE WAV; channel roles from device_info.audio.channel_roles; gaps are never filled with fabricated samples");
        manifest.put("motion_note","Raw head-mounted sensor axes; acceleration includes gravity. Mounting to shoulder/body is not calibrated. No absolute heading, GPS or gravity-free acceleration is inferred.");
        manifest.put("remote_note","Official mapped yaw/pitch/speed/auxiliary payload; no raw stick, remote IMU, sender sequence or heartbeat. Servo feedback is a separate measured signal; invalid values remain empty in CSV.");
        writeManifest();
        }catch(Exception e){try{events.close();}catch(Exception ignored){}events=null;throw e;}
        new Thread(()->{
            try{while(!finished){Work w=queue.poll(250,TimeUnit.MILLISECONDS);long n=dropPending.getAndSet(0);if(n>0){eventNow("storage_gap","写盘队列满，丢弃 "+n+" 个数据项",System.currentTimeMillis(),android.os.SystemClock.elapsedRealtimeNanos()/1000);closeAudio();}
                if(w!=null)w.run();else if(stopReason!=null)finish();}}
            catch(Exception e){accepting=false;finished=true;queue.clear();
                try{closeSegment();}catch(Exception ignored){}
                try{if(events!=null)events.close();}catch(Exception ignored){}finally{events=null;}
                try{manifest.put("status","error");manifest.put("error",e.getClass().getSimpleName()+": "+e.getMessage());manifest.put("dropped_queue_items",dropped.get());writeManifest();}catch(Exception ignored){}
                listener.finished(folder,e.getMessage()==null?e.getClass().getSimpleName():e.getMessage());}
        },"collector-file-writer").start();
    }
    public long dropped(){return dropped.get();}
    public boolean accepting(){return accepting;}
    private synchronized void submit(Work w){if(!accepting)return;if(!queue.offer(w)){dropped.incrementAndGet();dropPending.incrementAndGet();}}
    public void frame(String boot,long seq,long timestamp,int width,int height,byte[] jpeg){
        long wall=System.currentTimeMillis(),elapsed=android.os.SystemClock.elapsedRealtimeNanos()/1000;
        submit(()->{ensureSegment(boot);String name=String.format(Locale.ROOT,"frames/%010d.jpg",seq);
            try(FileOutputStream out=new FileOutputStream(new File(segmentFolder,name))){out.write(jpeg);}
            JSONObject row=envelope(wall,elapsed);row.put("boot_id",boot);row.put("seq",seq);row.put("timestamp_us",timestamp);row.put("width",width);row.put("height",height);row.put("file",name);
            line(frames,row);frameCount++;if(frameCount%10==0)frames.flush();
        });
    }
    public void telemetry(JSONObject value){
        long wall=System.currentTimeMillis(),elapsed=android.os.SystemClock.elapsedRealtimeNanos()/1000;
        submit(()->{ensureSegment(value.getString("boot_id"));JSONObject row=new JSONObject(value.toString());row.put("received_wall_ms",wall);row.put("receive_elapsed_us",elapsed);line(telemetry,row);writeMotionRows(value,elapsed);telemetryCount++;if(telemetryCount%20==0){telemetry.flush();motionCsv.flush();remoteCsv.flush();servoCsv.flush();}});
    }
    private void writeMotionRows(JSONObject value,long received)throws Exception{
        JSONArray batch=value.optJSONArray("imu_samples");
        if(batch==null){JSONObject imu=value.optJSONObject("imu");if(imu!=null){JSONObject one=new JSONObject(imu.toString());one.put("seq",value.optLong("seq"));batch=new JSONArray().put(one);}}
        if(batch!=null)for(int i=0;i<batch.length();i++){
            JSONObject s=batch.optJSONObject(i);if(s==null||s.optLong("seq",-1)<=lastImuSeq)continue;lastImuSeq=s.optLong("seq");boolean valid=s.optBoolean("valid");
            StringBuilder line=new StringBuilder();line.append(lastImuSeq).append(',').append(s.optLong("timestamp_us")).append(',').append(received).append(',').append(valid);
            for(String key:new String[]{"accel","gyro"}){JSONArray v=s.optJSONArray(key);for(int a=0;a<3;a++)line.append(',').append(valid?csvNumber(v==null?null:v.opt(a)):"");}
            motionCsv.write(line.toString());motionCsv.newLine();motionSamples++;
        }
        JSONObject remote=value.optJSONObject("remote");if(remote==null)return;JSONArray packets=remote.optJSONArray("packets");
        if(packets!=null)for(int i=0;i<packets.length();i++){
            JSONObject p=packets.optJSONObject(i);if(p==null||p.optLong("seq",-1)<=lastRemoteSeq)continue;lastRemoteSeq=p.optLong("seq");
            remoteCsv.write(lastRemoteSeq+","+p.optLong("timestamp_us")+","+received+","+p.optInt("target_id")+","+csvNumber(p.opt("yaw_deg"))+","+csvNumber(p.opt("pitch_deg"))+","+p.optInt("speed")+","+p.optBoolean("auxiliary")+","+p.optInt("rssi_dbm")+","+p.optInt("channel")+","+p.optString("source_mac"));remoteCsv.newLine();remotePackets++;
        }
        JSONObject m=remote.optJSONObject("motor");if(m!=null){
            StringBuilder line=new StringBuilder();line.append(m.optLong("timestamp_us")).append(',').append(received).append(',').append(m.optBoolean("enabled")).append(',').append(m.optLong("applied_seq")).append(',').append(csvNumber(m.opt("yaw_target_deg"))).append(',').append(csvNumber(m.opt("pitch_target_deg"))).append(',').append(csvNumber(m.opt("applied_speed")));
            for(String key:new String[]{"yaw","pitch"}){JSONObject f=m.optJSONObject(key);boolean valid=f!=null&&f.optBoolean("valid")&&m.optLong("feedback_age_ms",-1)>=0&&m.optLong("feedback_age_ms",-1)<1000;line.append(',').append(valid);for(String field:new String[]{"angle_deg","position_raw","load_raw","current_raw"})line.append(',').append(valid?csvNumber(f.opt(field)):"");}
            servoCsv.write(line.toString());servoCsv.newLine();
        }
    }
    private static String csvNumber(Object value){return value instanceof Number&&Double.isFinite(((Number)value).doubleValue())?value.toString():"";}
    public void audio(String boot,AudioBlock block){
        long wall=System.currentTimeMillis(),elapsed=android.os.SystemClock.elapsedRealtimeNanos()/1000;
        submit(()->{ensureSegment(boot);
            if(lastWrittenAudioSeq>=0&&((lastWrittenAudioSeq+1)&0xffffffffL)!=block.seq){
                eventNow("recorded_audio_gap","落盘音频序号 "+lastWrittenAudioSeq+" → "+block.seq,wall,elapsed);closeAudio();
            }
            if(wav!=null&&(rate!=block.sampleRate||channels!=block.channels||wavBytes+block.pcm.length>268435456L||
                    (lastAudioEnd>=0&&Math.abs(block.timestampUs-lastAudioEnd)>100000)))closeAudio();
            if(wav==null){rate=block.sampleRate;channels=block.channels;wavName=String.format(Locale.ROOT,"audio-%03d.wav",++audioNo);wav=new RandomAccessFile(new File(segmentFolder,wavName),"rw");wav.write(new byte[44]);wavBytes=0;audioFrames=0;}
            JSONObject row=envelope(wall,elapsed);row.put("boot_id",boot);row.put("seq",block.seq);row.put("timestamp_us",block.timestampUs);row.put("sample_rate",rate);row.put("channels",channels);row.put("bits",block.bits);row.put("frames",block.frames);row.put("file",wavName);row.put("sample_offset",audioFrames);row.put("byte_offset",44+wavBytes);row.put("payload_bytes",block.pcm.length);
            wav.write(block.pcm);wavBytes+=block.pcm.length;audioFrames+=block.frames;audioBlocks++;lastWrittenAudioSeq=block.seq;lastAudioEnd=block.timestampUs+(1000000L*block.frames/block.sampleRate);line(audioIndex,row);
            if(audioBlocks%100==0){audioIndex.flush();writeWavHeader();}
        });
    }
    public void event(String type,String detail){long wall=System.currentTimeMillis(),elapsed=android.os.SystemClock.elapsedRealtimeNanos()/1000;
        submit(()->{eventNow(type,detail,wall,elapsed);
            if(type.equals("device_reboot")||type.equals("connection_gap"))closeSegment();
            else if(type.equals("audio_gap")||type.equals("audio_reconnect")||type.equals("audio_time_gap"))closeAudio();
        });
    }
    public synchronized void stop(String reason){
        if(!accepting)return;accepting=false;stopReason=reason;
    }
    private void finish()throws Exception{eventNow("recording_stopped",stopReason,System.currentTimeMillis(),android.os.SystemClock.elapsedRealtimeNanos()/1000);closeSegment();events.close();events=null;manifest.put("status","complete");manifest.put("ended_at",utc(System.currentTimeMillis()));manifest.put("duration_ms",System.currentTimeMillis()-startedWall);manifest.put("frames",frameCount);manifest.put("telemetry_samples",telemetryCount);manifest.put("imu_samples",motionSamples);manifest.put("remote_packets",remotePackets);manifest.put("audio_blocks",audioBlocks);manifest.put("dropped_queue_items",dropped.get());writeManifest();finished=true;listener.finished(folder,null);}
    private void ensureSegment(String boot)throws Exception{
        if(segmentFolder!=null&&boot.equals(segmentBoot))return;
        closeSegment();segmentBoot=boot;String name=String.format(Locale.ROOT,"segment-%03d",++segmentNo);segmentFolder=new File(folder,name);
        if(!new File(segmentFolder,"frames").mkdirs())throw new IOException("无法创建分段目录");
        frames=writer(new File(segmentFolder,"frames.jsonl"));telemetry=writer(new File(segmentFolder,"telemetry.jsonl"));audioIndex=writer(new File(segmentFolder,"audio.jsonl"));audioNo=0;lastWrittenAudioSeq=-1;
        motionCsv=writer(new File(segmentFolder,"motion.csv"));motionCsv.write("seq,timestamp_us,receive_elapsed_us,valid,ax_mps2,ay_mps2,az_mps2,gx_dps,gy_dps,gz_dps\n");
        remoteCsv=writer(new File(segmentFolder,"remote.csv"));remoteCsv.write("seq,timestamp_us,receive_elapsed_us,target_id,yaw_deg,pitch_deg,speed,auxiliary,rssi_dbm,channel,source_mac\n");
        servoCsv=writer(new File(segmentFolder,"servos.csv"));servoCsv.write("timestamp_us,receive_elapsed_us,enabled,applied_seq,yaw_target_deg,pitch_target_deg,applied_speed,yaw_valid,yaw_deg,yaw_position_raw,yaw_load_raw,yaw_current_raw,pitch_valid,pitch_deg,pitch_position_raw,pitch_load_raw,pitch_current_raw\n");lastImuSeq=lastRemoteSeq=-1;
        JSONObject s=new JSONObject();s.put("directory",name);s.put("boot_id",boot);s.put("started_wall_ms",System.currentTimeMillis());segments.put(s);writeManifest();eventNow("segment_started",name+" boot="+boot,System.currentTimeMillis(),android.os.SystemClock.elapsedRealtimeNanos()/1000);
    }
    private void closeSegment()throws Exception{
        Exception failure=null;
        try{closeAudio();}catch(Exception e){failure=e;}
        try{if(frames!=null)frames.close();}catch(Exception e){if(failure==null)failure=e;}finally{frames=null;}
        try{if(telemetry!=null)telemetry.close();}catch(Exception e){if(failure==null)failure=e;}finally{telemetry=null;}
        try{if(motionCsv!=null)motionCsv.close();}catch(Exception e){if(failure==null)failure=e;}finally{motionCsv=null;}
        try{if(remoteCsv!=null)remoteCsv.close();}catch(Exception e){if(failure==null)failure=e;}finally{remoteCsv=null;}
        try{if(servoCsv!=null)servoCsv.close();}catch(Exception e){if(failure==null)failure=e;}finally{servoCsv=null;}
        try{if(audioIndex!=null)audioIndex.close();}catch(Exception e){if(failure==null)failure=e;}finally{audioIndex=null;segmentFolder=null;}
        if(failure!=null)throw failure;
    }
    private void closeAudio()throws Exception{try{if(wav!=null){try{writeWavHeader();}finally{try{wav.close();}finally{wav=null;}}}}finally{lastAudioEnd=-1;}}
    private void writeWavHeader()throws Exception{
        ByteBuffer b=ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);b.put(new byte[]{'R','I','F','F'}).putInt((int)(wavBytes+36)).put(new byte[]{'W','A','V','E','f','m','t',' '}).putInt(16).putShort((short)1).putShort((short)channels).putInt(rate).putInt(rate*channels*2).putShort((short)(channels*2)).putShort((short)16).put(new byte[]{'d','a','t','a'}).putInt((int)wavBytes);wav.seek(0);wav.write(b.array());wav.seek(44+wavBytes);
    }
    private void eventNow(String type,String detail,long wall,long elapsed)throws Exception{JSONObject row=envelope(wall,elapsed);row.put("type",type);row.put("detail",detail);row.put("boot_id",segmentBoot);line(events,row);events.flush();}
    private JSONObject envelope(long wall,long elapsed)throws Exception{JSONObject o=new JSONObject();o.put("received_wall_ms",wall);o.put("receive_elapsed_us",elapsed);return o;}
    private void writeManifest()throws Exception{File tmp=new File(folder,"manifest.tmp");try(FileOutputStream out=new FileOutputStream(tmp)){out.write(manifest.toString(2).getBytes("UTF-8"));out.getFD().sync();}if(!tmp.renameTo(new File(folder,"manifest.json")))throw new IOException("无法保存会话清单");}
    private static BufferedWriter writer(File file)throws Exception{return new BufferedWriter(new OutputStreamWriter(new FileOutputStream(file),"UTF-8"));}
    private static void line(BufferedWriter out,JSONObject row)throws Exception{out.write(row.toString());out.newLine();}
    private static String utc(long millis){SimpleDateFormat f=new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",Locale.ROOT);f.setTimeZone(TimeZone.getTimeZone("UTC"));return f.format(new Date(millis));}
}
