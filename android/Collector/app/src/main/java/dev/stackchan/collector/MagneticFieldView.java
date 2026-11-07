package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.os.SystemClock;
import android.view.View;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.Locale;

/** Read-only sensor-frame magnetic vector. Its XY projection is not a compass or position. */
public final class MagneticFieldView extends View {
    interface MonotonicClock {long nowMs();}
    private static final double FRESH_US=1000000, FUTURE_TOLERANCE_US=250000;
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path arrow=new Path();
    private final float d,sp;
    private final MonotonicClock clock;
    private Snapshot snapshot;

    private static final class Snapshot {
        final double x,y,z,ageUs;
        final long refreshedMs;
        final String reason;
        Snapshot(double x,double y,double z,double ageUs,long refreshedMs,String reason){
            this.x=x;this.y=y;this.z=z;this.ageUs=ageUs;this.refreshedMs=refreshedMs;this.reason=reason;
        }
    }

    public MagneticFieldView(Context context){this(context,SystemClock::elapsedRealtime);}
    MagneticFieldView(Context context,MonotonicClock clock){
        super(context);this.clock=clock;
        d=getResources().getDisplayMetrics().density;sp=getResources().getDisplayMetrics().scaledDensity;
        setBackgroundColor(HudTheme.BG);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
        snapshot=new Snapshot(0,0,0,0,clock.nowMs(),"等待数据");
    }

    /** Called by the existing UI refresh; no polling, timer, commands or JSON mutation. */
    public void refresh(JSONObject telemetry,boolean staleMotion,long receivedAgeUs){
        JSONObject group=telemetry==null?null:telemetry.optJSONObject("magnetometer");
        String reason=null;double x=0,y=0,z=0,age=0;
        if(telemetry==null)reason="等待数据";
        else if(group==null)reason="未提供";
        else if(staleMotion)reason="数据已过期";
        else if(!Boolean.TRUE.equals(group.opt("available")))reason=unavailable(group,"未就绪");
        else if(group.optBoolean("overflow"))reason="读数溢出";
        else if(group.optBoolean("saturated"))reason="读数饱和";
        else if(!Boolean.TRUE.equals(group.opt("valid"))||!"none".equals(group.optString("error","none")))
            reason=unavailable(group,"读数无效");
        else {
            JSONArray values=group.optJSONArray("field_ut");
            Double sampled=number(group.opt("timestamp_us")),current=number(telemetry.opt("timestamp_us"));
            if(values==null||values.length()!=3||number(values.opt(0))==null||number(values.opt(1))==null||number(values.opt(2))==null)
                reason="读数无效";
            else if(sampled==null||current==null||sampled<=0||current<=0||sampled!=Math.rint(sampled)||current!=Math.rint(current))
                reason="时间无效";
            else {
                double deviceAge=current-sampled;
                if(deviceAge < -FUTURE_TOLERANCE_US)reason="时间无效";
                else {
                    // An asynchronously newer sample is allowed, but must not
                    // gain an extra 250 ms of freshness after reception.
                    age=Math.max(0,deviceAge)+Math.max(0,receivedAgeUs);
                    x=number(values.opt(0));y=number(values.opt(1));z=number(values.opt(2));
                }
            }
        }
        snapshot=new Snapshot(x,y,z,age,clock.nowMs(),reason);invalidate();
    }

    private static Double number(Object value){
        if(!(value instanceof Number))return null;
        double number=((Number)value).doubleValue();return Double.isFinite(number)?number:null;
    }
    private static String unavailable(JSONObject group,String fallback){
        switch(group.optString("error")){
            case "stale":return "数据已过期";
            case "i2c":case "i2c_error":case "read_failed":return "读取失败";
            case "chip_id":case "chip_id_mismatch":case "not_detected":return "未检出传感器";
            case "not_initialized":return "未初始化";
            case "config":return "配置失败";
            case "trim":return "补偿参数无效";
            case "overflow":return "读数溢出";
            case "saturated":return "读数饱和";
            default:return fallback;
        }
    }
    private static String value(double number){
        double magnitude=Math.abs(number);
        return String.format(Locale.ROOT,magnitude>=10000||(magnitude>0&&magnitude<0.05)?"%+.2e":"%+.1f",number);
    }
    private void text(Canvas canvas,String text,float x,float centerY,float size,int color,Paint.Align align,boolean medium,float maximumWidth){
        paint.setStyle(Paint.Style.FILL);paint.setColor(color);paint.setStrokeWidth(d);paint.setTextAlign(align);
        paint.setTypeface(medium?HudTheme.MEDIUM:HudTheme.REGULAR);paint.setTextSize(size);
        float measured=paint.measureText(text);if(measured>maximumWidth&&maximumWidth>0)paint.setTextSize(size*maximumWidth/measured);
        Paint.FontMetrics metrics=paint.getFontMetrics();canvas.drawText(text,x,centerY-(metrics.ascent+metrics.descent)/2,paint);
    }
    @Override protected void onDraw(Canvas canvas){
        super.onDraw(canvas);canvas.drawColor(HudTheme.BG);
        Snapshot data=snapshot;
        // A delayed UI draw must age the immutable refresh snapshot using the
        // same monotonic clock as receive-age calculation, never wall time.
        double age=data.ageUs+Math.max(0,clock.nowMs()-data.refreshedMs)*1000.0;
        String reason=data.reason!=null?data.reason:age>=FRESH_US?"数据已过期":null;
        boolean valid=reason==null,zeroXY=valid&&data.x==0&&data.y==0;
        float width=getWidth(),height=getHeight(),padding=8*d;
        float header=25*d,footer=24*d,top=header,bottom=height-footer;
        text(canvas,"磁场",padding,13*d,15*sp,HudTheme.TEXT,Paint.Align.LEFT,true,width*.5f);
        text(canvas,"µT",width-padding,13*d,12*sp,HudTheme.MUTED,Paint.Align.RIGHT,false,width*.3f);
        if(bottom>top&&width>padding*2){
            float diagramWidth=Math.min(94*d,width*.41f),captionY=bottom-6*d;
            float centerX=padding+(diagramWidth-padding)/2,centerY=(top+captionY-16*d)/2;
            float radius=Math.max(0,Math.min((diagramWidth-padding)/2-12*d,(captionY-16*d-top)/2-5*d));
            paint.setStyle(Paint.Style.STROKE);paint.setColor(HudTheme.GRID);paint.setStrokeWidth(d);
            canvas.drawLine(centerX-radius,centerY,centerX+radius,centerY,paint);
            canvas.drawLine(centerX,centerY-radius,centerX,centerY+radius,paint);
            text(canvas,"X",centerX+radius+5*d,centerY,10*sp,HudTheme.MUTED,Paint.Align.LEFT,false,12*d);
            text(canvas,"Y",centerX,centerY-radius-7*d,10*sp,HudTheme.MUTED,Paint.Align.CENTER,false,12*d);
            if(valid&&!zeroXY&&radius>5*d){
                // Normalize only the drawing length. The values at right remain
                // the actual vector; no heading angle, north or position is inferred.
                double scale=Math.max(Math.abs(data.x),Math.abs(data.y));
                double nx=data.x/scale,ny=data.y/scale,length=Math.hypot(nx,ny);nx/=length;ny/=length;
                float dx=(float)nx,dy=(float)-ny,r=radius-3*d;
                float endX=centerX+dx*r,endY=centerY+dy*r,head=5*d;
                paint.setColor(HudTheme.ACCENT);paint.setStrokeWidth(2*d);
                canvas.drawLine(centerX,centerY,endX,endY,paint);
                arrow.reset();arrow.moveTo(endX,endY);
                arrow.lineTo(endX-dx*head-dy*head*.55f,endY-dy*head+dx*head*.55f);
                arrow.lineTo(endX-dx*head+dy*head*.55f,endY-dy*head-dx*head*.55f);arrow.close();
                paint.setStyle(Paint.Style.FILL);canvas.drawPath(arrow,paint);
            }
            text(canvas,"XY投影",centerX,captionY,10*sp,HudTheme.MUTED,Paint.Align.CENTER,false,diagramWidth-padding);
            float columnLeft=diagramWidth+10*d,row=(bottom-top)/3;
            float size=Math.min(17*sp,row*.68f);
            String[] labels={"X","Y","Z"};double[] values={data.x,data.y,data.z};
            for(int axis=0;axis<3;axis++){
                float y=top+row*(axis+.5f);int color=axis==2?HudTheme.AXIS_Z:HudTheme.TEXT;
                text(canvas,labels[axis],columnLeft,y,14*sp,HudTheme.MUTED,Paint.Align.LEFT,true,16*d);
                text(canvas,valid?value(values[axis]):"—",width-padding,y,size,valid?color:HudTheme.MUTED,
                     Paint.Align.RIGHT,true,width-padding-columnLeft-23*d);
            }
        }
        float footerY=height-10*d;
        if(reason!=null||zeroXY)text(canvas,reason!=null?reason:"XY为零",padding,footerY,11*sp,
            reason!=null?HudTheme.WARNING:HudTheme.MUTED,Paint.Align.LEFT,false,width*.52f-padding);
        text(canvas,"航向未校准",width-padding,footerY,11*sp,HudTheme.MUTED,Paint.Align.RIGHT,false,width*.44f);
        String description="磁场，传感器坐标，单位µT。"+(valid?
            "X "+value(data.x)+"，Y "+value(data.y)+"，Z "+value(data.z)+"。"+(zeroXY?"XY投影为零，无方向箭头。":"XY投影箭头仅表示磁场向量方向。"):
            reason+"，XYZ数值及投影箭头不可用。")+"航向未校准，不表示机器人位置或地理朝向。";
        if(!description.contentEquals(getContentDescription()==null?"":getContentDescription()))setContentDescription(description);
    }
}
