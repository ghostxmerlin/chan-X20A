package dev.stackchan.collector;

import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Set;
import java.util.Collections;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicReference;

/** Three bounded producers; no socket or media work is performed on the UI thread. */
public final class CollectorClient {
    public interface Listener {
        void info(JSONObject info);
        void status(String text, boolean connected);
        void frame(String boot, long seq, long timestampUs, int width, int height, byte[] jpeg);
        void telemetry(JSONObject value);
        void audio(String boot, AudioBlock value);
        void event(String type, String detail);
    }
    private final String base, token;
    private final Listener listener;
    private final ExecutorService workers=Executors.newFixedThreadPool(3);
    private final Set<HttpURLConnection> sockets=Collections.newSetFromMap(new ConcurrentHashMap<HttpURLConnection,Boolean>());
    private volatile boolean running=true;
    private volatile String boot="";
    private volatile int audioPort=8081;
    private volatile long frameIntervalMs=500;
    private volatile HttpURLConnection audioSocket;
    private volatile long audioEpoch=0;
    private long telemetrySeq=-1,frameSeq=-1;
    private long imuCursor=0,remoteCursor=0;
    private volatile boolean motorWanted=false;
    private volatile boolean standaloneControl=false;
    private final AtomicReference<Boolean> motorCommand=new AtomicReference<>();
    public void setMotorEnabled(boolean enabled){motorWanted=enabled;motorCommand.set(enabled);}

    public CollectorClient(String address,String token,Listener listener) throws Exception {
        String raw=address.trim(); if(!raw.contains("://"))raw="http://"+raw;
        URL u=new URL(raw);
        if(!u.getProtocol().equals("http") || u.getHost().isEmpty() || u.getUserInfo()!=null || u.getQuery()!=null || u.getRef()!=null)
            throw new IOException("请输入机器人局域网 IP，例如 192.168.1.20:8080");
        if(!token.matches("[0-9a-fA-F]{32}"))throw new IOException("配对码应为 32 位十六进制字符");
        base="http://"+u.getHost()+":"+(u.getPort()<0?8080:u.getPort());
        this.token=token;this.listener=listener;
    }
    public void start(){workers.execute(this::telemetryLoop);workers.execute(this::frameLoop);workers.execute(this::audioLoop);}
    public void close(){motorWanted=false;motorCommand.set(null);running=false;for(HttpURLConnection c:sockets)c.disconnect();workers.shutdownNow();}
    public String boot(){return boot;}
    private void pause(long ms){try{Thread.sleep(ms);}catch(InterruptedException ignored){Thread.currentThread().interrupt();}}
    private HttpURLConnection connect(String endpoint,boolean audio) throws IOException {
        URL u=new URL(audio?new URL(base).getProtocol()+"://"+new URL(base).getHost()+":"+audioPort+endpoint:base+endpoint);
        HttpURLConnection c=(HttpURLConnection)u.openConnection();
        c.setConnectTimeout(3000);c.setReadTimeout(audio?5000:3000);c.setUseCaches(false);
        c.setInstanceFollowRedirects(false);c.setRequestProperty("Authorization","Bearer "+token);
        c.setRequestProperty("Connection",audio?"close":"keep-alive");sockets.add(c);
        try{int status=c.getResponseCode();if(status!=200)throw new IOException(status==401||status==403?"配对码不正确":("HTTP "+status));return c;}
        catch(IOException e){release(c);throw e;}
    }
    private void release(HttpURLConnection c){if(c!=null){sockets.remove(c);c.disconnect();}}
    private byte[] readBody(HttpURLConnection c,int limit)throws IOException{
        try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] buf=new byte[8192];int n;
            while((n=in.read(buf))!=-1){if(out.size()+n>limit)throw new IOException("响应超过允许大小");out.write(buf,0,n);}return out.toByteArray();
        }
    }
    private JSONObject json(String path)throws Exception{HttpURLConnection c=null;try{c=connect(path,false);return new JSONObject(new String(readBody(c,65536),"UTF-8"));}finally{release(c);}}
    private void sendMotorState(boolean enabled)throws Exception{
        HttpURLConnection c=(HttpURLConnection)new URL(base+"/v1/remote").openConnection();sockets.add(c);
        try{c.setConnectTimeout(1500);c.setReadTimeout(1500);c.setInstanceFollowRedirects(false);c.setUseCaches(false);c.setRequestMethod("POST");c.setRequestProperty("Authorization","Bearer "+token);c.setRequestProperty("Content-Type","application/json");c.setDoOutput(true);
            byte[] body=(enabled?"{\"enabled\":true}":"{\"enabled\":false}").getBytes("UTF-8");c.setFixedLengthStreamingMode(body.length);try(java.io.OutputStream out=c.getOutputStream()){out.write(body);}
            if(c.getResponseCode()!=200)throw new IOException("舵机反馈或遥控接收不可用 (HTTP "+c.getResponseCode()+")");
            readBody(c,4096);
        }finally{release(c);}
    }
    private synchronized void acceptInfo(JSONObject info)throws Exception{
        if(info.optInt("protocol_version",0)!=1)throw new IOException("设备协议版本不兼容");
        String next=info.optString("boot_id","");if(next.isEmpty())throw new IOException("设备缺少启动标识");
        int port=info.optInt("audio_port",8081);if(port<1||port>65535)throw new IOException("设备音频端口无效");audioPort=port;
        JSONObject remote=info.optJSONObject("remote");
        standaloneControl=remote!=null&&"standalone".equals(remote.optString("control_mode"));
        if(!next.equals(boot)){
            String old=boot;boot=next;telemetrySeq=-1;frameSeq=-1;imuCursor=remoteCursor=0;motorWanted=false;motorCommand.set(null);audioEpoch++;
            HttpURLConnection active=audioSocket;if(active!=null)active.disconnect();
            if(!old.isEmpty())listener.event("device_reboot","启动标识变化；创建新分段");
        }
        JSONObject camera=info.optJSONObject("camera");
        double target=camera==null?2:camera.optDouble("target_fps",camera.optDouble("fps",2));
        frameIntervalMs=Double.isFinite(target)&&target>0?Math.max(100,Math.min(1000,Math.round(1000.0/target))):500;
        listener.info(info);
    }
    private void telemetryLoop(){
        boolean failed=false;long nextInfo=0,lastMotor=0;boolean sentMotor=false;
        while(running){long began=System.nanoTime();try{
            if(System.currentTimeMillis()>=nextInfo || boot.isEmpty()){acceptInfo(json("/v1/info"));nextInfo=System.currentTimeMillis()+3000;}
            Boolean command=motorCommand.getAndSet(null);
            boolean desired=command!=null?command:motorWanted;
            // A standalone receiver belongs to the remote, not this connection.
            // Only explicit button commands change it. Older firmware still gets its lease renewals.
            if(command!=null||(!standaloneControl&&(desired!=sentMotor||(desired&&System.nanoTime()-lastMotor>750000000L)))){
                try{sendMotorState(desired);if(command!=null||sentMotor!=desired)listener.event("remote_control",desired?"转头控制已启用，等待新的遥控输入":"转头控制已关闭");sentMotor=desired;lastMotor=System.nanoTime();}
                catch(Exception e){motorWanted=false;sentMotor=false;listener.event("remote_disabled","控制请求未确认："+e.getMessage());}
            }
            JSONObject t=json("/v1/telemetry?imu_after="+imuCursor+"&remote_after="+remoteCursor);String packetBoot=t.optString("boot_id");
            if(!packetBoot.equals(boot)){acceptInfo(json("/v1/info"));continue;}
            long seq=t.getLong("seq");boolean fresh;
            synchronized(this){fresh=seq!=telemetrySeq;if(fresh)telemetrySeq=seq;}
            if(failed){failed=false;listener.event("reconnect","遥测连接恢复；此前间隙已保留");}
            listener.status("已连接 · 正在接收",true);
            if(fresh){listener.telemetry(t);imuCursor=t.optJSONArray("imu_samples")==null?0:seq;JSONObject remote=t.optJSONObject("remote");if(remote!=null)remoteCursor=remote.optLong("rx_packets");
                if(t.optLong("imu_history_lost")>0)listener.event("imu_gap","设备运动缓存缺少 "+t.optLong("imu_history_lost")+" 个样本");
                if(remote!=null&&remote.optLong("history_lost")>0)listener.event("remote_gap","遥控缓存缺少 "+remote.optLong("history_lost")+" 条输入");
            }
        }catch(Exception e){
            motorWanted=false;motorCommand.set(null);
            if(running){if(!failed){failed=true;listener.event("connection_gap","遥测中断："+e.getMessage());}
            listener.status("连接中断 · 自动重试（"+e.getMessage()+"）",false);nextInfo=0;pause(1200);}
        }
        long elapsed=(System.nanoTime()-began)/1000000;pause(Math.max(0,100-elapsed));}
    }
    private void frameLoop(){boolean failed=false;while(running){long began=System.nanoTime();HttpURLConnection c=null;
        try{
            if(boot.isEmpty()){pause(300);continue;}
            c=connect("/v1/frame",false);String b=c.getHeaderField("X-Boot-Id");
            if(b==null||!b.equals(boot)){pause(300);continue;}
            long seq=Long.parseLong(c.getHeaderField("X-Seq"));long timestamp=Long.parseLong(c.getHeaderField("X-Timestamp-Us"));
            int width=Integer.parseInt(c.getHeaderField("X-Width")),height=Integer.parseInt(c.getHeaderField("X-Height"));
            if(width<1||height<1||width>1920||height>1920)throw new IOException("图像尺寸无效");
            byte[] jpeg=readBody(c,1048576);
            if(failed){failed=false;listener.event("camera_reconnect","图像恢复");}
            synchronized(this){if(b.equals(boot)&&seq!=frameSeq){frameSeq=seq;listener.frame(b,seq,timestamp,width,height,jpeg);}}
        }catch(Exception e){if(running&&!failed){failed=true;listener.event("camera_gap","图像暂不可用："+e.getMessage());}}
        finally{release(c);}
        pause(Math.max(1,frameIntervalMs-(System.nanoTime()-began)/1000000));
    }}
    private void audioLoop(){
        boolean failed=false;long lastSeq=-1;String lastBoot="";long lastEndUs=-1;
        while(running){HttpURLConnection c=null;try{
            if(boot.isEmpty()){pause(300);continue;}
            String streamBoot=boot;long epoch=audioEpoch;c=connect("/v1/audio",true);audioSocket=c;
            if(!streamBoot.equals(c.getHeaderField("X-Boot-Id"))||epoch!=audioEpoch)
                throw new IOException("音频启动标识变化，等待设备信息刷新");
            try(InputStream in=c.getInputStream()){
                while(running&&epoch==audioEpoch){
                    AudioBlock block=AudioBlock.read(in);
                    if(epoch!=audioEpoch)break;
                    if(!streamBoot.equals(lastBoot)){lastBoot=streamBoot;lastSeq=-1;lastEndUs=-1;}
                    if(block.seq==lastSeq)continue;
                    if(lastSeq>=0 && ((lastSeq+1)&0xffffffffL)!=block.seq)
                        listener.event("audio_gap","音频序号 "+lastSeq+" → "+block.seq);
                    if(lastEndUs>=0&&Math.abs(block.timestampUs-lastEndUs)>100000)
                        listener.event("audio_time_gap","音频时间间隙 "+(block.timestampUs-lastEndUs)+" µs");
                    lastSeq=block.seq;lastEndUs=block.timestampUs+(1000000L*block.frames/block.sampleRate);
                    if(failed){failed=false;listener.event("audio_reconnect","音频流恢复；新建音频文件");}
                    synchronized(this){if(epoch==audioEpoch&&streamBoot.equals(boot))listener.audio(streamBoot,block);}
                }
            }
        }catch(Exception e){if(running&&!failed){failed=true;listener.event("audio_gap","音频流中断："+e.getMessage());}}
        finally{audioSocket=null;release(c);}
        if(running)pause(1200);
    }}
}
