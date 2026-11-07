package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;
import org.json.JSONObject;
import java.util.Locale;

/** Mapped remote input and physical feedback, never labeled as raw joystick input. */
public final class RemoteView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private JSONObject data;
    private boolean stale=true;
    private final float d;
    public RemoteView(Context context){super(context);d=getResources().getDisplayMetrics().density;setBackgroundColor(HudTheme.BG);p.setTypeface(HudTheme.REGULAR);}
    public void update(JSONObject remote,boolean stale){data=remote;this.stale=stale;invalidate();}
    private void text(Canvas c,String s,float x,float y,int color,int size){p.setColor(color);p.setStyle(Paint.Style.FILL);p.setTextSize(size*d);c.drawText(s,x,y,p);}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float l=3*d,t=6*d,w=Math.min(110*d,getWidth()*.28f),h=57*d;
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(d);p.setColor(HudTheme.GRID);c.drawRect(l,t,l+w,t+h,p);c.drawLine(l+w/2,t,l+w/2,t+h,p);c.drawLine(l,t+h/2,l+w,t+h/2,p);
        JSONObject last=data==null?null:data.optJSONObject("last"),motor=data==null?null:data.optJSONObject("motor");
        boolean yawOnly=data!=null&&!data.optBoolean("pitch_control_enabled",true);
        boolean reactions=yawOnly&&"home_reaction".equals(data.optString("pitch_input_role"));
        boolean buttonNod=reactions&&"joystick_center_v1".equals(data.optString("nod_trigger"));
        String centerRole=data==null?"":data.optString("center_input_role");
        boolean centerDisplay=reactions&&"display_toggle".equals(centerRole);
        boolean centerRecordOnly=reactions&&data.optBoolean("center_press_supported",false)&&"none".equals(data.optString("nod_trigger"))&&(centerRole.isEmpty()||"record_only".equals(centerRole));
        boolean senderCenter=data!=null&&data.optBoolean("sender_center_supported",false);
        JSONObject yaw=motor==null?null:motor.optJSONObject("yaw"),pitch=motor==null?null:motor.optJSONObject("pitch");
        boolean measured=!stale&&motor!=null&&motor.optLong("feedback_age_ms",-1)>=0&&motor.optLong("feedback_age_ms",-1)<1000&&yaw!=null&&pitch!=null&&yaw.optBoolean("valid")&&pitch.optBoolean("valid");
        c.save();c.clipRect(l,t,l+w,t+h);
        if(last!=null){float x=l+w*(float)((last.optDouble("yaw_deg",0)+128)/256),y=t+h*(float)(1-last.optDouble("pitch_deg",45)/90);p.setColor(HudTheme.ACCENT);p.setStyle(Paint.Style.FILL);c.drawCircle(x,y,4*d,p);}
        if(measured){float x=l+w*(float)((yaw.optDouble("angle_deg",0)+128)/256),y=t+h*(float)(1-pitch.optDouble("angle_deg",45)/90);p.setColor(HudTheme.TEXT);p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(2*d);c.drawCircle(x,y,6*d,p);}
        c.restore();float x=l+w+14*d;
        String inputLabel=yawOnly?"输入":"目标";
        text(c,last==null?"● "+inputLabel+"  等待遥控输入":String.format(Locale.ROOT,"● %s  %+.1f° / %.1f°",inputLabel,last.optDouble("yaw_deg"),last.optDouble("pitch_deg")),x,21*d,HudTheme.ACCENT,12);
        text(c,measured?String.format(Locale.ROOT,"○ 反馈  %+.1f° / %.1f°",yaw.optDouble("angle_deg"),pitch.optDouble("angle_deg")):"○ 反馈  不可用",x,43*d,HudTheme.TEXT,12);
        String auxiliaryState=last==null?"":("辅助按钮状态 "+(last.optBoolean("auxiliary")?"1":"0"));
        String tail=last==null?"水平 / 俯仰 · 目标不是摇杆原值":("速度 "+last.optInt("speed")+"  "+auxiliaryState+"  "+last.optInt("rssi_dbm")+" dBm");
        if(yawOnly)tail="仅左右转动 · 上下输入保留";
        if(reactions)tail="左右转头 · 上下表情"+(centerDisplay?" · 中心开关屏":buttonNod?" · 中心点头":centerRecordOnly?" · 中心仅记录":"");
        text(c,"水平角 / 俯仰角",l,76*d,HudTheme.MUTED,9);
        text(c,stale?"遥测已过期":tail,l,getHeight()-5*d,stale?HudTheme.WARNING:HudTheme.MUTED,10);
        if(buttonNod&&!senderCenter)text(c,"中心点头需升级遥控器",x,65*d,HudTheme.WARNING,10);
        String behavior=reactions?("左右输入转头，上下输入触发表情。"+(centerDisplay?("摇杆中心切换显示屏开关。"+(senderCenter?"已收到中心按压扩展协议。":"等待中心按压扩展协议。")):buttonNod?("机器人支持摇杆中心点头。"+(senderCenter?"已收到中心按压扩展协议。":"中心点头需升级遥控器。")):centerRecordOnly?("摇杆中心仅记录，尚未绑定动作。"+(senderCenter?"已收到中心按压扩展协议。":"等待中心按压扩展协议。")):"")):(yawOnly?"仅左右转动，上下输入仅记录、不驱动舵机。":"");
        setContentDescription(behavior+(last==null?"尚无遥控输入。":String.format(Locale.ROOT,"遥控%s水平%.1f度，俯仰%.1f度。",inputLabel,last.optDouble("yaw_deg"),last.optDouble("pitch_deg"))+auxiliaryState+"。")+(measured?String.format(Locale.ROOT,"舵机反馈水平%.1f度，俯仰%.1f度。",yaw.optDouble("angle_deg"),pitch.optDouble("angle_deg")):"舵机反馈不可用。"));
    }
}
