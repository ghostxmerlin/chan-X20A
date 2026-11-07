package dev.stackchan.collector;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
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
    private static final int BG=HudTheme.BG,INK=HudTheme.TEXT,MUTED=HudTheme.MUTED,ACCENT=HudTheme.ACCENT;
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final ExecutorService files=Executors.newSingleThreadExecutor();
    private final ExecutorService network=Executors.newSingleThreadExecutor();
    private static final class PreviewFrame {
        final Bitmap bitmap;final FaceBoxes faces;final long received;final String boot;
        PreviewFrame(Bitmap bitmap,FaceBoxes faces,String boot){this.bitmap=bitmap;this.faces=faces;this.boot=boot;received=SystemClock.elapsedRealtime();}
    }
    private final AtomicReference<PreviewFrame> latestBitmap=new AtomicReference<>();
    private TextView status,device,frameMeta,audioMeta,sensorText,healthText,recordState,recordDetailState,eventText,connectionDetail;
    private TextView remoteState,compactState,imuMeta,environmentState;
    private final MotionHistory motionHistory=new MotionHistory();
    private final AudioWaveformHistory audioHistory=new AudioWaveformHistory();
    private AudioLevelView audioLevel;
    private MagneticFieldView magneticField;
    private CombinedMotionChart motionChart;
    private RemoteView remoteView;
    private PitchSettingView pitchView;
    private DeviceBinding binding=new DeviceBinding("","","");
    private RobotDiscovery discovery;
    private TextView bindingState;
    private String activeAddress="",probingAddress="";
    private boolean foreground=false,userPaused=false;
    private long lastDiscovery=0,routeProbeSerial=0;
    private Button motorButton;
    private volatile boolean motorRequested=false,motorPending=false;
    private AlertDialog settingsDialog,recordsDialog,menuDialog;
    private FaceCameraView camera;
    private Button faceButton;
    private boolean faceEnabled=false,facePending=false;
    private long faceEpoch=-1;
    private String faceError="none";
    private Button connect,record,mark,monitorButton;
    private LinearLayout sessionList,settings,records,menu;
    private volatile CollectorClient client;
    private volatile SessionRecorder recorder;
    private volatile LiveAudioMonitor monitor;
    private volatile JSONObject info;
    private static final class TelemetryReceipt {
        final JSONObject value;final long elapsedMs;
        TelemetryReceipt(JSONObject value,long elapsedMs){this.value=value;this.elapsedMs=elapsedMs;}
    }
    private volatile TelemetryReceipt latestTelemetry;
    private volatile String connectionStatus="尚未连接",lastEvent="所有采集均来自机器人。声音默认不播放。",frameDescription="等待图像",audioDescription="等待音频";
    private volatile boolean online=false;
    private volatile double rmsDb=-96;
    private volatile long videoFrames=0,audioBlocks=0,telemetrySamples=0,lastFrameWall=0,lastAudioElapsed=0,lastTelemetryWall=0;
    private long sessionStarted=0;
    private long lastTextRefresh=0,lastRateWall=0,lastRateFrames=0;
    private double receivedFps=0;
    private volatile int generation=0;
    private int monitorGeneration=0;
    private File exportFolder;
    private volatile boolean destroyed=false;
    private boolean finalizing=false;

    @Override public void onCreate(Bundle saved){super.onCreate(saved);getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        loadBinding();buildUi();discovery=new RobotDiscovery(ui,this::discovered);ui.post(refresh);
    }
    private void loadBinding(){binding=new DeviceBinding(getPreferences(0).getString("address",""),getPreferences(0).getString("token",""),getPreferences(0).getString("device_id",""));}
    private void saveBinding(DeviceBinding next){
        if(binding.address.equals(next.address)&&binding.token.equals(next.token)&&binding.deviceId.equals(next.deviceId))return;
        binding=next;getPreferences(0).edit().putString("address",next.address).putString("token",next.token).putString("device_id",next.deviceId).apply();updateBindingState();
    }
    @Override protected void onStart(){super.onStart();foreground=true;userPaused=false;loadBinding();ensureConnection();}
    private void ensureConnection(){
        if(!foreground||userPaused||destroyed||client!=null)return;
        if(binding.hasCredentials())connectTo(binding,false);
        else{connectionStatus="尚未绑定机器人";updateBindingState();}
    }
    private void discovered(String id,String candidate){
        if(!foreground||userPaused||online||!binding.matches(id)||candidate.equals(activeAddress)||candidate.equals(probingAddress))return;
        DeviceBinding expected=binding;probingAddress=candidate;final long probe=++routeProbeSerial;
        network.execute(()->{try{
            CollectorClient.verifyEndpoint(candidate,expected.token,expected.deviceId);
            ui.post(()->{if(probe!=routeProbeSerial)return;probingAddress="";
                if(foreground&&!userPaused&&!online&&binding.matches(expected.deviceId))connectTo(binding.at(candidate),true);
            });
        }catch(Exception ignored){ui.post(()->{if(probe==routeProbeSerial)probingAddress="";});}});
    }
    private void updateBindingState(){
        if(bindingState==null)return;
        bindingState.setText(binding.hasCredentials()?("已绑定 "+(binding.deviceId.isEmpty()?"机器人":binding.deviceId)+"\n"+binding.address+"\n打开或返回观察台后自动连接，地址变化时会重新寻找。"):
            "此安装尚未绑定机器人。请先在开发电脑完成一次本地绑定，之后打开观察台即可连接。");
        connect.setText(client==null?"重新连接":"暂停连接");connect.setEnabled(binding.hasCredentials());
    }
    private void buildUi(){
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN);
        getWindow().setStatusBarColor(BG);getWindow().setNavigationBarColor(BG);
        getWindow().getDecorView().setSystemUiVisibility(0);
        LinearLayout root=column();root.setBackgroundColor(BG);root.setPadding(dp(12),dp(8),dp(12),dp(8));
        root.setOnApplyWindowInsetsListener((v,insets)->{v.setPadding(insets.getSystemWindowInsetLeft()+dp(12),insets.getSystemWindowInsetTop()+dp(8),insets.getSystemWindowInsetRight()+dp(12),insets.getSystemWindowInsetBottom()+dp(8));return insets.consumeSystemWindowInsets();});
        LinearLayout columns=row();LinearLayout.LayoutParams columnsLp=new LinearLayout.LayoutParams(-1,0,1);root.addView(columns,columnsLp);
        audioLevel=new AudioLevelView(this,audioHistory);audioLevel.setTag("home_audio");
        LinearLayout.LayoutParams audioLp=new LinearLayout.LayoutParams(dp(56),-1);audioLp.rightMargin=dp(8);columns.addView(audioLevel,audioLp);
        LinearLayout picture=column();columns.addView(picture,new LinearLayout.LayoutParams(0,-1,1));
        camera=new FaceCameraView(this);camera.setTag("home_camera");camera.setBackgroundColor(BG);camera.setScaleType(ImageView.ScaleType.FIT_CENTER);camera.setContentDescription("机器人实时图像，完整画面");
        picture.addView(camera,new LinearLayout.LayoutParams(-1,0,1));
        frameMeta=text("等待机器人图像",12,MUTED);frameMeta.setSingleLine(true);frameMeta.setEllipsize(TextUtils.TruncateAt.END);frameMeta.setPadding(0,dp(4),0,0);picture.addView(frameMeta);
        LinearLayout signals=column();LinearLayout.LayoutParams signalsLp=new LinearLayout.LayoutParams(dp(236),-1);signalsLp.leftMargin=dp(12);columns.addView(signals,signalsLp);
        motionChart=new CombinedMotionChart(this,motionHistory);motionChart.setTag("home_motion");signals.addView(motionChart,new LinearLayout.LayoutParams(-1,0,1));
        magneticField=new MagneticFieldView(this);magneticField.setTag("home_magnetic");
        LinearLayout.LayoutParams magneticLp=new LinearLayout.LayoutParams(-1,0,1);magneticLp.topMargin=dp(8);signals.addView(magneticField,magneticLp);
        compactState=text("电量 —  Wi-Fi —\n遥控 —  转头 —  屏 —\n仰角 设— / 实—",16,INK);compactState.setTypeface(HudTheme.MEDIUM);compactState.setMaxLines(3);compactState.setEllipsize(TextUtils.TruncateAt.END);
        environmentState=text(compactEnvironment(null,false),16,INK);environmentState.setTypeface(HudTheme.MEDIUM);environmentState.setTag("home_environment");environmentState.setMaxLines(2);environmentState.setEllipsize(TextUtils.TruncateAt.END);
        pitchView=new PitchSettingView(this,degrees->{
            CollectorClient active=client;if(active==null||!online)return false;
            if(!active.setPitchAngle(degrees)){toast("正在处理上一次角度设置");return false;}
            SessionRecorder r=recorder;if(r!=null)r.event("pitch_setting_requested",String.format(Locale.ROOT,"用户设置抬头角度 %.1f°",degrees));
            lastEvent="正在保存抬头角度，执行状态将单独显示";return true;
        });pitchView.setTag("home_pitch");
        monitorButton=button("监听：关",false);monitorButton.setTag("home_monitor");monitorButton.setOnClickListener(v->toggleMonitor());
        faceButton=button("人脸框：关",false);faceButton.setTag("home_face");faceButton.setEnabled(false);faceButton.setOnClickListener(v->toggleFaces());
        LinearLayout homeToggles=row();homeToggles.addView(monitorButton,new LinearLayout.LayoutParams(0,dp(44),1));
        LinearLayout.LayoutParams faceLp=new LinearLayout.LayoutParams(0,dp(44),1);faceLp.leftMargin=dp(8);homeToggles.addView(faceButton,faceLp);
        LinearLayout sessionState=column();sessionState.setGravity(Gravity.CENTER_VERTICAL|Gravity.END);
        status=text("● 等待连接",14,MUTED);status.setSingleLine(true);status.setGravity(Gravity.END);sessionState.addView(status,new LinearLayout.LayoutParams(-1,-2));
        recordState=text("未记录",14,MUTED);recordState.setSingleLine(true);recordState.setGravity(Gravity.END);sessionState.addView(recordState,new LinearLayout.LayoutParams(-1,-2));
        Button overflow=button("⋮",false);overflow.setTag("home_menu");overflow.setContentDescription("更多：记录和机器人设置");overflow.setTextSize(24);
        HomeParameterBar parameters=new HomeParameterBar(this,compactState,pitchView,homeToggles,environmentState,sessionState,overflow);parameters.setTag("home_parameters");
        LinearLayout.LayoutParams parametersLp=new LinearLayout.LayoutParams(-1,-2);parametersLp.topMargin=dp(7);root.addView(parameters,parametersLp);
        buildSecondaryPanels();overflow.setOnClickListener(v->showMenu());setContentView(root);updateBindingState();
    }
    private void buildSecondaryPanels(){
        records=column();records.setPadding(dp(14),dp(8),dp(14),dp(12));
        LinearLayout recording=row();recording.setGravity(Gravity.CENTER_VERTICAL);
        recordDetailState=text("尚未记录",13,INK);recording.addView(recordDetailState,new LinearLayout.LayoutParams(0,-2,1));
        record=button("开始记录",true);record.setTag("record_toggle");mark=button("打标记",false);mark.setTag("record_marker");mark.setEnabled(false);
        recording.addView(record,new LinearLayout.LayoutParams(dp(100),dp(40)));LinearLayout.LayoutParams markLp=new LinearLayout.LayoutParams(dp(82),dp(40));markLp.leftMargin=dp(8);recording.addView(mark,markLp);records.addView(recording);
        record.setOnClickListener(v->toggleRecord());mark.setOnClickListener(v->addMarker());
        eventText=text(lastEvent,11,MUTED);eventText.setPadding(0,dp(10),0,dp(10));records.addView(eventText);
        TextView recordingNote=text("保持观察台在前台。切换应用或锁屏会保存并停止采集。",11,MUTED);recordingNote.setPadding(0,0,0,dp(12));records.addView(recordingNote);divider(records);
        LinearLayout historyHeader=row();historyHeader.setGravity(Gravity.CENTER_VERTICAL);TextView historyTitle=text("历史记录",14,INK);historyTitle.setTypeface(HudTheme.MEDIUM);historyHeader.addView(historyTitle,new LinearLayout.LayoutParams(0,-2,1));
        Button reload=button("刷新",false);historyHeader.addView(reload,new LinearLayout.LayoutParams(dp(64),dp(36)));reload.setOnClickListener(v->refreshSessions());records.addView(historyHeader,new LinearLayout.LayoutParams(-1,dp(46)));
        sessionList=column();sessionList.setTag("record_history");records.addView(sessionList);

        settings=column();settings.setPadding(dp(14),dp(8),dp(14),dp(12));
        device=text("等待设备信息",12,INK);device.setPadding(0,0,0,dp(5));settings.addView(device);
        bindingState=text("",12,MUTED);bindingState.setPadding(0,0,0,dp(8));settings.addView(bindingState);
        connectionDetail=text(connectionStatus,12,INK);connectionDetail.setPadding(0,0,0,dp(10));settings.addView(connectionDetail);
        LinearLayout connectionButtons=row();connect=button("重新连接",false);connectionButtons.addView(connect,new LinearLayout.LayoutParams(0,dp(40),1));connect.setOnClickListener(v->toggleConnection());
        Button search=button("重新寻找机器人",false);LinearLayout.LayoutParams searchLp=new LinearLayout.LayoutParams(0,dp(40),1);searchLp.leftMargin=dp(8);connectionButtons.addView(search,searchLp);
        search.setOnClickListener(v->{userPaused=false;ensureConnection();if(!online)discovery.scan(binding);});settings.addView(connectionButtons);spacer(settings,12);divider(settings);
        LinearLayout remoteHeader=row();remoteHeader.setGravity(Gravity.CENTER_VERTICAL);remoteState=text("官方遥控 · 等待设备信息",12,INK);remoteState.setMaxLines(3);remoteHeader.addView(remoteState,new LinearLayout.LayoutParams(0,-2,1));
        motorButton=button("启用转头",false);motorButton.setEnabled(false);remoteHeader.addView(motorButton,new LinearLayout.LayoutParams(dp(100),dp(40)));settings.addView(remoteHeader,new LinearLayout.LayoutParams(-1,dp(76)));motorButton.setOnClickListener(v->toggleMotor());
        remoteView=new RemoteView(this);settings.addView(remoteView,new LinearLayout.LayoutParams(-1,dp(96)));

        TextView remoteNote=text("同频道，ID 0 或 1。仅左右转头；位置回传异常单独标记。",11,MUTED);remoteNote.setPadding(0,dp(6),0,dp(12));settings.addView(remoteNote);divider(settings);
        TextView diagnostics=text("采集诊断",14,INK);diagnostics.setTypeface(HudTheme.MEDIUM);diagnostics.setPadding(0,dp(12),0,dp(8));settings.addView(diagnostics);
        imuMeta=text("等待传感器采样",12,MUTED);imuMeta.setPadding(0,0,0,dp(8));settings.addView(imuMeta);
        audioMeta=text("等待音频",12,MUTED);settings.addView(audioMeta);
        sensorText=text("等待传感器数据",12,MUTED);sensorText.setPadding(0,dp(8),0,dp(8));settings.addView(sensorText);
        healthText=text("",12,MUTED);settings.addView(healthText);

        menu=column();menu.setPadding(dp(12),dp(4),dp(12),dp(12));
        Button recordsButton=button("记录",false);recordsButton.setTag("menu_records");menu.addView(recordsButton,new LinearLayout.LayoutParams(-1,dp(44)));recordsButton.setOnClickListener(v->{menuDialog.dismiss();showRecords();});
        spacer(menu,8);Button robotButton=button("机器人设置",false);robotButton.setTag("menu_robot");menu.addView(robotButton,new LinearLayout.LayoutParams(-1,dp(44)));robotButton.setOnClickListener(v->{menuDialog.dismiss();showConnectionSettings();});

    }
    private void showMenu(){menuDialog=showPanel("观察选项",menu,340,false);}
    private void showRecords(){refreshSessions();recordsDialog=showPanel("记录",records,680,true);}
    private void showConnectionSettings(){updateBindingState();settingsDialog=showPanel("机器人设置",settings,680,true);}
    /** Owned dialog windows keep the Activity and current acquisition session in the foreground. */
    private AlertDialog showPanel(String title,View content,int widthDp,boolean expanded){
        if(content.getParent()!=null)((ViewGroup)content.getParent()).removeView(content);
        LinearLayout shell=column();shell.setBackgroundColor(BG);shell.setPadding(dp(1),dp(1),dp(1),dp(1));
        LinearLayout heading=row();heading.setPadding(dp(14),dp(5),dp(8),dp(5));heading.setGravity(Gravity.CENTER_VERTICAL);
        TextView label=text(title,16,INK);label.setTypeface(HudTheme.MEDIUM);heading.addView(label,new LinearLayout.LayoutParams(0,-2,1));Button close=button("关闭",false);heading.addView(close,new LinearLayout.LayoutParams(dp(64),dp(36)));shell.addView(heading);divider(shell);
        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(false);scroll.setBackgroundColor(BG);scroll.addView(content);shell.addView(scroll,new LinearLayout.LayoutParams(-1,expanded?0:-2,expanded?1:0));
        AlertDialog dialog=new AlertDialog.Builder(this).create();dialog.setView(shell,0,0,0,0);close.setOnClickListener(v->dialog.dismiss());dialog.show();HudTheme.applyDialog(dialog);
        android.graphics.Rect bounds=new android.graphics.Rect();getWindow().getDecorView().getWindowVisibleDisplayFrame(bounds);
        int width=bounds.width()>0?bounds.width():getResources().getDisplayMetrics().widthPixels;
        int height=bounds.height()>0?bounds.height():getResources().getDisplayMetrics().heightPixels;
        dialog.getWindow().setLayout(Math.min(dp(widthDp),width-dp(24)),expanded?Math.min(dp(540),height-dp(24)):-2);
        return dialog;
    }
    private void toggleConnection(){
        if(client!=null){userPaused=true;discovery.stop();routeProbeSerial++;disconnect("本次前台连接已暂停");return;}
        userPaused=false;ensureConnection();
    }
    private void connectTo(DeviceBinding destination,boolean migrating){
        try{
            final int gen=++generation;
            CollectorClient created=new CollectorClient(destination.address,destination.token,destination.deviceId,new CollectorClient.Listener(){
                private boolean active(){return gen==generation&&!destroyed;}
                public void info(JSONObject value){if(!active())return;String oldBoot=info==null?"":info.optString("boot_id");
                    if(!oldBoot.equals(value.optString("boot_id"))){latestTelemetry=null;lastTelemetryWall=0;audioHistory.clear();}info=value;
                    ui.post(()->{if(!active())return;saveBinding(new DeviceBinding(destination.address,destination.token,value.optString("device_id")));if(!oldBoot.equals(value.optString("boot_id"))){pitchView.reset();resetFaces();}});}
                public void status(String value,boolean connected){if(!active())return;connectionStatus=value;online=connected;if(!connected&&monitor!=null)ui.post(()->{if(active())stopMonitor();});}
                public void frame(String boot,long seq,long ts,int w,int h,byte[] jpeg){frameWithFaces(boot,seq,ts,w,h,jpeg,FaceBoxes.NONE);}
                public void frameWithFaces(String boot,long seq,long ts,int w,int h,byte[] jpeg,FaceBoxes faces){if(!active())return;
                    SessionRecorder r=recorder;if(r!=null)r.frame(boot,seq,ts,w,h,jpeg);
                    Bitmap bmp=BitmapFactory.decodeByteArray(jpeg,0,jpeg.length);if(bmp==null){event("camera_decode_error","JPEG 解码失败");return;}
                    PreviewFrame replaced=latestBitmap.getAndSet(new PreviewFrame(bmp,faces,boot));if(replaced!=null)replaced.bitmap.recycle();
                    if(r!=null&&faces.epoch>=0)r.event("face_detection",faces.json(boot,ts));
                    videoFrames++;lastFrameWall=System.currentTimeMillis();frameDescription=w+" × "+h;
                }
                public void telemetry(JSONObject value){if(!active())return;long received=SystemClock.elapsedRealtime();acceptMotion(value);latestTelemetry=new TelemetryReceipt(value,received);JSONObject remote=value.optJSONObject("remote"),motor=remote==null?null:remote.optJSONObject("motor");if(!motorPending&&motor!=null)motorRequested=motor.optBoolean("requested",motor.optBoolean("enabled"));telemetrySamples++;lastTelemetryWall=System.currentTimeMillis();SessionRecorder r=recorder;if(r!=null)r.telemetry(value);}
                public void audio(String boot,AudioBlock value){if(!active())return;rmsDb=value.rmsDb();audioBlocks++;lastAudioElapsed=SystemClock.elapsedRealtime();audioHistory.add(boot,value,lastAudioElapsed);
                    audioDescription=String.format(Locale.ROOT,"%.1f dBFS    %.0f kHz / %d 通道",rmsDb,value.sampleRate/1000.0,value.channels);
                    SessionRecorder r=recorder;if(r!=null)r.audio(boot,value);LiveAudioMonitor m=monitor;if(m!=null)m.offer(value);
                }
                public void event(String type,String detail){if(!active())return;
                    if(type.equals("face_setting_accepted")){ui.post(()->{if(!active())return;facePending=false;try{JSONObject value=new JSONObject(detail);faceEpoch=value.getLong("epoch");faceEnabled=value.getBoolean("enabled");}catch(Exception ignored){}});SessionRecorder r=recorder;if(r!=null)r.event(type,detail);return;}
                    if(type.equals("face_setting_failed")){ui.post(()->{if(active()){facePending=false;toast(detail);}});}
                    if(type.equals("pitch_setting_accepted")){lastEvent="抬头角度已保存，执行状态见首页仰角";try{JSONObject result=new JSONObject(detail);ui.post(()->{if(active())pitchView.accepted(result);});}catch(Exception ignored){}}
                    else{lastEvent=detail;if(type.equals("pitch_setting_failed"))ui.post(()->{if(active())pitchView.failed(detail);});}if(type.equals("remote_control")||type.equals("remote_disabled")||type.equals("device_reboot")||type.equals("connection_gap"))motorPending=false;SessionRecorder r=recorder;if(r!=null)r.event(type,detail);if(monitor!=null&&(type.equals("audio_gap")||type.equals("device_reboot")))ui.post(()->{if(active())stopMonitor();});}
            });
            CollectorClient old=client;client=created;if(old!=null){old.cancelRequests();network.execute(old::close);}
            activeAddress=DeviceBinding.normalizeAddress(destination.address);online=false;motorRequested=false;motorPending=false;resetFaces();
            if(!migrating){motionHistory.clear();audioHistory.clear();pitchView.reset();videoFrames=audioBlocks=telemetrySamples=0;lastRateWall=0;lastRateFrames=0;receivedFps=0;}
            else{pitchView.connectionLost("地址变化，本次未确认的角度设置不会自动重发");SessionRecorder r=recorder;if(r!=null)r.event("connection_route_changed","已验证同一机器人，连接地址更新；记录继续");}
            connectionStatus="正在验证并连接机器人…";lastFrameWall=lastTelemetryWall=0;lastAudioElapsed=0;frameDescription="等待图像";audioDescription="等待音频";info=null;latestTelemetry=null;
            created.start();updateBindingState();
        }catch(Exception e){connectionStatus=e.getMessage();updateBindingState();}
    }
    private void disconnect(String reason){
        stopMonitor();resetFaces();motorRequested=false;motorPending=false;pitchView.connectionLost("连接已停止，未确认的角度设置不会自动重发");
        generation++;CollectorClient old=client;client=null;if(old!=null){old.cancelRequests();network.execute(old::close);}online=false;connectionStatus="已断开 · "+reason;lastEvent=reason;
        stopRecord(reason);updateBindingState();
    }
    private void toggleRecord(){
        if(recorder!=null){stopRecord("用户停止记录");return;}
        if(finalizing){toast("正在封存上一条记录");return;}
        if(!online||info==null){toast("请先连接机器人，收到数据后再记录");return;}
        try{SessionRecorder next=new SessionRecorder(new File(getFilesDir(),"sessions"),new JSONObject(info.toString()),getPackageManager().getPackageInfo(getPackageName(),0).versionName,(folder,error)->ui.post(()->{
            if(destroyed)return;finalizing=false;recorder=null;record.setEnabled(true);record.setText("开始记录");mark.setEnabled(false);
            lastEvent=error==null?"记录已保存："+folder.getName():"记录写入失败："+error;toast(error==null?"记录已保存":"记录写入失败，请检查剩余空间");refreshSessions();
        }));recorder=next;sessionStarted=System.currentTimeMillis();next.event("recording_started",monitor==null?"前台观察；声音监听关闭":"前台观察；声音监听开启");record.setText("停止记录");mark.setEnabled(true);lastEvent="正在保存图像、音频、传感器与事件";}
        catch(Exception e){toast("无法开始记录："+e.getMessage());}
    }
    private void stopRecord(String reason){SessionRecorder old=recorder;if(old!=null){recorder=null;finalizing=true;old.stop(reason);record.setEnabled(false);record.setText("正在保存…");mark.setEnabled(false);}}
    private void resetFaces(){facePending=false;faceEnabled=false;faceEpoch=-1;faceError="none";if(camera!=null)camera.detection(false,-1);}
    private void toggleFaces(){
        CollectorClient active=client;if(active==null||!online||facePending)return;
        if(!active.setFaceDetection(!faceEnabled)){toast("人脸框开关暂不可用");return;}
        facePending=true;camera.detection(false,-1);faceButton.setEnabled(false);faceButton.setText("设置中…");
        SessionRecorder r=recorder;if(r!=null)r.event("face_setting_requested",faceEnabled?"关闭机器人人脸检测":"开启机器人人脸检测");
    }
    private void updateFaces(JSONObject telemetry,boolean stale){
        JSONObject state=telemetry==null?null:telemetry.optJSONObject("face_detection");
        boolean supported=state!=null&&state.optBoolean("supported");
        if(!online){facePending=false;camera.detection(false,-1);}
        if(supported&&!stale&&!facePending&&state.optLong("epoch",-1)>=faceEpoch){
            faceEpoch=state.optLong("epoch",-1);faceEnabled=state.optBoolean("enabled");
            String error=state.optString("error","none");
            if(!"none".equals(error)&&!error.equals(faceError))toast("人脸检测已停止："+error);
            faceError=error;
        }
        camera.detection(online&&!stale&&faceEnabled&&!facePending,faceEpoch);
        faceButton.setEnabled(online&&!stale&&supported&&!facePending);
        setTextIfChanged(faceButton,facePending?"设置中…":!supported?"人脸框：—":faceEnabled?"人脸框：开":"人脸框：关");
        faceButton.setContentDescription(!supported?"机器人未提供人脸检测":facePending?"正在设置人脸检测":faceEnabled?"人脸检测开启；点击关闭":"人脸检测关闭；点击开启");
    }
    private void toggleMonitor(){
        if(monitor!=null){stopMonitor();SessionRecorder r=recorder;if(r!=null)r.event("audio_monitor_disabled","用户关闭手机声音监听");return;}
        if(!online||client==null){toast("请先连接机器人");return;}
        final int monitorId=++monitorGeneration;
        monitor=new LiveAudioMonitor(message->ui.post(()->{if(monitorId!=monitorGeneration)return;stopMonitor();lastEvent="声音监听已停止："+message;toast(lastEvent);}));
        monitorButton.setText("监听：开");lastEvent="手机正在播放麦克风首通道；录制保留全部通道";SessionRecorder r=recorder;if(r!=null)r.event("audio_monitor_enabled","用户开启手机声音监听，首通道");
    }
    private void stopMonitor(){monitorGeneration++;LiveAudioMonitor old=monitor;monitor=null;if(old!=null)old.close();if(monitorButton!=null)monitorButton.setText("监听：关");}
    private void addMarker(){
        String[] choices={"开始步行","站立静止","身体转身","路面颠簸","遥控转头","自定义…"};
        LinearLayout options=column();options.setPadding(dp(12),dp(8),dp(12),dp(12));final AlertDialog[] markerDialog=new AlertDialog[1];
        for(int i=0;i<choices.length;i++){final int choice=i;Button item=button(choices[i],false);options.addView(item,new LinearLayout.LayoutParams(-1,dp(40)));if(i<choices.length-1)spacer(options,6);
            item.setOnClickListener(v->{markerDialog[0].dismiss();if(choice<5)saveMarker(choices[choice]);else showCustomMarker();});}
        markerDialog[0]=showPanel("标记当前时刻",options,360,true);
    }
    private void showCustomMarker(){
        LinearLayout form=column();form.setPadding(dp(12),dp(10),dp(12),dp(12));EditText input=input("观察内容",false);form.addView(input);
        Button save=button("保存标记",true);form.addView(save,new LinearLayout.LayoutParams(-1,dp(40)));AlertDialog dialog=showPanel("自定义标记",form,400,false);
        save.setOnClickListener(v->{saveMarker(input.getText().toString().trim());dialog.dismiss();});
    }
    private void saveMarker(String label){SessionRecorder r=recorder;if(r!=null){r.event("marker",label.isEmpty()?"观察标记":label);lastEvent="已打标记："+label;}}
    private void toggleMotor(){CollectorClient c=client;if(c==null||!online){toast("请先连接机器人");return;}if(motorPending)return;JSONObject caps=info==null?null:info.optJSONObject("capabilities");if(caps==null||!"supported".equals(caps.optString("remote"))){toast("当前固件尚未支持官方遥控");return;}motorRequested=!motorRequested;motorPending=true;c.setMotorEnabled(motorRequested);lastEvent=motorRequested?"正在启用转头，等待机器人确认":"正在停止转头，等待机器人确认";}
    private void acceptMotion(JSONObject t){
        JSONArray samples=t.optJSONArray("imu_samples");String boot=t.optString("boot_id");
        if(samples!=null){for(int i=0;i<samples.length();i++){JSONObject s=samples.optJSONObject(i);if(s!=null)addMotion(boot,s.optLong("seq"),s);}}
        else{JSONObject imu=t.optJSONObject("imu");if(imu!=null)addMotion(boot,t.optLong("seq"),imu);}
    }
    private void addMotion(String boot,long seq,JSONObject s){JSONArray a=s.optJSONArray("accel"),g=s.optJSONArray("gyro");double[] v=new double[6];for(int i=0;i<3;i++){v[i]=a==null?Double.NaN:a.optDouble(i,Double.NaN);v[i+3]=g==null?Double.NaN:g.optDouble(i,Double.NaN);}motionHistory.add(boot,seq,s.optLong("timestamp_us"),s.optBoolean("valid"),v);}
    private final Runnable refresh=new Runnable(){public void run(){if(destroyed)return;
        PreviewFrame frame=latestBitmap.getAndSet(null);if(frame!=null){
            if(info!=null&&frame.boot.equals(info.optString("boot_id")))camera.show(frame.bitmap,frame.faces,frame.received);else frame.bitmap.recycle();
        }
        long now=System.currentTimeMillis();
        if(foreground&&!userPaused&&!online&&binding.hasCredentials()&&now-lastDiscovery>=2000){lastDiscovery=now;discovery.scan(binding);}
        boolean staleMotion=!online||lastTelemetryWall==0||now-lastTelemetryWall>2000;
        motionChart.refresh(staleMotion);audioLevel.refresh(online);
        TelemetryReceipt receipt=latestTelemetry;JSONObject t=receipt==null?null:receipt.value;
        long receivedAgeUs=receipt==null?0:Math.max(0,SystemClock.elapsedRealtime()-receipt.elapsedMs)*1000;
        magneticField.refresh(t,staleMotion,receivedAgeUs);
        updateFaces(t,staleMotion);
        // Signal views run at 15 fps; text changes at most four times a second.
        if(lastRateWall==0){lastRateWall=now;lastRateFrames=videoFrames;}
        if(now-lastRateWall>=2000){receivedFps=(videoFrames-lastRateFrames)*1000.0/(now-lastRateWall);lastRateWall=now;lastRateFrames=videoFrames;}
        if(now-lastTextRefresh>=250){lastTextRefresh=now;
            setTextIfChanged(status,"● "+shortConnectionStatus());status.setTextColor(online?ACCENT:MUTED);setTextIfChanged(connectionDetail,connectionStatus);
            JSONObject di=info;if(di!=null)setTextIfChanged(device,di.optString("device_id","StackChan")+"  /  "+di.optString("firmware","采集固件"));
            setTextIfChanged(frameMeta,frameDescription+(lastFrameWall>0?String.format(Locale.ROOT,"   实收 %.1f fps",receivedFps):"")+(lastFrameWall>0&&now-lastFrameWall>2500?"  ·  画面已过期":""));
            boolean freshAudio=lastAudioElapsed>0&&SystemClock.elapsedRealtime()-lastAudioElapsed<2000;setTextIfChanged(audioMeta,audioDescription+(lastAudioElapsed>0&&!freshAudio?" · 音频中断":""));
            setTextIfChanged(environmentState,compactEnvironment(t,staleMotion,receivedAgeUs));
            if(t!=null){
                setTextIfChanged(compactState,staleMotion?"电量 —  Wi-Fi —\n遥控 —  转头 —  屏 —\n状态已过期":compactTelemetry(t));
                setTextIfChanged(healthText,formatHealth(t));setTextIfChanged(sensorText,formatTelemetry(t,online?receivedAgeUs:Long.MAX_VALUE)+(staleMotion?"\n数据已过期":""));
                JSONObject remote=t.optJSONObject("remote");remoteView.update(remote,staleMotion);
                JSONObject motor=remote==null?null:remote.optJSONObject("motor");
                pitchView.update(t.optJSONObject("pitch_setting"),online,staleMotion,t.optLong("timestamp_us"),!motorPending&&motor!=null&&motor.optBoolean("requested")&&motor.optBoolean("enabled"));
                JSONObject lastInput=remote==null?null:remote.optJSONObject("last");long inputAge=lastInput==null?0:Math.max(0,(t.optLong("timestamp_us")-lastInput.optLong("timestamp_us"))/1000000);
                setTextIfChanged(remoteState,remote==null?"官方遥控 · 固件待升级":("遥控 CH "+remote.optInt("wifi_channel")+" / ID "+remote.optInt("receiver_id")+"\n"+(!remote.optBoolean("ready")?"接收器不可用":remote.optBoolean("valid")?"已收 "+remote.optLong("rx_packets")+" 次 · 最近 "+inputAge+"s":"等待摇杆 / 姿态输入")+"\n"+(staleMotion?"转头状态已过期":motorStatus(remote.optJSONObject("motor")))));
                MotionHistory.Snapshot data=motionHistory.snapshot();double hz=data.size()>1?(data.size()-1)*1e6/(data.times[data.size()-1]-data.times[0]):0;
                setTextIfChanged(imuMeta,staleMotion?"IMU — Hz\n数据已过期":String.format(Locale.ROOT,"IMU %.1f Hz · 4 秒\n头部坐标%s",hz,t.optLong("imu_history_lost")>0?" · 存在缺样":""));
            }else{
                setTextIfChanged(compactState,"电量 —  Wi-Fi —\n遥控 —  转头 —  屏 —\n仰角 设— / 实—");setTextIfChanged(imuMeta,"IMU — Hz\n等待采样");
                setTextIfChanged(sensorText,"等待传感器数据");setTextIfChanged(healthText,"");setTextIfChanged(remoteState,"官方遥控 · 等待设备信息");remoteView.update(null,true);
                pitchView.update(di==null?null:di.optJSONObject("pitch_setting"),online,true,0,false);
            }
            motorButton.setEnabled(online&&t!=null&&!motorPending);setTextIfChanged(motorButton,!online||t==null?"等待连接":motorPending?"设置中…":motorRequested?"停止转头":"启用转头");
            SessionRecorder r=recorder;
            if(r!=null){long secs=(now-sessionStarted)/1000;String elapsed=String.format(Locale.ROOT,"● 记录 %02d:%02d",secs/60,secs%60);setTextIfChanged(recordState,elapsed);setTextIfChanged(recordDetailState,elapsed);recordState.setTextColor(ACCENT);setTextIfChanged(eventText,lastEvent+" · 写盘丢弃 "+r.dropped()+" 项");}
            else{String idle=finalizing?"正在封存…":"未记录";setTextIfChanged(recordState,idle);setTextIfChanged(recordDetailState,idle);recordState.setTextColor(MUTED);setTextIfChanged(eventText,lastEvent);}
            record.setEnabled(!finalizing&&(r!=null||(online&&info!=null)));mark.setEnabled(r!=null);
            monitorButton.setEnabled(monitor!=null||(online&&client!=null));setTextIfChanged(monitorButton,monitor==null?"监听：关":"监听：开");
        }
        ui.postDelayed(this,67);
    }};
    private String shortConnectionStatus(){
        if(online)return "已连接";
        if(!binding.hasCredentials())return "未绑定";
        if(userPaused)return "已暂停";
        return client==null?"等待连接":"重连中";
    }
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
    private static String compactTelemetry(JSONObject t){
        JSONObject power=t.optJSONObject("power"),health=t.optJSONObject("health"),remote=t.optJSONObject("remote"),pitch=t.optJSONObject("pitch_setting"),display=t.optJSONObject("display");
        JSONObject last=remote==null?null:remote.optJSONObject("last"),motor=remote==null?null:remote.optJSONObject("motor");
        String battery=available(power,t)?numeric(power,"battery_percent",0)+"%"+(power.optBoolean("charging")?"充电":""):"—";
        String input="未收";long sampled=last==null?0:last.optLong("timestamp_us");
        if(sampled>0){long seconds=Math.max(0,(t.optLong("timestamp_us")-sampled)/1000000);input=seconds<60?seconds+"s前":seconds<3600?seconds/60+"m前":seconds/3600+"h前";}
        else if(remote==null)input="—";
        String turn=motor==null?"—":motor.optBoolean("enabled")?"开":motor.optBoolean("requested")?"准备":"关";
        String measured="—";long age=pitch==null?Long.MAX_VALUE:t.optLong("timestamp_us")-pitch.optLong("feedback_timestamp_us");
        if(pitch!=null&&pitch.optBoolean("feedback_valid")&&t.optLong("timestamp_us")>0&&age>=-250000&&age<=1000000)measured=angleValue(pitch,"measured_deg");
        String screen=display==null||!display.has("enabled")?"—":display.optBoolean("enabled")?"亮":"灭";
        return "电量 "+battery+"  Wi-Fi "+numeric(health,"wifi_rssi_dbm",0)+"dBm"+
            "\n遥控 "+input+"  转头 "+turn+"  屏 "+screen+
            "\n仰角 设"+angleValue(pitch,"configured_deg")+" / 实"+measured;
    }
    private static String angleValue(JSONObject source,String key){Number value=number(source,key);if(value==null)return "—";double degrees=value.doubleValue();return String.format(Locale.ROOT,degrees==Math.rint(degrees)?"%.0f°":"%.1f°",degrees);}
    static String compactEnvironment(JSONObject telemetry,boolean stale){return compactEnvironment(telemetry,stale,0);}
    static String compactEnvironment(JSONObject telemetry,boolean stale,long receivedAgeUs){
        JSONObject light=telemetry==null?null:telemetry.optJSONObject("light"),proximity=telemetry==null?null:telemetry.optJSONObject("proximity");
        String lux="—",raw="—";
        if(!stale&&environmentAvailable(light,telemetry,receivedAgeUs)&&nonnegativeNumber(light,"lux"))lux=numeric(light,"lux",1);
        if(!stale&&environmentAvailable(proximity,telemetry,receivedAgeUs)&&validProximity(proximity))raw=numeric(proximity,"raw",0);
        return "光 "+lux+" lx"+(telemetry!=null&&(stale||environmentStale(light,telemetry,receivedAgeUs))?" · 过期":"")+
            "\n接近 "+raw+" raw"+(telemetry!=null&&(stale||environmentStale(proximity,telemetry,receivedAgeUs))?" · 过期":"");
    }
    private static boolean nonnegativeNumber(JSONObject group,String key){Number value=number(group,key);return value!=null&&value.doubleValue()>=0;}
    private static boolean validProximity(JSONObject group){Number value=number(group,"raw");return value!=null&&value.doubleValue()>=0&&value.doubleValue()<=2047&&value.doubleValue()==Math.rint(value.doubleValue());}
    private static boolean environmentStale(JSONObject group,JSONObject telemetry,long receivedAgeUs){
        if(group==null)return false;if("stale".equals(group.optString("error")))return true;
        Number sampled=number(group,"timestamp_us"),current=number(telemetry,"timestamp_us");
        return sampled!=null&&current!=null&&sampled.doubleValue()>0&&current.doubleValue()-sampled.doubleValue()+Math.max(0,receivedAgeUs)>1000000;
    }
    private static boolean environmentAvailable(JSONObject group,JSONObject telemetry,long receivedAgeUs){
        if(group==null||!group.optBoolean("available")||!group.optBoolean("valid")||group.optBoolean("saturated")||group.optBoolean("overflow"))return false;
        Number sampled=number(group,"timestamp_us"),current=number(telemetry,"timestamp_us");
        if(sampled==null||current==null||sampled.longValue()<=0||current.longValue()<=0)return false;
        double age=current.doubleValue()-sampled.doubleValue();
        // Reject invalid device time before adding monotonic receive/replay age.
        return age>=-250000&&age<=1000000&&age+Math.max(0,receivedAgeUs)<=1000000;
    }
    private static String environmentUnavailable(JSONObject group,JSONObject telemetry,long receivedAgeUs){
        if(group==null)return "未提供";
        if(environmentStale(group,telemetry,receivedAgeUs))return "数据已过期";
        if(group.optBoolean("saturated"))return "读数饱和";
        if(group.optBoolean("overflow"))return "读数溢出";
        String error=group.optString("error","none"),reason;
        switch(error){
            case "none":reason=group.optBoolean("available")?"不可用":"未就绪";break;
            case "not_initialized":reason="未初始化";break;
            case "not_detected":case "chip_id":case "chip_id_mismatch":reason="未检出传感器";break;
            case "i2c":case "i2c_error":case "read_failed":reason="读取失败";break;
            case "config":reason="配置失败";break;
            case "trim":reason="校准参数无效";break;
            case "invalid":reason="读数无效";break;
            case "saturated":reason="读数饱和";break;
            case "overflow":reason="读数溢出";break;
            default:reason="不可用（"+(error.length()>40?error.substring(0,40)+"…":error)+"）";
        }
        return reason;
    }
    private static boolean magneticVector(JSONObject group){
        JSONArray field=group==null?null:group.optJSONArray("field_ut");if(field==null||field.length()!=3)return false;
        for(int axis=0;axis<3;axis++){Object value=field.opt(axis);if(!(value instanceof Number)||!Double.isFinite(((Number)value).doubleValue()))return false;}return true;
    }
    static String formatTelemetry(JSONObject t){return formatTelemetry(t,0);}
    static String formatTelemetry(JSONObject t,long receivedAgeUs){
        JSONObject imu=t.optJSONObject("imu"),head=t.optJSONObject("head_touch"),touch=t.optJSONObject("touch"),power=t.optJSONObject("power");
        JSONObject magnetic=t.optJSONObject("magnetometer"),light=t.optJSONObject("light"),proximity=t.optJSONObject("proximity");
        StringBuilder s=new StringBuilder();
        s.append("加速度 (").append(imu==null?"m/s²":imu.optString("accel_unit","m/s²")).append(")\n");
        s.append(available(imu,t)?axes(imu.optJSONArray("accel")):unavailable(imu,t));
        s.append("\n角速度 (").append(imu==null?"°/s":imu.optString("gyro_unit","°/s")).append(")\n");
        s.append(available(imu,t)?axes(imu.optJSONArray("gyro")):unavailable(imu,t));
        s.append("\n磁场 (µT，传感器坐标)\n").append(environmentAvailable(magnetic,t,receivedAgeUs)&&magneticVector(magnetic)?axes(magnetic.optJSONArray("field_ut")):environmentUnavailable(magnetic,t,receivedAgeUs));
        s.append("\n光照 (lx)  ").append(environmentAvailable(light,t,receivedAgeUs)&&nonnegativeNumber(light,"lux")?numeric(light,"lux",1):environmentUnavailable(light,t,receivedAgeUs));
        s.append("\n接近 (原始计数)  ").append(environmentAvailable(proximity,t,receivedAgeUs)&&validProximity(proximity)?numeric(proximity,"raw",0):environmentUnavailable(proximity,t,receivedAgeUs));
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
    @Override protected void onStop(){foreground=false;routeProbeSerial++;probingAddress="";if(discovery!=null)discovery.stop();if(client!=null||recorder!=null)disconnect("应用离开前台，已停止采集");super.onStop();}
    @Override protected void onDestroy(){destroyed=true;disconnect("观察台已关闭");ui.removeCallbacks(refresh);if(discovery!=null)discovery.close();network.shutdown();files.shutdown();super.onDestroy();}
    private EditText input(String hint,boolean password){EditText e=new EditText(this);e.setTextSize(15);e.setTypeface(HudTheme.REGULAR);e.setTextColor(INK);e.setHintTextColor(MUTED);e.setHint(hint);e.setSingleLine(true);e.setInputType(password?InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);e.setPadding(dp(12),dp(10),dp(12),dp(10));LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,dp(52));lp.bottomMargin=dp(7);e.setLayoutParams(lp);e.setBackground(outline(BG,ACCENT,1));e.setBackgroundTintList(null);return e;}
    private TextView text(String s,int size,int color){TextView t=new TextView(this);t.setText(s);t.setTextSize(size);t.setTypeface(HudTheme.REGULAR);t.setTextColor(color);return t;}
    private Button button(String s,boolean primary){
        Button b=new Button(this);b.setText(s);b.setAllCaps(false);b.setTextSize(13);b.setTypeface(HudTheme.MEDIUM);b.setTextColor(new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled},new int[]{}},new int[]{MUTED,INK}));
        b.setMinWidth(dp(64));b.setMinimumWidth(dp(64));b.setMinHeight(dp(36));b.setMinimumHeight(dp(36));b.setPadding(dp(10),dp(4),dp(10),dp(4));
        StateListDrawable background=new StateListDrawable();background.addState(new int[]{-android.R.attr.state_enabled},outline(BG,HudTheme.GRID,1));background.addState(new int[]{android.R.attr.state_pressed},outline(HudTheme.GRID,ACCENT,2));background.addState(new int[]{android.R.attr.state_focused},outline(HudTheme.GRID,ACCENT,2));background.addState(new int[]{},outline(BG,ACCENT,primary?2:1));
        b.setBackground(background);b.setBackgroundTintList(null);b.setStateListAnimator(null);b.setElevation(0);return b;
    }
    private LinearLayout row(){LinearLayout l=new LinearLayout(this);l.setOrientation(LinearLayout.HORIZONTAL);return l;}
    private LinearLayout column(){LinearLayout l=new LinearLayout(this);l.setOrientation(LinearLayout.VERTICAL);return l;}
    private void spacer(LinearLayout parent,int height){parent.addView(new View(this),new LinearLayout.LayoutParams(-1,dp(height)));}
    private void divider(LinearLayout parent){View v=new View(this);v.setBackgroundColor(HudTheme.GRID);parent.addView(v,new LinearLayout.LayoutParams(-1,dp(1)));}
    private GradientDrawable outline(int color,int stroke,int width){GradientDrawable d=new GradientDrawable();d.setColor(color);d.setStroke(dp(width),stroke);return d;}
    private int dp(int px){return (int)(px*getResources().getDisplayMetrics().density+.5f);}
    private void toast(String s){Toast.makeText(this,s,Toast.LENGTH_SHORT).show();}
}
