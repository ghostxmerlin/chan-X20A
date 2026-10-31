package dev.stackchan.collector;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.widget.*;
import org.json.JSONObject;
import java.io.*;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Local timeline replay. Images, telemetry and optional sound use Android receive time. */
final class SessionPlayer {
    private final Activity activity;
    private final File folder;
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final ExecutorService io=Executors.newSingleThreadExecutor();
    private final List<Frame> frames=new ArrayList<>();
    private final List<Sample> sensors=new ArrayList<>();
    private final List<Track> tracks=new ArrayList<>();
    private TextView meta,sensor,summary;
    private ImageView image;
    private SeekBar seek;
    private Button play,sound;
    private volatile boolean dismissed=false;
    private boolean playing=false,audible=false,seeking=false;
    private long origin=0,duration=0,position=0,playAnchor=0;
    private int shownFrame=-2,decodeGeneration=0;
    private MediaPlayer media;
    private Track activeTrack;
    private String recordStatus="";
    SessionPlayer(Activity activity,File folder){this.activity=activity;this.folder=folder;}
    void show(){
        LinearLayout content=new LinearLayout(activity);content.setOrientation(LinearLayout.VERTICAL);content.setPadding(dp(16),dp(10),dp(16),dp(10));
        ScrollView scroll=new ScrollView(activity);scroll.addView(content);
        summary=text("正在读取本地记录…",13);content.addView(summary);
        image=new ImageView(activity);image.setScaleType(ImageView.ScaleType.FIT_CENTER);image.setBackgroundColor(0xff102737);content.addView(image,new LinearLayout.LayoutParams(-1,dp(215)));
        meta=text("",12);content.addView(meta);seek=new SeekBar(activity);seek.setMax(10000);content.addView(seek);
        LinearLayout actions=new LinearLayout(activity);play=new Button(activity);play.setText("播放画面");sound=new Button(activity);sound.setText("声音：关闭");actions.addView(play,new LinearLayout.LayoutParams(0,-2,1));actions.addView(sound,new LinearLayout.LayoutParams(0,-2,1));content.addView(actions);
        sensor=text("",13);content.addView(sensor);
        AlertDialog dialog=new AlertDialog.Builder(activity).setTitle("回放 · "+folder.getName()).setView(scroll).setPositiveButton("关闭",null).create();
        dialog.setOnDismissListener(d->{dismissed=true;ui.removeCallbacks(tick);releaseAudio();io.shutdownNow();});dialog.show();
        play.setEnabled(false);sound.setEnabled(false);
        play.setOnClickListener(v->{playing=!playing;play.setText(playing?"暂停":"播放画面");if(playing){if(position>=duration)position=0;playAnchor=android.os.SystemClock.elapsedRealtime()-position/1000;ui.post(tick);}else releaseAudio();});
        sound.setOnClickListener(v->{audible=!audible;sound.setText(audible?"声音：开启":"声音：关闭");if(!audible)releaseAudio();});
        seek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener(){public void onStartTrackingTouch(SeekBar s){seeking=true;releaseAudio();}public void onStopTrackingTouch(SeekBar s){seeking=false;playAnchor=android.os.SystemClock.elapsedRealtime()-position/1000;}public void onProgressChanged(SeekBar s,int value,boolean user){if(user){position=duration*value/10000;render();}}});
        io.execute(()->{try{load();ui.post(()->{if(dismissed)return;play.setEnabled(true);sound.setEnabled(!tracks.isEmpty());summary.setText(recordStatus+frames.size()+" 帧图像 / "+tracks.size()+" 段声音\n按接收时间回放；设备原始时间戳保留在导出文件中。");render();});}catch(Exception e){ui.post(()->{if(!dismissed)summary.setText("无法回放："+e.getMessage());});}});
    }
    private void load()throws Exception{
        try(BufferedReader r=new BufferedReader(new InputStreamReader(new FileInputStream(new File(folder,"manifest.json")),"UTF-8"))){StringBuilder raw=new StringBuilder();String line;while((line=r.readLine())!=null&&raw.length()<65536)raw.append(line);if(!"complete".equals(new JSONObject(raw.toString()).optString("status")))recordStatus="此记录未完整封存，部分末尾数据可能缺失。\n";}
        File[] segments=folder.listFiles(f->f.isDirectory()&&f.getName().startsWith("segment-"));if(segments==null)throw new IOException("没有分段数据");Arrays.sort(segments);
        for(File segment:segments){
            readLines(new File(segment,"frames.jsonl"),o->{if(frames.size()>=200000)throw new IOException("记录较长，请导出后在电脑查看");Frame f=new Frame();f.file=new File(segment,o.getString("file"));f.time=o.getLong("receive_elapsed_us");f.timestamp=o.optLong("timestamp_us");f.seq=o.optLong("seq");f.boot=o.optString("boot_id");frames.add(f);});
            final long[] last={-1};readLines(new File(segment,"telemetry.jsonl"),o->{long time=o.getLong("receive_elapsed_us");if(last[0]<0||time-last[0]>=200000){if(sensors.size()>=200000)throw new IOException("传感器记录较长，请导出查看");Sample s=new Sample();s.time=time;s.value=o;sensors.add(s);last[0]=time;}});
            final Track[] current={null};readLines(new File(segment,"audio.jsonl"),o->{File file=new File(segment,o.getString("file"));Track t=current[0];if(t==null||!t.file.equals(file)){t=new Track();t.file=file;t.time=o.getLong("receive_elapsed_us");t.rate=o.getInt("sample_rate");tracks.add(t);current[0]=t;}t.frames=o.getLong("sample_offset")+o.getLong("frames");t.end=o.getLong("receive_elapsed_us")+o.getLong("frames")*1000000L/t.rate;});
        }
        frames.sort((a,b)->Long.compare(a.time,b.time));sensors.sort((a,b)->Long.compare(a.time,b.time));tracks.sort((a,b)->Long.compare(a.time,b.time));
        long min=Long.MAX_VALUE,max=0;for(Frame f:frames){min=Math.min(min,f.time);max=Math.max(max,f.time);}for(Sample s:sensors){min=Math.min(min,s.time);max=Math.max(max,s.time);}for(Track t:tracks){min=Math.min(min,t.time);max=Math.max(max,t.end);}if(min==Long.MAX_VALUE)throw new IOException("这条记录没有收到数据");origin=min;duration=Math.max(1,max-min);
    }
    private final Runnable tick=new Runnable(){public void run(){if(dismissed||!playing)return;if(!seeking){position=(android.os.SystemClock.elapsedRealtime()-playAnchor)*1000;if(position>=duration){position=duration;playing=false;play.setText("播放画面");releaseAudio();}render();if(playing&&audible)syncAudio();}if(playing)ui.postDelayed(this,80);}};
    private void render(){if(dismissed)return;long time=origin+position;int found=-1;for(int i=0;i<frames.size();i++){if(frames.get(i).time>time)break;found=i;}
        seek.setProgress((int)(10000*position/Math.max(1,duration)));String clock=String.format(Locale.ROOT,"%.1f / %.1f 秒",position/1000000.0,duration/1000000.0);
        if(found>=0){Frame f=frames.get(found);meta.setText(clock+"\n第 "+f.seq+" 帧 · 设备 "+String.format(Locale.ROOT,"%.3f s",f.timestamp/1000000.0)+"\n启动 "+f.boot+(time-f.time>2500000?" · 此处为采集中断间隙":""));
            if(found!=shownFrame){shownFrame=found;final int request=++decodeGeneration;io.execute(()->{Bitmap bitmap=BitmapFactory.decodeFile(f.file.getAbsolutePath());ui.post(()->{if(!dismissed&&request==decodeGeneration)image.setImageBitmap(bitmap);else if(bitmap!=null)bitmap.recycle();});});}
        }else meta.setText(clock+"\n此时还没有图像");
        Sample latest=null;for(Sample s:sensors){if(s.time>time)break;latest=s;}sensor.setText(latest==null?"此时还没有传感器数据":MainActivity.formatTelemetry(latest.value));
    }
    private void syncAudio(){long time=origin+position;Track found=null;for(Track t:tracks){if(t.time>time)break;if(time<t.end)found=t;}
        if(found==null){releaseAudio();return;}if(found==activeTrack)return;releaseAudio();final Track track=found;
        try{media=new MediaPlayer();activeTrack=track;media.setDataSource(track.file.getAbsolutePath());media.setOnPreparedListener(p->{if(dismissed||!playing||!audible||activeTrack!=track)return;p.seekTo((int)Math.max(0,(origin+position-track.time)/1000));p.start();});media.setOnErrorListener((p,a,b)->{releaseAudio();sound.setText("此音频无法播放");return true;});media.prepareAsync();}catch(Exception e){releaseAudio();sound.setText("此音频无法播放");}
    }
    private void releaseAudio(){if(media!=null){media.release();media=null;}activeTrack=null;}
    private interface Row{void accept(JSONObject o)throws Exception;}
    private void readLines(File file,Row row)throws Exception{if(!file.exists())return;try(BufferedReader r=new BufferedReader(new InputStreamReader(new FileInputStream(file),"UTF-8"))){String line;while((line=r.readLine())!=null){if(dismissed)return;try{row.accept(new JSONObject(line));}catch(org.json.JSONException e){/* interrupted final line remains visible through manifest status */}}}}
    private TextView text(String s,int size){TextView t=new TextView(activity);t.setText(s);t.setTextColor(0xff15324a);t.setTextSize(size);t.setPadding(0,dp(8),0,dp(8));return t;}
    private int dp(int n){return(int)(n*activity.getResources().getDisplayMetrics().density+.5f);}
    private static class Frame{File file;long time,timestamp,seq;String boot;}
    private static class Sample{long time;JSONObject value;}
    private static class Track{File file;long time,end,frames;int rate;}
}
