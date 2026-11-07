package dev.stackchan.collector;

import android.content.Context;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import org.json.JSONObject;
import java.util.Locale;

/** Immediate, persisted five-degree steps. Only one confirmed trajectory at a time. */
final class PitchSettingView extends LinearLayout {
    interface Listener {boolean apply(double degrees);}
    private final TextView value;
    private final Button minus,plus;
    private final Listener listener;
    private JSONObject current;
    private boolean pending=false,online=false,stale=true,motorEnabled=false;
    private long acceptedSeq=0,deviceTimeUs=0;
    private String error="";
    PitchSettingView(Context context,Listener listener){
        super(context);this.listener=listener;setOrientation(HORIZONTAL);setGravity(Gravity.CENTER_VERTICAL);setBackgroundColor(HudTheme.BG);
        minus=stepButton("−","仰角减少 5 度","pitch_minus");addView(minus,new LayoutParams(dp(44),dp(44)));
        value=new TextView(context);value.setTextSize(14);value.setTextColor(HudTheme.TEXT);value.setTypeface(HudTheme.MEDIUM);value.setIncludeFontPadding(false);value.setGravity(Gravity.CENTER);value.setMaxLines(2);addView(value,new LayoutParams(0,-2,1));
        plus=stepButton("+","仰角增加 5 度","pitch_plus");addView(plus,new LayoutParams(dp(44),dp(44)));
        minus.setOnClickListener(v->step(-5));plus.setOnClickListener(v->step(5));render();
    }
    private Button stepButton(String text,String description,String tag){Button b=new Button(getContext());b.setText(text);b.setTextSize(24);b.setPadding(0,0,0,0);b.setContentDescription(description);b.setTag(tag);HudTheme.styleButton(b);return b;}
    private int dp(float n){return Math.round(n*getResources().getDisplayMetrics().density);}
    private double saved(){return current==null?Double.NaN:current.optDouble("configured_deg",Double.NaN);}
    private boolean moving(){String s=current==null?"":current.optString("state");return s.equals("moving")||s.equals("queued")||s.equals("waiting_feedback");}
    private boolean ready(){double s=saved();return online&&!stale&&motorEnabled&&!pending&&!moving()&&Double.isFinite(s)&&s>=5&&s<=85;}
    private void step(int delta){
        if(!ready())return;
        double next=Math.max(5,Math.min(85,Math.round((saved()+delta)*10)/10.0));
        if(next==saved())return;
        // Lock before dispatch, including synchronous callbacks; never repeat on hold.
        pending=true;error="";render();
        if(!listener.apply(next)){pending=false;error="未发送";render();}
    }
    void reset(){current=null;acceptedSeq=0;pending=false;motorEnabled=false;error="";render();}
    void update(JSONObject setting,boolean connected,boolean stale,long deviceTimeUs,boolean motorEnabled){
        online=connected;this.stale=stale;this.deviceTimeUs=deviceTimeUs;this.motorEnabled=motorEnabled;
        if(setting!=null&&setting.optLong("request_seq",0)>=acceptedSeq)current=setting;
        render();
    }
    void accepted(JSONObject response){
        long sequence=response.optLong("request_seq",0);if(sequence<acceptedSeq)return;
        pending=false;error="";acceptedSeq=sequence;
        JSONObject setting=response.optJSONObject("pitch_setting");if(setting!=null)current=setting;
        render();
    }
    void failed(String reason){pending=false;error=reason;render();}
    void connectionLost(String reason){online=false;stale=true;if(pending){pending=false;error=reason;}render();}
    private static String angle(double number){return Double.isFinite(number)?String.format(Locale.ROOT,"%.1f°",number):"—";}
    private static void text(TextView view,String next){if(!next.contentEquals(view.getText()))view.setText(next);}
    private void render(){
        double saved=saved();boolean ready=ready();minus.setEnabled(ready&&saved>5);plus.setEnabled(ready&&saved<85);
        String measured="—",detail,shortState;
        if(current!=null){double actual=current.optDouble("measured_deg",Double.NaN);long age=deviceTimeUs-current.optLong("feedback_timestamp_us",0);
            if(!stale&&current.optBoolean("feedback_valid")&&Double.isFinite(actual)&&deviceTimeUs>0&&age>=-250000&&age<=1000000)measured=angle(actual);}
        if(!online){shortState="等待连接";detail="连接后可设置，保存后下次开机沿用";}
        else if(stale||current==null){shortState="等待状态";detail="状态已过期，等待重新接收";}
        else if(pending){shortState="保存中…";detail="正在保存，请等待机器人确认";}
        else if(!motorEnabled){shortState="转头已停用";detail="启用转头后可调整";}
        else if(!error.isEmpty()){shortState="设置未确认";detail=error;}
        else if(current.optInt("storage_error",0)!=0){shortState="存储异常";detail=current.optBoolean("persisted")?"存储异常，保留此前保存值":"存储读取异常，当前使用默认值";}
        else{String s=current.optString("state");shortState=moving()?"调整中…":s.equals("feedback_unavailable")?"反馈不可用":s.equals("trajectory_timeout")?"调整超时":s.equals("start_rejected")||s.equals("target_unavailable")?"未执行":"每次 5°";detail=stateText(s);}
        text(value,"仰角 "+angle(saved)+"\n"+shortState);
        String description="抬头角度。"+(current!=null&&!current.optBoolean("persisted")?"默认 ":"已保存 ")+angle(saved)+"，实测 "+measured+"。"+detail+"。范围 5 至 85 度，每次增减 5 度，到边界停止。";
        if(!description.contentEquals(getContentDescription()==null?"":getContentDescription()))setContentDescription(description);
    }
    static String stateText(String state){
        switch(state){
            case "moving":return "正在调整；保存值用于下次开机";
            case "trajectory_complete":return "指令轨迹已结束；实际角度请看实测";
            case "queued":case "waiting_feedback":return "等待有效位置反馈后调整";
            case "feedback_unavailable":return "位置反馈不可用，本次未完成调整";
            case "trajectory_timeout":return "本次调整超时，未确认到位";
            case "cancelled":return "本次调整已取消，保存值保留";
            case "start_rejected":return "当前角度无法安全开始，本次未调整";
            case "target_unavailable":return "当前目标不可执行，本次未调整";
            default:return "已保存，等待执行状态";
        }
    }
}
