package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PathEffect;
import android.view.View;
import java.util.Locale;

/** Compact raw-data strips: XYZ use solid/dashed/dotted lines, with visible missing-data gaps. */
public final class MotionChart extends View {
    private static final int[] COLORS={HudTheme.ACCENT,HudTheme.TEXT,HudTheme.AXIS_Z};
    private static final String[] AXES={"X","Y","Z"};
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path=new Path();
    private final PathEffect[] lineStyles;
    private final MotionHistory history;
    private final boolean gyro;
    private boolean stale=true;
    private final float d;
    public MotionChart(Context context,MotionHistory history,boolean gyro){
        super(context);this.history=history;this.gyro=gyro;d=getResources().getDisplayMetrics().density;
        lineStyles=new PathEffect[]{null,new DashPathEffect(new float[]{6*d,3*d},0),new DashPathEffect(new float[]{d,3*d},0)};
        setBackgroundColor(HudTheme.BG);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
    }
    public void refresh(boolean stale){this.stale=stale;invalidate();}
    private void label(Canvas c,String value,float x,float y,int color,float size,boolean bold){
        paint.setPathEffect(null);paint.setAlpha(255);paint.setColor(color);paint.setStyle(Paint.Style.FILL);
        paint.setTextSize(size*d);paint.setTypeface(bold?HudTheme.MEDIUM:HudTheme.REGULAR);c.drawText(value,x,y,paint);
    }
    private void stroke(int axis){
        paint.setColor(COLORS[axis]);paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(1.6f*d);
        paint.setStrokeCap(axis==2?Paint.Cap.ROUND:Paint.Cap.BUTT);paint.setPathEffect(lineStyles[axis]);
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);MotionHistory.Snapshot s=history.snapshot();int offset=gyro?3:0;
        float width=getWidth(),height=getHeight();boolean compact=height<90*d;
        float titleY=(compact?11:14)*d,legendY=(compact?24:29)*d;
        float top=(compact?30:36)*d,bottom=height-13*d,left=29*d,right=width-2*d;
        boolean hasGap=false;long previousTime=0;
        double limit=gyro?30:20;long end=s.size()==0?0:s.times[s.size()-1];
        for(int i=0;i<s.size();i++){
            if(!s.valid[i]||(previousTime>0&&s.times[i]-previousTime>150000))hasGap=true;
            previousTime=s.times[i];
            if(s.valid[i])for(int a=0;a<3;a++)limit=Math.max(limit,Math.abs(s.values[i][offset+a])*1.12);
        }
        limit=Math.ceil(limit/(gyro?30:10))*(gyro?30:10);
        label(c,gyro?"角速度  °/s":"加速度  m/s²",0,titleY,HudTheme.TEXT,compact?10:11,true);
        String state=stale?(s.size()==0?"等待":"过期"):(hasGap?"缺样":"");
        paint.setTextSize(9*d);float stateWidth=paint.measureText(state);
        if(!state.isEmpty())label(c,state,width-stateWidth-2*d,titleY,HudTheme.WARNING,9,true);
        boolean latestValid=s.size()>0&&s.valid[s.size()-1];
        String detail="等待有效运动数据";
        if(latestValid){double[] last=s.values[s.size()-1];detail=String.format(Locale.ROOT,"X %+.2f，Y %+.2f，Z %+.2f",last[offset],last[offset+1],last[offset+2]);}
        for(int a=0;a<3;a++){
            float x=a*width/3;stroke(a);c.drawLine(x,legendY-3*d,x+10*d,legendY-3*d,paint);
            String value=AXES[a]+(latestValid?String.format(Locale.ROOT," %+.1f",s.values[s.size()-1][offset+a]):" —");
            label(c,value,x+13*d,legendY,COLORS[a],9,false);
        }
        setContentDescription((gyro?"角速度，单位度每秒。":"加速度，包含重力，单位米每二次方秒。")+
            "最近"+(MotionHistory.WINDOW_US/1000000)+"秒，X实线、Y虚线、Z点线。"+detail+(hasGap?"，存在缺样":"")+(stale?"，数据已过期":""));
        if(bottom<=top||right<=left)return;
        paint.setPathEffect(null);paint.setStrokeCap(Paint.Cap.BUTT);paint.setStrokeWidth(d);
        for(int k=-1;k<=1;k++){
            float y=(top+bottom)/2-k*(bottom-top)/2;
            paint.setColor(HudTheme.GRID);paint.setStyle(Paint.Style.STROKE);c.drawLine(left,y,right,y,paint);
            label(c,String.format(Locale.ROOT,"%.0f",k*limit),0,y+3*d,HudTheme.MUTED,8,false);
        }
        for(int k=0;k<=2;k++){
            float x=left+(right-left)*k/2;
            paint.setColor(HudTheme.GRID);paint.setStyle(Paint.Style.STROKE);c.drawLine(x,top,x,bottom,paint);
            long seconds=(2-k)*MotionHistory.WINDOW_US/2/1000000;
            String tick=(seconds==0?"":"−")+seconds+"s";
            label(c,tick,k==2?x-11*d:x,bottom+10*d,HudTheme.MUTED,8,false);
        }
        c.save();c.clipRect(left,top-d,right,bottom+d);
        for(int a=0;a<3;a++){
            path.reset();boolean connected=false;long previous=0;
            for(int i=0;i<s.size();i++){
                if(!s.valid[i]){connected=false;continue;}
                float x=right-(end-s.times[i])*(right-left)/MotionHistory.WINDOW_US;
                float y=(top+bottom)/2-(float)(s.values[i][offset+a]/limit)*(bottom-top)/2;
                if(!connected||s.times[i]-previous>150000)path.moveTo(x,y);else path.lineTo(x,y);
                connected=true;previous=s.times[i];
            }
            stroke(a);paint.setAlpha(stale?115:255);c.drawPath(path,paint);
        }
        c.restore();paint.setPathEffect(null);paint.setAlpha(255);
        if(stale&&s.size()==0){
            String message="等待采集";
            paint.setTypeface(HudTheme.MEDIUM);paint.setTextSize(10*d);float textWidth=paint.measureText(message);
            float x=left+(right-left-textWidth)/2,y=(top+bottom)/2+3*d;
            paint.setStyle(Paint.Style.FILL);paint.setColor(HudTheme.BG);c.drawRect(x-4*d,y-11*d,x+textWidth+4*d,y+3*d,paint);
            label(c,message,x,y,HudTheme.WARNING,10,true);
        }
    }
}
