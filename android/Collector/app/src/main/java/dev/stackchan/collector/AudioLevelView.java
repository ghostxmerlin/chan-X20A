package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.os.SystemClock;
import android.view.View;
import java.util.Locale;

/** Latest mic-channel PCM block RMS on a fixed -60..0 dBFS scale. */
public final class AudioLevelView extends View {
    private static final float FLOOR_DB=-60;
    private final AudioWaveformHistory history;
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final float d;
    interface MonotonicClock {long nowMs();}
    private final MonotonicClock clock;
    private boolean connected;

    public AudioLevelView(Context context,AudioWaveformHistory history){
        this(context,history,SystemClock::elapsedRealtime);
    }
    /** Deterministic clock for stopped-stream and delayed-draw regression tests. */
    AudioLevelView(Context context,AudioWaveformHistory history,MonotonicClock clock){
        super(context);this.history=history;this.clock=clock;d=getResources().getDisplayMetrics().density;
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
    }
    /** Uses the Activity's existing UI tick; no animator, worker or independent timer. */
    public void refresh(boolean connected){this.connected=connected;invalidate();}

    private void label(Canvas canvas,String text,float x,float y,int color,float size,Paint.Align align,boolean bold){
        paint.setStyle(Paint.Style.FILL);paint.setColor(color);paint.setTextSize(size*d);paint.setTextAlign(align);
        paint.setTypeface(bold?HudTheme.MEDIUM:HudTheme.REGULAR);canvas.drawText(text,x,y,paint);
    }
    private static String db(double value){
        return value==Double.NEGATIVE_INFINITY?"−∞":String.format(Locale.ROOT,"%.1f",value);
    }
    @Override protected void onDraw(Canvas canvas){
        super.onDraw(canvas);canvas.drawColor(HudTheme.BG);
        AudioWaveformHistory.LevelSnapshot s=history.levelSnapshot();
        // A producer packet can arrive after refresh(). Take time after the
        // snapshot here, so delayed rendering neither invents nor hides a stop.
        boolean stale=s.stale(clock.nowMs());
        boolean current=connected&&!stale&&!Double.isNaN(s.rmsDb);
        String status=!connected?"已断开":!s.hasSample?"等待音频":stale?"已过期":"收包中";
        float width=getWidth(),height=getHeight(),center=width/2;
        label(canvas,"音量",center,15*d,HudTheme.TEXT,12,Paint.Align.CENTER,true);
        label(canvas,"dBFS",center,31*d,HudTheme.MUTED,10,Paint.Align.CENTER,false);
        float left=30*d,right=width-5*d,top=44*d,bottom=height-45*d;
        if(right>left&&bottom>top){
            for(int level=0;level>=-60;level-=20){
                float y=top+(bottom-top)*(-level)/60;
                label(canvas,level==0?"0":"−"+(-level),24*d,y+3*d,HudTheme.MUTED,9,Paint.Align.RIGHT,false);
                paint.setColor(HudTheme.GRID);paint.setStrokeWidth(d);canvas.drawLine(left-3*d,y,right,y,paint);
            }
            paint.setStyle(Paint.Style.STROKE);paint.setColor(HudTheme.GRID);paint.setStrokeWidth(d);
            canvas.drawRect(left,top,right,bottom,paint);paint.setStyle(Paint.Style.FILL);
            if(current){
                float fraction=(float)Math.max(0,Math.min(1,(s.rmsDb-FLOOR_DB)/-FLOOR_DB));
                // Below-floor and digital-silence blocks have no fabricated minimum bar.
                if(fraction>0){
                    paint.setColor(HudTheme.ACCENT);
                    float innerTop=top+d,innerBottom=bottom-d;
                    canvas.drawRect(left+d,innerBottom-fraction*(innerBottom-innerTop),right-d,innerBottom,paint);
                }
            }
        }
        label(canvas,current?db(s.rmsDb):"—",center,height-24*d,current?HudTheme.TEXT:HudTheme.MUTED,12,Paint.Align.CENTER,true);
        label(canvas,status,center,height-7*d,current?HudTheme.TEXT:HudTheme.MUTED,10,Paint.Align.CENTER,false);
        String description="麦克风首通道，最新 PCM 块的 RMS 电平，固定 −60 至 0 dBFS 刻度，无自动增益或峰值保持。"+
            status+"。"+(current?"当前电平 "+db(s.rmsDb)+" dBFS；块时长 "+
            String.format(Locale.ROOT,"%.1f",s.blockDurationUs/1000.0)+" 毫秒。":"当前电平不可用。");
        if(!description.contentEquals(getContentDescription()==null?"":getContentDescription()))setContentDescription(description);
    }
}
