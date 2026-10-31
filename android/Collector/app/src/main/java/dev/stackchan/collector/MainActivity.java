package dev.stackchan.collector;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.text.TextUtils;
import android.view.ViewGroup;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.*;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.util.Arrays;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicReference;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

public final class MainActivity extends Activity {
    private static final int BG=0xfff3f5f2,INK=0xff233b4a,MUTED=0xff617780,ACCENT=0xff178577,RED=0xffc56d32;
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final ExecutorService files=Executors.newSingleThreadExecutor();
    private final AtomicReference<Bitmap> latestBitmap=new AtomicReference<>();
    private TextView status,device,frameMeta,audioMeta,sensorText,healthText,recordState,eventText;
    private TextView remoteState,compactState,imuMeta;
    private final MotionHistory motionHistory=new MotionHistory();
    private MotionChart accelChart,gyroChart;
    private RemoteView remoteView;
    private Button motorButton;
    private volatile boolean motorRequested=false,motorPending=false;
    private AlertDialog settingsDialog;
    private ImageView camera;
    private EditText address,token;
    private Button connect,record,mark,monitorButton;
    private LinearLayout sessionList,settings;
    private ProgressBar level;
    private volatile CollectorClient client;
    private volatile SessionRecorder recorder;
    private volatile LiveAudioMonitor monitor;
    private volatile JSONObject info;
    private volatile JSONObject latestTelemetry;
    private volatile String connectionStatus="尚未连接",lastEvent="所有采集均来自机器人。声音默认不播放。",frameDescription="等待图像",audioDescription="等待音频";
    private volatile boolean online=false;
    private volatile double rmsDb=-96;
    private volatile long videoFrames=0,audioBlocks=0,telemetrySamples=0,lastFrameWall=0,lastAudioWall=0,lastTelemetryWall=0;
    private long sessionStarted=0;
    private long lastTextRefresh=0,lastRateWall=0,lastRateFrames=0;
    private double receivedFps=0;
    private int generation=0;
    private int monitorGeneration=0;
    private File exportFolder;
    private boolean destroyed=false,finalizing=false;

    @Override public void onCreate(Bundle saved){super.onCreate(saved);getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);buildUi();
        address.setText(getPreferences(0).getString("address",""));
        if(getIntent().hasExtra("address"))address.setText(getIntent().getStringExtra("address"));
        if(getIntent().hasExtra("token"))token.setText(getIntent().getStringExtra("token"));
        // Debug intent is only a form-fill aid; connecting always requires the visible button.
        refreshSessions();ui.post(refresh);
    }
    private void buildUi(){
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(BG);
        root.setOnApplyWindowInsetsListener((v,insets)->{v.setPadding(insets.getSystemWindowInsetLeft()+dp(14),insets.getSystemWindowInsetTop()+dp(8),insets.getSystemWindowInsetRight()+dp(14),insets.getSystemWindowInsetBottom()+dp(8));return insets.consumeSystemWindowInsets();});
        LinearLayout header=row();header.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout identity=new LinearLayout(this);identity.setOrientation(LinearLayout.VERTICAL);
        TextView title=text("StackChan · 肩上采集",18,INK);title.setTypeface(Typeface.DEFAULT,Typeface.BOLD);identity.addView(title);
        device=text("同一 Wi-Fi 内观察与记录",10,MUTED);device.setSingleLine(true);device.setEllipsize(TextUtils.TruncateAt.END);identity.addView(device);
        header.addView(identity,new LinearLayout.LayoutParams(0,-2,1));
        status=text("● 尚未连接",12,MUTED);status.setMaxLines(2);status.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);header.addView(status,new LinearLayout.LayoutParams(0,-1,1));
        Button settingsButton=button("连接设置",false);LinearLayout.LayoutParams settingsLp=new LinearLayout.LayoutParams(dp(88),dp(38));settingsLp.leftMargin=dp(12);header.addView(settingsButton,settingsLp);
        root.addView(header,new LinearLayout.LayoutParams(-1,dp(42)));
        LinearLayout columns=row();LinearLayout.LayoutParams columnsLp=new LinearLayout.LayoutParams(-1,0,1);columnsLp.topMargin=dp(8);root.addView(columns,columnsLp);
        LinearLayout left=new LinearLayout(this);left.setOrientation(LinearLayout.VERTICAL);columns.addView(left,new LinearLayout.LayoutParams(0,-1,56));
        LinearLayout screen=new LinearLayout(this);screen.setOrientation(LinearLayout.VERTICAL);screen.setBackground(shape(0xff102737,10));screen.setPadding(dp(8),dp(6),dp(8),dp(6));
        left.addView(screen,new LinearLayout.LayoutParams(-1,0,1));
        camera=new ImageView(this);camera.setScaleType(ImageView.ScaleType.FIT_CENTER);camera.setContentDescription("机器人实时图像");screen.addView(camera,new LinearLayout.LayoutParams(-1,0,1));
        frameMeta=text("等待图像 · 请先连接机器人",11,0xffc0d6e3);frameMeta.setSingleLine(true);frameMeta.setEllipsize(TextUtils.TruncateAt.END);frameMeta.setPadding(dp(2),dp(4),dp(2),0);screen.addView(frameMeta);
        LinearLayout recording=row();recording.setGravity(Gravity.CENTER_VERTICAL);LinearLayout.LayoutParams recordingLp=new LinearLayout.LayoutParams(-1,dp(50));recordingLp.topMargin=dp(5);left.addView(recording,recordingLp);
        recordState=text("尚未记录",13,INK);recordState.setTypeface(Typeface.DEFAULT,Typeface.BOLD);recordState.setMaxLines(2);recording.addView(recordState,new LinearLayout.LayoutParams(0,-2,1));
        record=button("开始记录",true);mark=button("打标记",false);mark.setEnabled(false);recording.addView(record,new LinearLayout.LayoutParams(dp(100),dp(42)));LinearLayout.LayoutParams markLp=new LinearLayout.LayoutParams(dp(78),dp(42));markLp.leftMargin=dp(8);recording.addView(mark,markLp);record.setOnClickListener(v->toggleRecord());mark.setOnClickListener(v->addMarker());
        eventText=text(lastEvent,10,MUTED);eventText.setSingleLine(true);eventText.setEllipsize(TextUtils.TruncateAt.END);left.addView(eventText);
        compactState=text("电量 —   Wi-Fi —   触摸 —",11,MUTED);compactState.setPadding(0,dp(5),0,0);left.addView(compactState);
        ScrollView rightScroll=new ScrollView(this);rightScroll.setFillViewport(true);LinearLayout.LayoutParams rightLp=new LinearLayout.LayoutParams(0,-1,44);rightLp.leftMargin=dp(16);columns.addView(rightScroll,rightLp);
        LinearLayout right=new LinearLayout(this);right.setOrientation(LinearLayout.VERTICAL);right.setPadding(0,0,dp(4),dp(12));rightScroll.addView(right);
        accelChart=new MotionChart(this,motionHistory,false);right.addView(accelChart,new LinearLayout.LayoutParams(-1,dp(125)));
        gyroChart=new MotionChart(this,motionHistory,true);LinearLayout.LayoutParams gyroLp=new LinearLayout.LayoutParams(-1,dp(125));gyroLp.topMargin=dp(5);right.addView(gyroChart,gyroLp);
        imuMeta=text("最近 8 秒 · 头部传感器坐标 · 等待采样",10,MUTED);imuMeta.setPadding(0,dp(1),0,dp(7));right.addView(imuMeta);
        divider(right);
        LinearLayout remoteHeader=row();remoteHeader.setGravity(Gravity.CENTER_VERTICAL);remoteState=text("官方遥控 · 等待设备信息",12,INK);remoteState.setMaxLines(3);remoteHeader.addView(remoteState,new LinearLayout.LayoutParams(0,-2,1));
        motorButton=button("启用转头",false);remoteHeader.addView(motorButton,new LinearLayout.LayoutParams(dp(100),dp(36)));right.addView(remoteHeader,new LinearLayout.LayoutParams(-1,dp(64)));motorButton.setOnClickListener(v->toggleMotor());
        remoteView=new RemoteView(this);right.addView(remoteView,new LinearLayout.LayoutParams(-1,dp(96)));
        TextView remoteNote=text("同频道，ID 0 或 1。支持摇杆与姿态控制；位置回传异常会单独标记。",10,MUTED);remoteNote.setPadding(0,0,0,dp(7));right.addView(remoteNote);divider(right);
        LinearLayout sound=row();sound.setGravity(Gravity.CENTER_VERTICAL);sound.setPadding(0,dp(5),0,dp(4));right.addView(sound);
        sound.addView(text("音频电平",14,INK),new LinearLayout.LayoutParams(0,-2,1));monitorButton=button("声音监听：关闭",false);sound.addView(monitorButton,new LinearLayout.LayoutParams(dp(130),dp(36)));monitorButton.setOnClickListener(v->toggleMonitor());
        level=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);level.setMax(96);level.setProgressTintList(android.content.res.ColorStateList.valueOf(ACCENT));right.addView(level,new LinearLayout.LayoutParams(-1,dp(6)));
        audioMeta=text("等待音频",11,MUTED);audioMeta.setPadding(0,dp(3),0,dp(9));right.addView(audioMeta);
        healthText=text("",11,MUTED);healthText.setPadding(0,dp(2),0,dp(5));right.addView(healthText);
        TextView note=text("当前为前台采集；切换应用或锁屏会保存并停止。",11,MUTED);note.setPadding(0,dp(4),0,dp(9));right.addView(note);divider(right);
        LinearLayout history=row();history.setGravity(Gravity.CENTER_VERTICAL);TextView ht=text("观察记录",16,INK);ht.setTypeface(Typeface.DEFAULT,Typeface.BOLD);history.addView(ht,new LinearLayout.LayoutParams(0,-2,1));Button reload=button("刷新",false);history.addView(reload,new LinearLayout.LayoutParams(dp(64),dp(36)));reload.setOnClickListener(v->refreshSessions());LinearLayout.LayoutParams historyLp=new LinearLayout.LayoutParams(-1,dp(48));right.addView(history,historyLp);
        sessionList=new LinearLayout(this);sessionList.setOrientation(LinearLayout.VERTICAL);right.addView(sessionList);
        // Keep connection fields off the dashboard so all six IMU axes stay visible in landscape.
        settings=new LinearLayout(this);settings.setOrientation(LinearLayout.VERTICAL);settings.setPadding(dp(18),dp(8),dp(18),dp(8));
        address=input("机器人 IP，例如 192.168.1.20:8080",false);address.setContentDescription("机器人地址");settings.addView(address);
        token=input("32 位配对码",true);token.setContentDescription("配对码");settings.addView(token);
        TextView localNote=text("地址见机器人屏幕；配对码见本地配置。",12,MUTED);localNote.setPadding(0,dp(3),0,dp(8));settings.addView(localNote);
        connect=button("连接机器人",true);settings.addView(connect);connect.setOnClickListener(v->{toggleConnection();if(client!=null&&settingsDialog!=null)settingsDialog.dismiss();});
        settingsButton.setOnClickListener(v->showConnectionSettings());setContentView(root);
    }
    private void showConnectionSettings(){
        if(settings.getParent()!=null)((ViewGroup)settings.getParent()).removeView(settings);
        ScrollView scroll=new ScrollView(this);scroll.addView(settings);
        settingsDialog=new AlertDialog.Builder(this).setTitle("连接机器人").setView(scroll).setNegativeButton("关闭",null).create();settingsDialog.show();
    }
    private void toggleConnection(){
        if(client!=null){disconnect("用户断开连接");return;}
        try{
            final int gen=++generation;
            CollectorClient created=new CollectorClient(address.getText().toString(),token.getText().toString(),new CollectorClient.Listener(){
                private boolean active(){return gen==generation&&!destroyed;}
                public void info(JSONObject value){if(!active())return;info=value;}
                public void status(String value,boolean connected){if(!active())return;connectionStatus=value;online=connected;if(!connected&&monitor!=null)ui.post(()->{if(active())stopMonitor();});}
                public void frame(String boot,long seq,long ts,int w,int h,byte[] jpeg){if(!active())return;
                    SessionRecorder r=recorder;if(r!=null)r.frame(boot,seq,ts,w,h,jpeg);
                    Bitmap bmp=BitmapFactory.decodeByteArray(jpeg,0,jpeg.length);if(bmp==null){event("camera_decode_error","JPEG 解码失败");return;}
                    Bitmap replaced=latestBitmap.getAndSet(bmp);if(replaced!=null)replaced.recycle();
                    videoFrames++;lastFrameWall=System.currentTimeMillis();frameDescription=w+" × "+h+"   第 "+seq+" 帧";
                }
                public void telemetry(JSONObject value){if(!active())return;acceptMotion(value);latestTelemetry=value;JSONObject remote=value.optJSONObject("remote"),motor=remote==null?null:remote.optJSONObject("motor");if(!motorPending&&motor!=null)motorRequested=motor.optBoolean("requested",motor.optBoolean("enabled"));telemetrySamples++;lastTelemetryWall=System.currentTimeMillis();SessionRecorder r=recorder;if(r!=null)r.telemetry(value);}
                public void audio(String boot,AudioBlock value){if(!active())return;rmsDb=value.rmsDb();audioBlocks++;lastAudioWall=System.currentTimeMillis();
                    audioDescription=String.format(Locale.ROOT,"%.1f dBFS    %.0f kHz / %d 通道",rmsDb,value.sampleRate/1000.0,value.channels);
                    SessionRecorder r=recorder;if(r!=null)r.audio(boot,value);LiveAudioMonitor m=monitor;if(m!=null)m.offer(value);
                }
                public void event(String type,String detail){if(!active())return;lastEvent=detail;if(type.equals("remote_control")||type.equals("remote_disabled")||type.equals("device_reboot")||type.equals("connection_gap"))motorPending=false;SessionRecorder r=recorder;if(r!=null)r.event(type,detail);if(monitor!=null&&(type.equals("audio_gap")||type.equals("device_reboot")))ui.post(()->{if(active())stopMonitor();});}
            });
            client=created;motionHistory.clear();motorRequested=false;motorPending=false;connectionStatus="正在连接…";videoFrames=audioBlocks=telemetrySamples=0;lastFrameWall=lastAudioWall=lastTelemetryWall=0;lastRateWall=0;lastRateFrames=0;receivedFps=0;frameDescription="等待图像";audioDescription="等待音频";info=null;latestTelemetry=null;
            getPreferences(0).edit().putString("address",address.getText().toString().trim()).apply();created.start();connect.setText("断开连接");address.setEnabled(false);token.setEnabled(false);
            ((android.view.inputmethod.InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(address.getWindowToken(),0);
        }catch(Exception e){toast(e.getMessage());}
    }
    private void disconnect(String reason){
        stopMonitor();
        motorRequested=false;motorPending=false;
        generation++;CollectorClient old=client;client=null;if(old!=null)old.close();online=false;connectionStatus="已断开 · "+reason;lastEvent=reason;
        stopRecord(reason);connect.setText("连接机器人");address.setEnabled(true);token.setEnabled(true);
    }
    private void toggleRecord(){
        if(recorder!=null){stopRecord("用户停止记录");return;}
        if(finalizing){toast("正在封存上一条记录");return;}
        if(!online||info==null){toast("请先连接机器人，收到数据后再记录");return;}
        try{SessionRecorder next=new SessionRecorder(new File(getFilesDir(),"sessions"),new JSONObject(info.toString()),getPackageManager().getPackageInfo(getPackageName(),0).versionName,(folder,error)->ui.post(()->{
            if(destroyed)return;finalizing=false;recorder=null;record.setEnabled(true);record.setText("开始记录");mark.setEnabled(false);
            lastEvent=error==null?"记录已保存："+folder.getName():"记录写入失败："+error;toast(error==null?"记录已保存":"记录写入失败，请检查剩余空间");refreshSessions();
        }));recorder=next;sessionStarted=System.currentTimeMillis();next.event("recording_started",monitor==null?"前台观察；声音监听关闭":"前台观察；声音监听开启");record.setText("停止记录");record.setBackground(shape(RED,12));mark.setEnabled(true);lastEvent="正在保存图像、音频、传感器与事件";}
        catch(Exception e){toast("无法开始记录："+e.getMessage());}
    }
    private void stopRecord(String reason){SessionRecorder old=recorder;if(old!=null){recorder=null;finalizing=true;old.stop(reason);record.setEnabled(false);record.setText("正在保存…");record.setBackground(shape(ACCENT,12));mark.setEnabled(false);}}
    private void toggleMonitor(){
        if(monitor!=null){stopMonitor();SessionRecorder r=recorder;if(r!=null)r.event("audio_monitor_disabled","用户关闭手机声音监听");return;}
        if(!online||client==null){toast("请先连接机器人");return;}
        final int monitorId=++monitorGeneration;
        monitor=new LiveAudioMonitor(message->ui.post(()->{if(monitorId!=monitorGeneration)return;stopMonitor();lastEvent="声音监听已停止："+message;toast(lastEvent);}));
        monitorButton.setText("声音监听：开启");lastEvent="手机正在播放麦克风首通道；录制保留全部通道";SessionRecorder r=recorder;if(r!=null)r.event("audio_monitor_enabled","用户开启手机声音监听，首通道");
    }
    private void stopMonitor(){monitorGeneration++;LiveAudioMonitor old=monitor;monitor=null;if(old!=null)old.close();if(monitorButton!=null)monitorButton.setText("声音监听：关闭");}
    private void addMarker(){String[] choices={"开始步行","站立静止","身体转身","路面颠簸","遥控转头","自定义…"};new AlertDialog.Builder(this).setTitle("标记当前时刻").setItems(choices,(dialog,index)->{if(index<5){saveMarker(choices[index]);return;}EditText input=input("观察内容",false);new AlertDialog.Builder(this).setTitle("自定义标记").setView(input).setNegativeButton("取消",null).setPositiveButton("保存",(d,w)->saveMarker(input.getText().toString().trim())).show();}).show();}
    private void saveMarker(String label){SessionRecorder r=recorder;if(r!=null){r.event("marker",label.isEmpty()?"观察标记":label);lastEvent="已打标记："+label;}}
    private void toggleMotor(){CollectorClient c=client;if(c==null||!online){toast("请先连接机器人");return;}if(motorPending)return;JSONObject caps=info==null?null:info.optJSONObject("capabilities");if(caps==null||!"supported".equals(caps.optString("remote"))){toast("当前固件尚未支持官方遥控");return;}motorRequested=!motorRequested;motorPending=true;c.setMotorEnabled(motorRequested);lastEvent=motorRequested?"正在启用转头，等待机器人确认":"正在停止转头，等待机器人确认";}
    private void acceptMotion(JSONObject t){
        JSONArray samples=t.optJSONArray("imu_samples");String boot=t.optString("boot_id");
        if(samples!=null){for(int i=0;i<samples.length();i++){JSONObject s=samples.optJSONObject(i);if(s!=null)addMotion(boot,s.optLong("seq"),s);}}
        else{JSONObject imu=t.optJSONObject("imu");if(imu!=null)addMotion(boot,t.optLong("seq"),imu);}
    }
    private void addMotion(String boot,long seq,JSONObject s){JSONArray a=s.optJSONArray("accel"),g=s.optJSONArray("gyro");double[] v=new double[6];for(int i=0;i<3;i++){v[i]=a==null?Double.NaN:a.optDouble(i,Double.NaN);v[i+3]=g==null?Double.NaN:g.optDouble(i,Double.NaN);}motionHistory.add(boot,seq,s.optLong("timestamp_us"),s.optBoolean("valid"),v);}
    private final Runnable refresh=new Runnable(){public void run(){if(destroyed)return;
        Bitmap bmp=latestBitmap.getAndSet(null);if(bmp!=null)camera.setImageBitmap(bmp);
        long now=System.currentTimeMillis();
        boolean staleMotion=lastTelemetryWall==0||now-lastTelemetryWall>2000;
        accelChart.refresh(staleMotion);gyroChart.refresh(staleMotion);
        // Media still arrives at source rate; text has a readable cadence and does not flood accessibility events.
        if(lastRateWall==0){lastRateWall=now;lastRateFrames=videoFrames;}
        if(now-lastRateWall>=2000){receivedFps=(videoFrames-lastRateFrames)*1000.0/(now-lastRateWall);lastRateWall=now;lastRateFrames=videoFrames;}
        if(now-lastTextRefresh>=250){lastTextRefresh=now;
            setTextIfChanged(status,"● "+connectionStatus);status.setTextColor(online?ACCENT:MUTED);
            JSONObject di=info;if(di!=null)setTextIfChanged(device,di.optString("device_id","StackChan")+"  /  "+di.optString("firmware","采集固件"));
            setTextIfChanged(frameMeta,frameDescription+(lastFrameWall>0?String.format(Locale.ROOT,"   实收 %.1f fps",receivedFps):"")+(lastFrameWall>0&&now-lastFrameWall>2500?"  ·  画面已过期":""));
            boolean freshAudio=lastAudioWall>0&&now-lastAudioWall<2000;int progress=freshAudio?(int)(96+rmsDb):0;if(level.getProgress()!=progress)level.setProgress(progress);setTextIfChanged(audioMeta,audioDescription+(lastAudioWall>0&&!freshAudio?" · 音频中断":""));
            JSONObject t=latestTelemetry;if(t!=null){setTextIfChanged(compactState,compactTelemetry(t)+(staleMotion?" · 已过期":""));setTextIfChanged(healthText,formatHealth(t));
                JSONObject remote=t.optJSONObject("remote");remoteView.update(remote,staleMotion);
                JSONObject lastInput=remote==null?null:remote.optJSONObject("last");long inputAge=lastInput==null?0:Math.max(0,(t.optLong("timestamp_us")-lastInput.optLong("timestamp_us"))/1000000);
                setTextIfChanged(remoteState,remote==null?"官方遥控 · 固件待升级":("遥控 CH "+remote.optInt("wifi_channel")+" / ID "+remote.optInt("receiver_id")+"\n"+(!remote.optBoolean("ready")?"接收器不可用":remote.optBoolean("valid")?"已收 "+remote.optLong("rx_packets")+" 次 · 最近 "+inputAge+"s":"等待摇杆 / 姿态输入")+"\n"+(staleMotion?"转头状态已过期":motorStatus(remote.optJSONObject("motor")))));
                motorButton.setEnabled(online&&!motorPending);setTextIfChanged(motorButton,!online?"等待连接":motorPending?"设置中…":motorRequested?"停止转头":"启用转头");
                MotionHistory.Snapshot data=motionHistory.snapshot();double hz=data.size()>1?(data.size()-1)*1e6/(data.times[data.size()-1]-data.times[0]):0;
                setTextIfChanged(imuMeta,String.format(Locale.ROOT,"最近 8 秒 · 实收 %.1f Hz · 头部坐标%s",hz,t.optLong("imu_history_lost")>0?" · 存在缺样":""));
            }
            SessionRecorder r=recorder;if(r!=null){long secs=(now-sessionStarted)/1000;setTextIfChanged(recordState,String.format(Locale.ROOT,"● 记录中   %02d:%02d",secs/60,secs%60));recordState.setTextColor(RED);setTextIfChanged(eventText,lastEvent+" · 写盘丢弃 "+r.dropped()+" 项");}
            else{setTextIfChanged(recordState,finalizing?"正在封存…":"尚未记录");recordState.setTextColor(INK);setTextIfChanged(eventText,lastEvent);}
        }
        ui.postDelayed(this,50);
    }};
    private void setTextIfChanged(TextView view,String value){if(!value.contentEquals(view.getText()))view.setText(value);}
    private static String motorStatus(JSONObject motor){
        if(motor==null)return "等待转头状态";
        String state=motor.optString("state");
        if(motor.optBoolean("enabled")&&motor.has("feedback_valid")&&!motor.optBoolean("feedback_valid"))return "转头已启用 · 位置回传暂缺";
        if("feedback_paused".equals(state))return "回传异常，动作暂停 · 稳定后自动待命";
        if("feedback_invalid".equals(state))return "回传异常，转头已停用";
        if("initializing".equals(state)||"waiting_for_feedback".equals(state))return "已启用 · 等待舵机就绪";
        if("waiting_for_input".equals(state))return "已就绪 · 等待新的遥控动作";
        if("following".equals(state))return "转头控制已开启";
        if("disarmed".equals(state)||!motor.optBoolean("requested"))return "转头已关闭";
        return "正在准备转头";
    }
    private static String compactTelemetry(JSONObject t){JSONObject p=t.optJSONObject("power"),h=t.optJSONObject("health"),head=t.optJSONObject("head_touch"),touch=t.optJSONObject("touch");
        String battery=available(p,t)?numeric(p,"battery_percent",0)+"%"+(p.optBoolean("charging")?"充电":""):"—";
        String top=available(head,t)?vector(head.optJSONArray("values"),0):"—";
        return "电量 "+battery+"   Wi-Fi "+numeric(h,"wifi_rssi_dbm",0)+" dBm\n触摸 顶部 "+top+"  屏幕 "+(available(touch,t)?(touch.optInt("count")==0?"无":"有"):"—");
    }
    static String formatTelemetry(JSONObject t){
        JSONObject imu=t.optJSONObject("imu"),head=t.optJSONObject("head_touch"),touch=t.optJSONObject("touch"),power=t.optJSONObject("power");
        StringBuilder s=new StringBuilder();
        s.append("加速度 (").append(imu==null?"m/s²":imu.optString("accel_unit","m/s²")).append(")\n");
        s.append(available(imu,t)?axes(imu.optJSONArray("accel")):unavailable(imu,t));
        s.append("\n角速度 (").append(imu==null?"°/s":imu.optString("gyro_unit","°/s")).append(")\n");
        s.append(available(imu,t)?axes(imu.optJSONArray("gyro")):unavailable(imu,t));
        s.append("\n头顶触摸  ").append(available(head,t)?vector(head.optJSONArray("values"),0):unavailable(head,t));
        s.append("\n屏幕触摸  ");
        if(!available(touch,t))s.append(unavailable(touch,t));
        else if(number(touch,"count")!=null&&number(touch,"count").intValue()==0)s.append("未触摸");
        else s.append(numeric(touch,"count",0)).append(" 点 (").append(numeric(touch,"x",0)).append(", ").append(numeric(touch,"y",0)).append(")");
        s.append("\n电池  ").append(available(power,t)?numeric(power,"battery_percent",0)+"%"+(power.optBoolean("charging")?" · 充电中":""):unavailable(power,t));
        return s.toString();
    }
    private static boolean valid(JSONObject o){return o!=null&&o.optBoolean("valid");}
    private static boolean available(JSONObject group,JSONObject telemetry){
        if(!valid(group))return false;Number sampled=number(group,"timestamp_us"),current=number(telemetry,"timestamp_us");
        // Sensor groups are asynchronous: request-time touch/power may be newer than the task snapshot.
        if(sampled==null||current==null)return true;long age=current.longValue()-sampled.longValue();
        return age>=-250000&&age<=2000000;
    }
    private static String unavailable(JSONObject group,JSONObject telemetry){return valid(group)&&!available(group,telemetry)?"数据已过期":"不可用";}
    private static String formatHealth(JSONObject telemetry){
        JSONObject health=telemetry.optJSONObject("health");Number heap=number(health,"heap_free"),psram=number(health,"psram_free");
        return "可用内存 "+(heap==null?"—":heap.longValue()/1024+" KB")+"   PSRAM "+(psram==null?"—":psram.longValue()/1024+" KB");
    }
    private static Number number(JSONObject o,String key){Object n=o==null?null:o.opt(key);return n instanceof Number&&Double.isFinite(((Number)n).doubleValue())?(Number)n:null;}
    private static String numeric(JSONObject o,String key,int decimals){Number n=number(o,key);return n==null?"—":String.format(Locale.ROOT,"%."+decimals+"f",n.doubleValue());}
    private static String element(JSONArray a,int index,int decimals){Object n=a==null?null:a.opt(index);return n instanceof Number&&Double.isFinite(((Number)n).doubleValue())?String.format(Locale.ROOT,"%."+decimals+"f",((Number)n).doubleValue()):"—";}
    private static String axes(JSONArray a){return "X "+element(a,0,2)+"     Y "+element(a,1,2)+"     Z "+element(a,2,2);}
    private static String vector(JSONArray a,int decimals){if(a==null)return"—";StringBuilder s=new StringBuilder();for(int i=0;i<a.length();i++){if(i>0)s.append(" / ");s.append(element(a,i,decimals));}return s.toString();}
    private void refreshSessions(){files.execute(()->{
        File root=new File(getFilesDir(),"sessions");File[] folders=root.listFiles(File::isDirectory);if(folders==null)folders=new File[0];Arrays.sort(folders,(a,b)->b.getName().compareTo(a.getName()));
        final File[] list=folders;final String[] details=new String[Math.min(50,list.length)];
        for(int i=0;i<details.length;i++){details[i]="未完成封存";try(BufferedReader reader=new BufferedReader(new InputStreamReader(new FileInputStream(new File(list[i],"manifest.json")),"UTF-8"))){StringBuilder raw=new StringBuilder();String line;while((line=reader.readLine())!=null&&raw.length()<65536)raw.append(line);JSONObject m=new JSONObject(raw.toString());details[i]="complete".equals(m.optString("status"))?m.optLong("frames")+" 帧 / "+m.optLong("audio_blocks")+" 音频块":("error".equals(m.optString("status"))?"写入错误 · 可导出已有数据":"未完成封存 · 可导出已有数据");}catch(Exception ignored){}}
        ui.post(()->{if(destroyed)return;sessionList.removeAllViews();if(list.length==0){sessionList.addView(text("还没有记录。连接后，点击「开始记录」。",14,MUTED));return;}
            for(int i=0;i<Math.min(50,list.length);i++){File folder=list[i];LinearLayout row=row();row.setPadding(0,dp(4),0,dp(8));row.setGravity(Gravity.CENTER_VERTICAL);TextView name=text(folder.getName()+"\n"+details[i],12,INK);row.addView(name,new LinearLayout.LayoutParams(0,-2,1));Button open=button("回放",false),export=button("导出",false);row.addView(open);row.addView(export);sessionList.addView(row);
                open.setOnClickListener(v->{if(recorder!=null||finalizing){toast("请先停止并保存记录");return;}new SessionPlayer(this,folder).show();});
                export.setOnClickListener(v->{if(recorder!=null||finalizing){toast("请先停止并保存记录");return;}exportFolder=folder;Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT);intent.addCategory(Intent.CATEGORY_OPENABLE);intent.setType("application/zip");intent.putExtra(Intent.EXTRA_TITLE,"StackChan-"+folder.getName()+".zip");startActivityForResult(intent,20);});
            }
        });
    });}
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(request==20&&result==RESULT_OK&&data!=null&&data.getData()!=null&&exportFolder!=null){Uri dest=data.getData();File source=exportFolder;toast("正在导出…");files.execute(()->{try(OutputStream raw=getContentResolver().openOutputStream(dest);ZipOutputStream out=new ZipOutputStream(raw)){zipDirectory(source,source,out);ui.post(()->toast("导出完成"));}catch(Exception e){ui.post(()->toast("导出失败："+e.getMessage()));}});}}
    private void zipDirectory(File root,File node,ZipOutputStream out)throws Exception{File[] list=node.listFiles();if(list==null)throw new IOException("无法读取记录目录");for(File f:list){if(f.isDirectory()){zipDirectory(root,f,out);continue;}out.putNextEntry(new ZipEntry(root.toURI().relativize(f.toURI()).getPath()));try(InputStream in=new FileInputStream(f)){byte[] buf=new byte[65536];int n;while((n=in.read(buf))!=-1)out.write(buf,0,n);}out.closeEntry();}}
    @Override protected void onStop(){super.onStop();if(client!=null||recorder!=null)disconnect("应用离开前台，已停止采集");}
    @Override protected void onDestroy(){destroyed=true;disconnect("观察台已关闭");ui.removeCallbacks(refresh);files.shutdown();super.onDestroy();}
    private EditText input(String hint,boolean password){EditText e=new EditText(this);e.setTextSize(15);e.setTextColor(INK);e.setHint(hint);e.setSingleLine(true);e.setInputType(password?InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);e.setPadding(dp(12),dp(10),dp(12),dp(10));LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,dp(52));lp.bottomMargin=dp(7);e.setLayoutParams(lp);e.setBackground(shape(Color.WHITE,10));return e;}
    private TextView text(String s,int size,int color){TextView t=new TextView(this);t.setText(s);t.setTextSize(size);t.setTextColor(color);return t;}
    private Button button(String s,boolean primary){Button b=new Button(this);b.setText(s);b.setAllCaps(false);b.setTextSize(13);b.setTextColor(primary?Color.WHITE:ACCENT);b.setMinWidth(dp(64));b.setMinimumWidth(dp(64));b.setMinHeight(dp(36));b.setMinimumHeight(dp(36));b.setPadding(dp(10),dp(4),dp(10),dp(4));b.setBackground(shape(primary?ACCENT:0xffdce9ee,12));return b;}
    private LinearLayout row(){LinearLayout l=new LinearLayout(this);l.setOrientation(LinearLayout.HORIZONTAL);return l;}
    private void divider(LinearLayout parent){View v=new View(this);v.setBackgroundColor(0xffcfdee7);parent.addView(v,new LinearLayout.LayoutParams(-1,dp(1)));}
    private GradientDrawable shape(int color,int radius){GradientDrawable d=new GradientDrawable();d.setColor(color);d.setCornerRadius(dp(radius));return d;}
    private int dp(int px){return (int)(px*getResources().getDisplayMetrics().density+.5f);}
    private void toast(String s){Toast.makeText(this,s,Toast.LENGTH_SHORT).show();}
}
