package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.os.SystemClock;
import android.view.View;
import java.util.Locale;

/** Two seconds of measured mic-channel min/max; fixed full-scale axis, no automatic gain. */
public final class AudioWaveformView extends View {
    private final AudioWaveformHistory history;
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final float d;
    interface MonotonicClock {long nowMs();}
    private final MonotonicClock clock;
    private boolean connected;
    public AudioWaveformView(Context context,AudioWaveformHistory history){
        this(context,history,SystemClock::elapsedRealtime);
    }
    /** Clock injection keeps delayed-draw and stopped-stream tests deterministic. */
    AudioWaveformView(Context context,AudioWaveformHistory history,MonotonicClock clock){
        super(context);this.history=history;this.clock=clock;d=getResources().getDisplayMetrics().density;
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
    }
    /** Call from the existing UI tick. There is no independent timer or worker. */
    public void refresh(boolean connected){this.connected=connected;invalidate();}
    private void label(Canvas c,String text,float x,float y,int color,float size,boolean bold){
        paint.setStyle(Paint.Style.FILL);paint.setColor(color);paint.setAlpha(255);paint.setTextSize(size*d);
        paint.setTypeface(bold?HudTheme.MEDIUM:HudTheme.REGULAR);c.drawText(text,x,y,paint);
    }
    private static String db(double value){
        return Double.isNaN(value)?"— dBFS":value==Double.NEGATIVE_INFINITY?"−∞ dBFS":String.format(Locale.ROOT,"%.1f dBFS",value);
    }
    @Override protected void onDraw(Canvas canvas){
        super.onDraw(canvas);canvas.drawColor(HudTheme.BG);
        AudioWaveformHistory.Snapshot s=history.snapshot();
        // Audio may arrive after invalidate() while drawing is delayed. Compare
        // this immutable snapshot to time sampled here, after the snapshot.
        boolean stale=s.stale(clock.nowMs()),dim=!connected||stale;
        float width=getWidth(),height=getHeight(),left=22*d,right=width-4*d,top=28*d,bottom=height-22*d;
        label(canvas,"麦克风",0,16*d,HudTheme.TEXT,13,true);
        String level=dim?"— dBFS":db(s.rmsDb);
        paint.setTextSize(12*d);paint.setTypeface(HudTheme.REGULAR);
        label(canvas,level,Math.max(68*d,width-paint.measureText(level)-3*d),16*d,HudTheme.TEXT,12,false);
        if(bottom<=top||right<=left)return;
        float middle=(top+bottom)/2,half=(bottom-top)/2;
        for(int k=-1;k<=1;k++){
            float y=middle-k*half;paint.setColor(HudTheme.GRID);paint.setStrokeWidth(d);canvas.drawLine(left,y,right,y,paint);
            label(canvas,k>0?"+1":k<0?"−1":"0",0,y+3*d,HudTheme.MUTED,9,false);
        }
        for(int k=0;k<3;k++){float x=left+(right-left)*k/2;paint.setColor(HudTheme.GRID);canvas.drawLine(x,top,x,bottom,paint);}
        canvas.save();canvas.clipRect(left,top,right,bottom);
        paint.setColor(HudTheme.ACCENT);paint.setAlpha(dim?75:255);paint.setStrokeWidth(Math.max(1,d));
        // Each bar contains only measured extrema from one 5 ms bucket. There
        // are no connecting lines that could bridge missing blocks.
        for(int i=0;i<s.size();i++){
            long center=s.startsUs[i]+(s.endsUs[i]-s.startsUs[i])/2;
            float x=right-(s.endUs-center)*(right-left)/AudioWaveformHistory.WINDOW_US;
            float high=middle-s.maximum[i]*half,low=middle-s.minimum[i]*half;
            canvas.drawLine(x,high,x,low,paint);
        }
        paint.setAlpha(255);paint.setColor(HudTheme.WARNING);paint.setStrokeWidth(d);
        for(int i=1;i<s.size();i++)if(s.breakBefore[i]){
            float x=right-(s.endUs-s.startsUs[i])*(right-left)/AudioWaveformHistory.WINDOW_US;
            // A short interruption marker remains visible even if packet
            // timestamps fail to leave a proportional blank interval.
            canvas.drawLine(x-2*d,top,x,top+4*d,paint);canvas.drawLine(x,top+4*d,x+2*d,top,paint);
        }
        canvas.restore();paint.setAlpha(255);
        String status=!connected?"已断开":s.size()==0?"等待音频":stale?"数据已过期":s.recentGaps>0?"间断 "+s.recentGaps:"收包中";
        label(canvas,"2 s · ±1 FS",0,height-5*d,HudTheme.MUTED,10,false);
        paint.setTextSize(10*d);paint.setTypeface(HudTheme.REGULAR);
        label(canvas,status,Math.max(92*d,width-paint.measureText(status)-3*d),height-5*d,
              dim||s.recentGaps>0?HudTheme.WARNING:HudTheme.TEXT,10,false);
        String description="麦克风首通道，真实 PCM 波形，最近 2 秒，固定幅值正负 1 满幅，不自动增益。"+
            status+"。"+(dim?"当前电平不可用。":"当前电平 "+db(s.rmsDb)+"。")+
            (s.recentGaps>0?"窗口内 "+s.recentGaps+" 处时序、序号或格式间断，不跨间断连接；不等同于丢包数。":"");
        if(!description.contentEquals(getContentDescription()==null?"":getContentDescription()))setContentDescription(description);
    }
}
