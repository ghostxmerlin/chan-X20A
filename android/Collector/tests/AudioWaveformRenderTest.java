package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.os.SystemClock;
import android.view.View;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/** Native offscreen rendering with explicitly synthetic PCM, never microphone playback. */
public final class AudioWaveformRenderTest {
    private static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    private static AudioBlock block(long seq,long time,int amplitude,boolean referenceOnly)throws Exception{
        ByteBuffer wire=ByteBuffer.allocate(32+480*4).order(ByteOrder.LITTLE_ENDIAN);
        wire.put(new byte[]{'S','C','A','1'}).putInt((int)seq).putLong(time).putInt(24000).putInt(480).putShort((short)2).putShort((short)16).putInt(480*4);
        for(int i=0;i<480;i++){wire.putShort((short)(referenceOnly?0:(i%2==0?amplitude:-amplitude)));wire.putShort((short)32767);}
        return AudioBlock.read(new ByteArrayInputStream(wire.array()));
    }
    private static Bitmap draw(View view,int width,int height){
        view.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(height,View.MeasureSpec.EXACTLY));view.layout(0,0,width,height);
        Bitmap image=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);view.draw(new Canvas(image));return image;
    }
    private static boolean waveformPixel(int color){int r=(color>>16)&255,g=(color>>8)&255,b=color&255;return g>90&&r<80&&b<120&&g>2*r;}
    private static void noWarningOverlay(Bitmap image,float d){
        for(int y=(int)(29*d);y<image.getHeight()-23*d;y++)for(int x=(int)(23*d);x<image.getWidth()-5*d;x++){
            int color=image.getPixel(x,y),r=(color>>16)&255,g=(color>>8)&255,b=color&255;
            check(!(r>120&&g>150&&b<140),"stale/disconnected status must remain in fixed footer, never cover PCM plot");
        }
    }
    private static float verticalExtent(Bitmap image,float d){
        float middle=(28*d+image.getHeight()-22*d)/2,extent=0;
        for(int y=(int)(29*d);y<image.getHeight()-23*d;y++)for(int x=(int)(23*d);x<image.getWidth()-5*d;x++)
            if(waveformPixel(image.getPixel(x,y)))extent=Math.max(extent,Math.abs(y-middle));
        return extent;
    }
    public static void render(Context context,String destination)throws Exception{
        float d=context.getResources().getDisplayMetrics().density;int width=Math.round(182*d),height=Math.round(85*d);
        AudioWaveformHistory loud=new AudioWaveformHistory(),weak=new AudioWaveformHistory(),silent=new AudioWaveformHistory(),gap=new AudioWaveformHistory();
        for(int i=0;i<100;i++){long t=1000000+i*20000L;long wall=1000+i*20;
            loud.add("synthetic",block(i,t,30000,false),wall);weak.add("synthetic",block(i,t,32,false),wall);silent.add("synthetic",block(i,t,0,true),wall);}
        long[] elapsed={3000};
        AudioWaveformView.MonotonicClock clock=()->elapsed[0];
        AudioWaveformView loudView=new AudioWaveformView(context,loud,clock),weakView=new AudioWaveformView(context,weak,clock),silentView=new AudioWaveformView(context,silent,clock);
        loudView.refresh(true);weakView.refresh(true);silentView.refresh(true);
        Bitmap a=draw(loudView,width,height),b=draw(weakView,width,height),c=draw(silentView,width,height);
        check(verticalExtent(a,d)>10*d,"strong real PCM must occupy fixed-scale plot");
        check(verticalExtent(b,d)<=2*d,"weak PCM must not be auto amplified");
        check(verticalExtent(c,d)<=2*d,"loud reference must not create mic waveform");
        check(weakView.getContentDescription().toString().contains("-60.2 dBFS"),"real first-channel dBFS");
        check(silentView.getContentDescription().toString().contains("−∞ dBFS"),"zero mic is silence, not reference energy");
        gap.add("synthetic",block(1,1000000,30000,false),1000);gap.add("synthetic",block(3,1120000,30000,false),1120);
        AudioWaveformView gapView=new AudioWaveformView(context,gap,clock);elapsed[0]=1140;gapView.refresh(true);Bitmap e=draw(gapView,width,height);
        check(gapView.getContentDescription().toString().contains("间断"),"gap annotation");
        float left=22*d,right=width-4*d;
        int gapLeft=(int)Math.ceil(right-(1140000-1040000)*(right-left)/2000000f),gapRight=(int)Math.floor(right-(1140000-1100000)*(right-left)/2000000f);
        for(int x=gapLeft;x<=gapRight;x++)for(int y=(int)(34*d);y<height-28*d;y++)
            check(!waveformPixel(e.getPixel(x,y)),"no fabricated waveform across missing interval");
        // invalidate happened at 3000, then the producer delivered new PCM
        // while the UI was blocked. Sampling time in refresh would falsely
        // classify this later snapshot as having a future receive timestamp.
        elapsed[0]=3000;weakView.refresh(true);
        weak.add("synthetic",block(100,3000000,32,false),4200);elapsed[0]=4210;
        Bitmap recovered=draw(weakView,width,height);
        check(weakView.getContentDescription().toString().contains("收包中")&&weakView.getContentDescription().toString().contains("-60.2 dBFS"),
              "audio arriving between refresh and delayed draw must stay fresh");
        // Conversely, a genuinely stopped stream must age at draw time even
        // without another refresh call. Do not conceal it with a longer TTL.
        elapsed[0]=5199;Bitmap boundary=draw(weakView,width,height);
        check(weakView.getContentDescription().toString().contains("收包中"),"fresh immediately before one-second boundary");
        int stored=weak.snapshot().size();elapsed[0]=5200;Bitmap f=draw(weakView,width,height);
        check(weakView.getContentDescription().toString().contains("数据已过期")&&!weakView.getContentDescription().toString().contains("-60.2"),"stale audio must not claim current level");
        noWarningOverlay(f,d);
        weak.add("synthetic",block(101,3020000,32,false),5250);elapsed[0]=5251;
        Bitmap resumed=draw(weakView,width,height);
        check(weakView.getContentDescription().toString().contains("收包中")&&weakView.getContentDescription().toString().contains("-60.2 dBFS"),
              "new PCM restores live state immediately after a real interruption");
        elapsed[0]=6000;weakView.refresh(false);Bitmap g=draw(weakView,width,height);
        check(weakView.getContentDescription().toString().contains("已断开")&&weak.snapshot().size()==stored,"disconnect freezes real history without invented samples");
        noWarningOverlay(g,d);
        AudioWaveformView empty=new AudioWaveformView(context,new AudioWaveformHistory(),clock);empty.refresh(true);Bitmap emptyImage=draw(empty,width,height);
        check(empty.getContentDescription().toString().contains("等待音频"),"honest empty state");
        AudioWaveformHistory platformHistory=new AudioWaveformHistory();
        platformHistory.add("synthetic",block(1,1000000,32,false),SystemClock.elapsedRealtime());
        AudioWaveformView platformClockView=new AudioWaveformView(context,platformHistory);platformClockView.refresh(true);
        Bitmap platformClockImage=draw(platformClockView,width,height);
        check(platformClockView.getContentDescription().toString().contains("收包中"),
              "default View must use elapsedRealtime, never compare monotonic reception to the unrelated wall-clock epoch");
        Bitmap contactSheet=Bitmap.createBitmap(width*2,height*4,Bitmap.Config.ARGB_8888);Canvas sheet=new Canvas(contactSheet);
        Bitmap[] panels={a,b,c,e,f,g,emptyImage};for(int i=0;i<panels.length;i++)sheet.drawBitmap(panels[i],(i%2)*width,(i/2)*height,null);
        try(FileOutputStream out=new FileOutputStream(destination)){check(contactSheet.compress(Bitmap.CompressFormat.PNG,100,out),"PNG encode");}
        for(Bitmap panel:panels)panel.recycle();recovered.recycle();boundary.recycle();resumed.recycle();platformClockImage.recycle();contactSheet.recycle();
        renderLevels(context,new File(destination).getParentFile());
        System.out.println("PASS: real-PCM waveform at 182x85dp; fixed amplitude, reference isolation, gaps, delayed-draw monotonic freshness, true interruption and footer-only stale/disconnected states (synthetic inputs only)");
    }

    /** Count only the meter interior, excluding scale, labels and its empty outline. */
    private static int levelPixels(Bitmap image,float d){
        int x=Math.round((30*d+image.getWidth()-5*d)/2),filled=0;
        for(int y=(int)Math.ceil(45*d);y<image.getHeight()-46*d;y++)if(image.getPixel(x,y)==HudTheme.ACCENT)filled++;
        return filled;
    }
    private static void save(Bitmap image,File file)throws Exception{
        try(FileOutputStream out=new FileOutputStream(file)){check(image.compress(Bitmap.CompressFormat.PNG,100,out),"level PNG encode");}
    }
    private static void renderLevels(Context context,File directory)throws Exception{
        float d=context.getResources().getDisplayMetrics().density;int width=Math.round(64*d),height=Math.round(300*d);
        long[] elapsed={1010};AudioLevelView.MonotonicClock clock=()->elapsed[0];
        AudioWaveformHistory history=new AudioWaveformHistory();history.add("level",block(1,1000000,16384,false),1000);
        AudioLevelView view=new AudioLevelView(context,history,clock);view.refresh(true);
        Bitmap normal=draw(view,width,height);float innerHeight=height-91*d;
        check(Math.abs(levelPixels(normal,d)/innerHeight-0.899657)<0.02,"fixed -60..0 dBFS scale maps -6.02 dBFS to measured height");
        check(view.getContentDescription().toString().contains("-6.0 dBFS")&&view.getContentDescription().toString().contains("20.0 毫秒"),"normal latest 20ms mic RMS with explicit units");
        history.add("level",block(2,1020000,1024,false),1020);elapsed[0]=1021;
        Bitmap latest=draw(view,width,height);
        check(Math.abs(levelPixels(latest,d)/innerHeight-0.498283)<0.02&&levelPixels(latest,d)<levelPixels(normal,d),"latest weaker block replaces earlier height immediately with no peak hold");
        check(view.getContentDescription().toString().contains("-30.1 dBFS"),"level is latest block RMS, not history RMS");

        AudioWaveformHistory silentHistory=new AudioWaveformHistory(),weakHistory=new AudioWaveformHistory();
        silentHistory.add("level",block(1,1000000,0,true),1000);weakHistory.add("level",block(1,1000000,16,false),1000);
        AudioLevelView silent=new AudioLevelView(context,silentHistory,clock),weak=new AudioLevelView(context,weakHistory,clock);
        silent.refresh(true);weak.refresh(true);Bitmap silence=draw(silent,width,height),belowFloor=draw(weak,width,height);
        check(levelPixels(silence,d)==0&&silent.getContentDescription().toString().contains("−∞ dBFS"),"silent mic with full-scale reference has no bar");
        check(levelPixels(belowFloor,d)==0&&weak.getContentDescription().toString().contains("-66.2 dBFS"),"below-floor real level stays numeric without minimum bar or automatic gain");

        // A new producer block follows invalidate while the UI is delayed.
        elapsed[0]=1500;view.refresh(true);history.add("level",block(3,1040000,8192,false),2200);elapsed[0]=2210;
        Bitmap delayed=draw(view,width,height);
        check(levelPixels(delayed,d)>0&&view.getContentDescription().toString().contains("-12.0 dBFS")&&view.getContentDescription().toString().contains("收包中"),"delayed draw uses snapshot then current monotonic time");
        elapsed[0]=3199;Bitmap boundary=draw(view,width,height);check(levelPixels(boundary,d)>0,"999ms remains fresh");
        elapsed[0]=3200;Bitmap stale=draw(view,width,height);
        check(levelPixels(stale,d)==0&&view.getContentDescription().toString().contains("已过期")&&!view.getContentDescription().toString().contains("-12.0"),"draw-time 1s expiry hides current bar even without another refresh");
        history.add("level",block(4,1060000,2048,false),3250);elapsed[0]=3251;Bitmap resumed=draw(view,width,height);
        check(levelPixels(resumed,d)>0&&view.getContentDescription().toString().contains("-24.1 dBFS"),"fresh block restores current bar after interruption");
        view.refresh(false);Bitmap disconnected=draw(view,width,height);
        check(levelPixels(disconnected,d)==0&&view.getContentDescription().toString().contains("已断开"),"disconnect hides bar even with fresh stored data");
        AudioLevelView empty=new AudioLevelView(context,new AudioWaveformHistory(),clock);empty.refresh(true);Bitmap waiting=draw(empty,width,height);
        check(levelPixels(waiting,d)==0&&empty.getContentDescription().toString().contains("等待音频"),"empty connected meter waits without false expiry");

        elapsed[0]=3251;view.refresh(true);Bitmap compact=draw(view,Math.round(56*d),Math.round(250*d));
        check(levelPixels(compact,d)>0&&view.getContentDescription().toString().contains("-24.1 dBFS"),"56x250dp compact meter retains scale and current level");
        Bitmap tall=draw(view,Math.round(64*d),Math.round(330*d));
        check(levelPixels(tall,d)>levelPixels(compact,d),"meter uses available vertical space at 64x330dp");
        AudioWaveformHistory realClockHistory=new AudioWaveformHistory();
        realClockHistory.add("level",block(1,1000000,1024,false),SystemClock.elapsedRealtime());
        AudioLevelView realClockView=new AudioLevelView(context,realClockHistory);realClockView.refresh(true);Bitmap realClock=draw(realClockView,width,height);
        check(levelPixels(realClock,d)>0&&realClockView.getContentDescription().toString().contains("收包中"),"default level clock is elapsedRealtime, not wall epoch");

        Bitmap[] panels={normal,latest,silence,belowFloor,stale,disconnected,waiting,delayed};
        Bitmap sheet=Bitmap.createBitmap(width*4,height*2,Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(sheet);
        for(int i=0;i<panels.length;i++)canvas.drawBitmap(panels[i],(i%4)*width,(i/4)*height,null);
        save(sheet,new File(directory,"audio-level.png"));save(compact,new File(directory,"audio-level-compact.png"));
        for(Bitmap panel:panels)panel.recycle();boundary.recycle();resumed.recycle();compact.recycle();tall.recycle();realClock.recycle();sheet.recycle();
        System.out.println("PASS: latest-block mic RMS meter at 56x250/64x300/64x330dp; fixed -60..0 dBFS, silence/reference isolation, latest weak block, no gain/peak hold, delayed draw and exact 1s expiry (synthetic inputs only)");
    }
}
