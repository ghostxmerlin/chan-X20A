package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;
import org.json.JSONObject;
import java.util.Locale;

/** Mapped remote target and physical feedback, never labeled as raw joystick input. */
public final class RemoteView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private JSONObject data;
    private boolean stale=true;
    private final float d;
    public RemoteView(Context context){super(context);d=getResources().getDisplayMetrics().density;}
    public void update(JSONObject remote,boolean stale){data=remote;this.stale=stale;invalidate();}
    private void text(Canvas c,String s,float x,float y,int color,int size){p.setColor(color);p.setStyle(Paint.Style.FILL);p.setTextSize(size*d);c.drawText(s,x,y,p);}
    @Override protected void onDraw(Canvas c){
        float l=5*d,t=8*d,w=110*d,h=68*d;
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(d);p.setColor(0xffb7c5c8);c.drawRect(l,t,l+w,t+h,p);c.drawLine(l+w/2,t,l+w/2,t+h,p);c.drawLine(l,t+h/2,l+w,t+h/2,p);
        JSONObject last=data==null?null:data.optJSONObject("last"),motor=data==null?null:data.optJSONObject("motor");
        JSONObject yaw=motor==null?null:motor.optJSONObject("yaw"),pitch=motor==null?null:motor.optJSONObject("pitch");
        boolean measured=!stale&&motor!=null&&motor.optLong("feedback_age_ms",-1)>=0&&motor.optLong("feedback_age_ms",-1)<1000&&yaw!=null&&pitch!=null&&yaw.optBoolean("valid")&&pitch.optBoolean("valid");
        if(last!=null){float x=l+w*(float)((last.optDouble("yaw_deg",0)+128)/256),y=t+h*(float)(1-last.optDouble("pitch_deg",45)/90);p.setColor(0xff3574c4);p.setStyle(Paint.Style.FILL);c.drawCircle(x,y,4*d,p);}
        if(measured){float x=l+w*(float)((yaw.optDouble("angle_deg",0)+128)/256),y=t+h*(float)(1-pitch.optDouble("angle_deg",45)/90);p.setColor(0xff178577);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(2*d);c.drawCircle(x,y,6*d,p);}
        float x=130*d;
        text(c,last==null?"● 目标  等待遥控输入":String.format(Locale.ROOT,"● 目标  %+.1f° / %.1f°",last.optDouble("yaw_deg"),last.optDouble("pitch_deg")),x,23*d,0xff3574c4,12);
        text(c,measured?String.format(Locale.ROOT,"○ 反馈  %+.1f° / %.1f°",yaw.optDouble("angle_deg"),pitch.optDouble("angle_deg")):"○ 反馈  不可用",x,44*d,0xff178577,12);
        String tail=last==null?"水平 / 俯仰 · 目标不是摇杆原值":("速度 "+last.optInt("speed")+"  按钮 "+(last.optBoolean("auxiliary")?"按下":"松开")+"  "+last.optInt("rssi_dbm")+" dBm");
        text(c,stale?"遥测已过期":tail,x,66*d,0xff617780,10);
        text(c,"水平角 / 俯仰角",0,91*d,0xff617780,10);
        setContentDescription((last==null?"尚无遥控输入。":String.format(Locale.ROOT,"遥控目标水平%.1f度，俯仰%.1f度。",last.optDouble("yaw_deg"),last.optDouble("pitch_deg")))+(measured?String.format(Locale.ROOT,"舵机反馈水平%.1f度，俯仰%.1f度。",yaw.optDouble("angle_deg"),pitch.optDouble("angle_deg")):"舵机反馈不可用。"));
    }
}
