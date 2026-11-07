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
        default void frameWithFaces(String boot,long seq,long timestampUs,int width,int height,byte[] jpeg,FaceBoxes faces){frame(boot,seq,timestampUs,width,height,jpeg);}
        void telemetry(JSONObject value);
        void audio(String boot, AudioBlock value);
        void event(String type, String detail);
    }
    private final String base, token,expectedDeviceId;
    private final Listener listener;
    private final ExecutorService workers=Executors.newFixedThreadPool(3);
    private final Set<HttpURLConnection> sockets=Collections.newSetFromMap(new ConcurrentHashMap<HttpURLConnection,Boolean>());
    private volatile boolean running=true;
    private volatile boolean endpointVerified=false;
    private volatile String verifiedDeviceId="";
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
    private final AtomicReference<Double> pitchCommand=new AtomicReference<>();
    private final AtomicReference<Boolean> faceCommand=new AtomicReference<>();
    public boolean setFaceDetection(boolean enabled){return running&&endpointVerified&&!boot.isEmpty()&&faceCommand.compareAndSet(null,enabled);}
    public void setMotorEnabled(boolean enabled){motorWanted=enabled;motorCommand.set(enabled);}
    public boolean setPitchAngle(double angle){
        if(!Double.isFinite(angle)||angle<5||angle>85)throw new IllegalArgumentException("抬头角度应为 5–85°");
        if(!running||!endpointVerified||boot.isEmpty())return false;
        return pitchCommand.compareAndSet(null,Math.round(angle*10.0)/10.0);
    }

    public CollectorClient(String address,String token,Listener listener) throws Exception {
        this(address,token,"",listener);
    }
    public CollectorClient(String address,String token,String expectedDeviceId,Listener listener) throws Exception {
        base="http://"+DeviceBinding.normalizeAddress(address);
        if(token==null||!token.matches("[0-9a-fA-F]{32}"))throw new IOException("尚未绑定机器人");
        this.token=token;this.expectedDeviceId=expectedDeviceId==null?"":expectedDeviceId;this.listener=listener;
    }
    public void start(){workers.execute(this::telemetryLoop);workers.execute(this::frameLoop);workers.execute(this::audioLoop);}
    public void cancelRequests(){motorWanted=false;motorCommand.set(null);pitchCommand.set(null);faceCommand.set(null);running=false;endpointVerified=false;workers.shutdownNow();}
    public void close(){cancelRequests();for(HttpURLConnection c:sockets)c.disconnect();}
    public String boot(){return boot;}
    private void pause(long ms){try{Thread.sleep(ms);}catch(InterruptedException ignored){Thread.currentThread().interrupt();}}
    private HttpURLConnection connect(String endpoint,boolean audio) throws IOException {
        if(!endpointVerified||!running)throw new IOException("等待验证已绑定机器人");
        URL u=new URL(audio?new URL(base).getProtocol()+"://"+new URL(base).getHost()+":"+audioPort+endpoint:base+endpoint);
        HttpURLConnection c=(HttpURLConnection)u.openConnection();
        c.setConnectTimeout(3000);c.setReadTimeout(audio?5000:3000);c.setUseCaches(false);
        c.setInstanceFollowRedirects(false);c.setRequestProperty("Authorization","Bearer "+token);
        c.setRequestProperty("Connection",audio?"close":"keep-alive");sockets.add(c);
        try{int status=c.getResponseCode();if(status!=200){if(status==401||status==403)endpointVerified=false;throw new HttpStatusException(status);}return c;}
        catch(IOException e){if(!(e instanceof HttpStatusException))endpointVerified=false;release(c);throw e;}
    }
    private static final class HttpStatusException extends IOException {
        final int status;HttpStatusException(int status){super(status==401||status==403?"设备鉴权失败，请重新绑定":"HTTP "+status);this.status=status;}
    }
    private void release(HttpURLConnection c){if(c!=null){sockets.remove(c);c.disconnect();}}
    private static byte[] readBody(HttpURLConnection c,int limit)throws IOException{
        try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] buf=new byte[8192];int n;
            while((n=in.read(buf))!=-1){if(out.size()+n>limit)throw new IOException("响应超过允许大小");out.write(buf,0,n);}return out.toByteArray();
        }
    }
    public static String verifyEndpoint(String address,String token,String expectedId)throws Exception{
        String nonce=DiscoveryProof.nonce();
        HttpURLConnection c=(HttpURLConnection)new URL("http://"+DeviceBinding.normalizeAddress(address)+"/v1/discovery?nonce="+nonce).openConnection();
        try{
            c.setConnectTimeout(2000);c.setReadTimeout(2000);c.setInstanceFollowRedirects(false);c.setUseCaches(false);
            c.setRequestProperty("Connection","close"); // Deliberately no Authorization header.
            int status=c.getResponseCode();if(status!=200)throw new IOException(status==404?"机器人固件需升级后自动连接":"等待已绑定机器人回应");
            JSONObject proof=new JSONObject(new String(readBody(c,2048),"UTF-8"));
            String id=proof.optString("device_id");
            if(!DiscoveryProof.verify(token,expectedId,nonce,proof.optInt("protocol"),id,proof.optString("nonce"),proof.optString("proof")))
                throw new IOException("设备身份验证未通过，正在寻找已绑定机器人");
            return id;
        }finally{c.disconnect();}
    }
    private JSONObject json(String path)throws Exception{HttpURLConnection c=null;try{c=connect(path,false);return new JSONObject(new String(readBody(c,65536),"UTF-8"));}finally{release(c);}}
    private void sendMotorState(boolean enabled)throws Exception{
        if(!endpointVerified||!running)throw new IOException("等待验证已绑定机器人");
        HttpURLConnection c=(HttpURLConnection)new URL(base+"/v1/remote").openConnection();sockets.add(c);
        try{c.setConnectTimeout(1500);c.setReadTimeout(1500);c.setInstanceFollowRedirects(false);c.setUseCaches(false);c.setRequestMethod("POST");c.setRequestProperty("Authorization","Bearer "+token);c.setRequestProperty("Content-Type","application/json");c.setDoOutput(true);
            byte[] body=(enabled?"{\"enabled\":true}":"{\"enabled\":false}").getBytes("UTF-8");c.setFixedLengthStreamingMode(body.length);try(java.io.OutputStream out=c.getOutputStream()){out.write(body);}
            if(c.getResponseCode()!=200)throw new IOException("舵机反馈或遥控接收不可用 (HTTP "+c.getResponseCode()+")");
            readBody(c,4096);
        }finally{release(c);}
    }
    private void sendPitchAngle(double angle)throws Exception{
        if(!endpointVerified||!running)throw new IOException("等待验证已绑定机器人");
        HttpURLConnection c=(HttpURLConnection)new URL(base+"/v1/settings/pitch").openConnection();sockets.add(c);
        try{
            c.setConnectTimeout(2000);c.setReadTimeout(3000);c.setInstanceFollowRedirects(false);c.setUseCaches(false);c.setRequestMethod("POST");
            c.setRequestProperty("Authorization","Bearer "+token);c.setRequestProperty("Content-Type","application/json");c.setDoOutput(true);
            byte[] body=new JSONObject().put("angle_deg",angle).toString().getBytes("UTF-8");c.setFixedLengthStreamingMode(body.length);
            try(java.io.OutputStream out=c.getOutputStream()){out.write(body);}
            int status=c.getResponseCode();
            if(status!=202)throw new IOException(status==409?"转头已停用或设置忙，角度未保存":status==400?"角度设置无效，未保存":status==401||status==403?"设备鉴权失败，角度未保存":"保存未确认（HTTP "+status+"）");
            JSONObject result=new JSONObject(new String(readBody(c,4096),"UTF-8"));
            if(!result.optBoolean("accepted")||!result.optBoolean("persisted"))throw new IOException("机器人未确认保存角度");
            listener.event("pitch_setting_accepted",result.toString());
        }finally{release(c);}
    }
    private void sendFaceDetection(boolean enabled)throws Exception{
        if(!endpointVerified||!running)throw new IOException("等待验证已绑定机器人");
        HttpURLConnection c=(HttpURLConnection)new URL(base+"/v1/settings/face-detection").openConnection();sockets.add(c);
        try{
            c.setConnectTimeout(2000);c.setReadTimeout(3000);c.setInstanceFollowRedirects(false);c.setUseCaches(false);c.setRequestMethod("POST");
            c.setRequestProperty("Authorization","Bearer "+token);c.setRequestProperty("Content-Type","application/json");c.setDoOutput(true);
            byte[] body=new JSONObject().put("enabled",enabled).toString().getBytes("UTF-8");c.setFixedLengthStreamingMode(body.length);
            try(java.io.OutputStream out=c.getOutputStream()){out.write(body);}
            int status=c.getResponseCode();if(status!=200)throw new IOException("HTTP "+status);
            JSONObject result=new JSONObject(new String(readBody(c,4096),"UTF-8"));
            JSONObject setting=result.optJSONObject("face_detection");
            if(!result.optBoolean("accepted")||setting==null||!setting.has("enabled")||setting.optBoolean("enabled")!=enabled)throw new IOException("机器人未确认开关状态");
            listener.event("face_setting_accepted",setting.toString());
        }finally{release(c);}
    }
    private synchronized void acceptInfo(JSONObject info)throws Exception{
        if(info.optInt("protocol_version",0)!=1)throw new IOException("设备协议版本不兼容");
        if(!verifiedDeviceId.equals(info.optString("device_id")))throw new IOException("设备身份与已验证端点不一致");
        String next=info.optString("boot_id","");if(next.isEmpty())throw new IOException("设备缺少启动标识");
        int port=info.optInt("audio_port",8081);if(port<1||port>65535)throw new IOException("设备音频端口无效");audioPort=port;
        JSONObject remote=info.optJSONObject("remote");
        standaloneControl=remote!=null&&"standalone".equals(remote.optString("control_mode"));
        if(!next.equals(boot)){
            String old=boot;boot=next;telemetrySeq=-1;frameSeq=-1;imuCursor=remoteCursor=0;motorWanted=false;motorCommand.set(null);pitchCommand.set(null);faceCommand.set(null);audioEpoch++;
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
            if(!endpointVerified){String identity=verifyEndpoint(base,token,expectedDeviceId);if(!running)break;verifiedDeviceId=identity;endpointVerified=true;nextInfo=0;}
            if(System.currentTimeMillis()>=nextInfo || boot.isEmpty()){acceptInfo(json("/v1/info"));nextInfo=System.currentTimeMillis()+3000;}
            Boolean command=motorCommand.getAndSet(null);
            boolean desired=command!=null?command:motorWanted;
            // A standalone receiver belongs to the remote, not this connection.
            // Only explicit button commands change it. Older firmware still gets its lease renewals.
            if(command!=null||(!standaloneControl&&(desired!=sentMotor||(desired&&System.nanoTime()-lastMotor>750000000L)))){
                try{sendMotorState(desired);if(command!=null||sentMotor!=desired)listener.event("remote_control",desired?"转头控制已启用，等待新的遥控输入":"转头控制已关闭");sentMotor=desired;lastMotor=System.nanoTime();}
                catch(Exception e){motorWanted=false;sentMotor=false;listener.event("remote_disabled","控制请求未确认："+e.getMessage());}
            }
            Boolean faces=faceCommand.getAndSet(null);
            if(faces!=null)try{sendFaceDetection(faces);}catch(Exception e){listener.event("face_setting_failed","人脸框开关未确认："+e.getMessage());}
            Double angle=pitchCommand.getAndSet(null);
            if(angle!=null)try{sendPitchAngle(angle);}catch(Exception e){listener.event("pitch_setting_failed","角度保存或执行请求未确认："+e.getMessage());}
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
            endpointVerified=false;
            motorWanted=false;motorCommand.set(null);
            if(faceCommand.getAndSet(null)!=null)listener.event("face_setting_failed","连接中断，恢复后不会自动重发开关请求");
            if(pitchCommand.getAndSet(null)!=null)listener.event("pitch_setting_failed","连接中断，角度未提交；恢复后不会自动重发");
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
            synchronized(this){if(b.equals(boot)&&seq!=frameSeq){frameSeq=seq;listener.frameWithFaces(b,seq,timestamp,width,height,jpeg,FaceBoxes.parse(c.getHeaderField("X-Face-Detection"),seq));}}
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
