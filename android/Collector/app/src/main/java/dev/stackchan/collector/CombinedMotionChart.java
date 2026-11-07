package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PathEffect;
import android.view.View;
import java.util.Locale;

/** Side-by-side raw acceleration/rotation plots share device time, never units or vertical scales. */
public final class CombinedMotionChart extends View {
    private static final int[] COLORS={HudTheme.ACCENT,HudTheme.TEXT,HudTheme.AXIS_Z};
    private static final String[] AXES={"X","Y","Z"};
    private final MotionHistory history;
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path=new Path();
    private final PathEffect[] lineStyles;
    private final float d;
    private boolean stale=true;

    public CombinedMotionChart(Context context,MotionHistory history){
        super(context);this.history=history;d=getResources().getDisplayMetrics().density;
        lineStyles=new PathEffect[]{null,new DashPathEffect(new float[]{6*d,3*d},0),new DashPathEffect(new float[]{d,3*d},0)};
        setBackgroundColor(HudTheme.BG);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
    }
    public void refresh(boolean stale){this.stale=stale;invalidate();}
    private void label(Canvas canvas,String value,float x,float y,int color,float size,boolean bold){
        paint.setStyle(Paint.Style.FILL);paint.setPathEffect(null);paint.setAlpha(255);paint.setColor(color);
        paint.setTypeface(bold?HudTheme.MEDIUM:HudTheme.REGULAR);paint.setTextSize(size*d);canvas.drawText(value,x,y,paint);
    }
    private void grid(Canvas canvas,float x1,float y1,float x2,float y2){
        paint.setPathEffect(null);paint.setAlpha(255);paint.setStrokeCap(Paint.Cap.BUTT);paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(d);paint.setColor(HudTheme.GRID);canvas.drawLine(x1,y1,x2,y2,paint);
    }
    private void stroke(int axis){
        paint.setStyle(Paint.Style.STROKE);paint.setColor(COLORS[axis]);paint.setStrokeWidth(1.5f*d);
        paint.setStrokeCap(axis==2?Paint.Cap.ROUND:Paint.Cap.BUTT);paint.setPathEffect(lineStyles[axis]);
    }
    private static double limit(MotionHistory.Snapshot data,int offset){
        double limit=offset==0?20:30,step=offset==0?10:30;
        for(int i=0;i<data.size();i++)if(data.valid[i])for(int axis=0;axis<3;axis++){
            double value=Math.abs(data.values[i][offset+axis]),margin=value*1.12;
            limit=Math.max(limit,Double.isFinite(margin)?margin:value);
        }
        double rounded=Math.ceil(limit/step)*step;return Double.isFinite(rounded)?rounded:limit;
    }
    private static String number(double value){
        double absolute=Math.abs(value);
        return String.format(Locale.ROOT,absolute>=10000?"%.0e":"%.0f",value).replace("e+","e");
    }
    @Override protected void onDraw(Canvas canvas){
        super.onDraw(canvas);MotionHistory.Snapshot data=history.snapshot();
        float width=getWidth(),height=getHeight(),middle=width/2;
        if(width<180*d||height<80*d)return;
        boolean compact=height<112*d;
        float frameBottom=height-(compact?12:16)*d;
        float plotTop=(compact?31:35)*d,plotBottom=frameBottom-(compact?13:17)*d;
        double accelerationLimit=limit(data,0),rotationLimit=limit(data,3);
        boolean hasGap=false;long previous=0;
        for(int i=0;i<data.size();i++){if(!data.valid[i]||(previous>0&&data.times[i]-previous>150000))hasGap=true;previous=data.times[i];}

        // Equal-height columns preserve the user's left/right comparison. Each
        // column has the same four-second range and its own unit and y scale.
        grid(canvas,d/2,d/2,width-d/2,d/2);grid(canvas,d/2,d/2,d/2,frameBottom);
        grid(canvas,width-d/2,d/2,width-d/2,frameBottom);grid(canvas,d/2,frameBottom,width-d/2,frameBottom);
        grid(canvas,middle,d/2,middle,frameBottom);
        drawColumn(canvas,data,0,0,middle,plotTop,plotBottom,accelerationLimit);
        drawColumn(canvas,data,3,middle,width,plotTop,plotBottom,rotationLimit);
        label(canvas,"最近"+(MotionHistory.WINDOW_US/1000000)+"秒",5*d,height-4*d,HudTheme.MUTED,8.5f,false);
        String state=data.size()==0?"等待采集":stale?"过期":!data.valid[data.size()-1]?"样本无效":hasGap?"时序间断":"";
        if(!state.isEmpty()){
            paint.setTypeface(HudTheme.MEDIUM);paint.setTextSize(8.5f*d);
            float stateWidth=paint.measureText(state);
            label(canvas,state,width-stateWidth-5*d,height-4*d,HudTheme.WARNING,8.5f,true);
        }
        String description="六轴左右并排，共用最近"+(MotionHistory.WINDOW_US/1000000)+"秒时间轴。左侧加速度包含重力，单位米每二次方秒，独立量程正负"+number(accelerationLimit)+
            "。右侧角速度单位度每秒，独立量程正负"+number(rotationLimit)+"。X实线、Y虚线、Z点线。"+
            (data.size()==0?"等待采集。":stale?"数据已过期，当前数值不可用。":!data.valid[data.size()-1]?"最新样本不可用。":"")+(hasGap?"存在数据间断，不连接缺失段。":"");
        if(!description.contentEquals(getContentDescription()==null?"":getContentDescription()))setContentDescription(description);
    }
    private void drawColumn(Canvas canvas,MotionHistory.Snapshot data,int offset,float columnLeft,float columnRight,float top,float bottom,double limit){
        float columnWidth=columnRight-columnLeft;
        label(canvas,offset==0?"加速度":"角速度",columnLeft+5*d,12*d,HudTheme.TEXT,10,true);
        String unit=offset==0?"m/s²":"°/s";
        paint.setTypeface(HudTheme.REGULAR);paint.setTextSize(8*d);float unitWidth=paint.measureText(unit);
        label(canvas,unit,columnRight-unitWidth-5*d,12*d,HudTheme.MUTED,8,false);
        // Only identity and line style belong in this compact legend. Numeric
        // readings remain the measured traces, without squeezing six values in.
        for(int axis=0;axis<3;axis++){
            float x=columnLeft+5*d+axis*(columnWidth-10*d)/3;stroke(axis);paint.setAlpha(255);
            canvas.drawLine(x,24*d,x+8*d,24*d,paint);
            label(canvas,AXES[axis],x+11*d,27*d,COLORS[axis],8.5f,false);
        }
        paint.setTypeface(HudTheme.REGULAR);paint.setTextSize(8*d);
        float left=columnLeft+Math.max(24*d,paint.measureText(number(-limit))+7*d),right=columnRight-5*d;
        if(right<=left)return;
        for(int tick=0;tick<=2;tick++){
            float x=left+(right-left)*tick/2;grid(canvas,x,top,x,bottom);
            long seconds=MotionHistory.WINDOW_US*(2-tick)/2000000;
            String text=tick==2?"0s":"−"+seconds+"s";
            paint.setTypeface(HudTheme.REGULAR);paint.setTextSize(8*d);float textWidth=paint.measureText(text);
            label(canvas,text,tick==0?x:tick==2?x-textWidth:x-textWidth/2,bottom+12*d,HudTheme.MUTED,8,false);
        }
        for(int step=-1;step<=1;step++){
            float y=(top+bottom)/2-step*(bottom-top)/2;grid(canvas,left,y,right,y);
            label(canvas,number(step*limit),columnLeft+4*d,y+3*d,HudTheme.MUTED,8,false);
        }
        long end=data.size()==0?0:data.times[data.size()-1];
        canvas.save();canvas.clipRect(left,top-d,right,bottom+d);
        for(int axis=0;axis<3;axis++){
            path.reset();boolean connected=false;long previous=0;
            for(int i=0;i<data.size();i++){
                if(!data.valid[i]){connected=false;continue;}
                float x=right-(float)((end-data.times[i])*(right-left)/MotionHistory.WINDOW_US);
                float y=(top+bottom)/2-(float)(data.values[i][offset+axis]/limit)*(bottom-top)/2;
                if(!connected||data.times[i]-previous>150000)path.moveTo(x,y);else path.lineTo(x,y);
                connected=true;previous=data.times[i];
            }
            stroke(axis);paint.setAlpha(stale?100:255);canvas.drawPath(path,paint);
        }
        canvas.restore();paint.setPathEffect(null);paint.setAlpha(255);
    }
}
