package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Typeface;
import android.view.View;
import java.util.Locale;

/** Two compact scientific strips, with explicit units, device-time axes and missing-data gaps. */
public final class MotionChart extends View {
    private static final int INK=0xff233b4a,MUTED=0xff617780;
    private static final int[] COLORS={0xff3574c4,0xff178577,0xffc56d32};
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path=new Path();
    private final MotionHistory history;
    private final boolean gyro;
    private boolean stale=true;
    private final float d;
    public MotionChart(Context context,MotionHistory history,boolean gyro){super(context);this.history=history;this.gyro=gyro;d=getResources().getDisplayMetrics().density;setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);}
    public void refresh(boolean stale){this.stale=stale;invalidate();}
    private void label(Canvas c,String value,float x,float y,int color,float size,boolean bold){
        paint.setColor(color);paint.setStyle(Paint.Style.FILL);paint.setTextSize(size*d);paint.setTypeface(bold?Typeface.create("sans-serif-medium",0):Typeface.create("sans-serif",0));c.drawText(value,x,y,paint);
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);MotionHistory.Snapshot s=history.snapshot();int offset=gyro?3:0;
        float width=getWidth(),height=getHeight();
        label(c,gyro?"角速度":"加速度",0,17*d,INK,14,true);
        label(c,gyro?"°/s · 头部转动":"m/s² · 含重力",70*d,17*d,MUTED,11,false);
        float legendX=Math.max(190*d,width-108*d);
        for(int a=0;a<3;a++)label(c,new String[]{"X","Y","Z"}[a],legendX+a*36*d,17*d,COLORS[a],11,true);
        double limit=gyro?30:20;long end=s.size()==0?0:s.times[s.size()-1];
        for(int i=0;i<s.size();i++)if(s.valid[i])for(int a=0;a<3;a++)limit=Math.max(limit,Math.abs(s.values[i][offset+a])*1.12);
        limit=Math.ceil(limit/(gyro?30:10))*(gyro?30:10);
        float l=32*d,r=width-4*d,t=30*d,b=height-38*d;
        if(b<=t)return;
        paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(d);
        for(int k=-1;k<=1;k++){
            float y=(t+b)/2-k*(b-t)/2;paint.setColor(k==0?0xffb7c5c8:0xffdbe2df);c.drawLine(l,y,r,y,paint);
            label(c,String.format(Locale.ROOT,"%.0f",k*limit),0,y+4*d,MUTED,10,false);paint.setStyle(Paint.Style.STROKE);
        }
        for(int k=0;k<=4;k++){
            float x=l+(r-l)*k/4;paint.setColor(0xffe2e7e3);c.drawLine(x,t,x,b,paint);
            label(c,k==4?"现在":("−"+(8-k*2)+"s"),k==4?x-24*d:x-4*d,b+14*d,MUTED,9,false);paint.setStyle(Paint.Style.STROKE);
        }
        c.save();c.clipRect(l,t-d,r,b+d);
        for(int a=0;a<3;a++){
            path.reset();boolean connected=false;long previous=0;
            for(int i=0;i<s.size();i++){
                if(!s.valid[i]){connected=false;continue;}
                float x=r-(end-s.times[i])*(r-l)/8000000f;
                float y=(t+b)/2-(float)(s.values[i][offset+a]/limit)*(b-t)/2;
                if(!connected||s.times[i]-previous>150000)path.moveTo(x,y);else path.lineTo(x,y);
                connected=true;previous=s.times[i];
            }
            paint.setColor(COLORS[a]);paint.setAlpha(stale?90:255);paint.setStrokeWidth(1.5f*d);paint.setStyle(Paint.Style.STROKE);c.drawPath(path,paint);
        }
        c.restore();paint.setAlpha(255);
        String detail="等待有效运动数据";
        if(s.size()>0&&s.valid[s.size()-1]){
            double[] latest=s.values[s.size()-1];double norm=Math.sqrt(latest[offset]*latest[offset]+latest[offset+1]*latest[offset+1]+latest[offset+2]*latest[offset+2]);
            detail=String.format(Locale.ROOT,"X %+.2f   Y %+.2f   Z %+.2f",latest[offset],latest[offset+1],latest[offset+2]);
            label(c,detail,0,height-5*d,INK,11,false);
            String strength=gyro?String.format(Locale.ROOT,"转动 %.0f°/s",norm):String.format(Locale.ROOT,"合加速度 %.2fg",norm/9.80665);
            paint.setTextSize(10*d);float sw=paint.measureText(strength);
            if(width>390*d)label(c,strength,width-sw,height-5*d,MUTED,10,false);
        }else label(c,detail,0,height-5*d,MUTED,11,false);
        if(stale)label(c,s.size()==0?"等待采集":"数据已过期",l+10*d,(t+b)/2-10*d,INK,12,true);
        setContentDescription((gyro?"角速度，单位度每秒。":"加速度，包含重力，单位米每二次方秒。")+detail+(stale?"，数据已过期":""));
    }
}
