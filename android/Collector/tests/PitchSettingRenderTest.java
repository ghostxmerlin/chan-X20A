package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.view.View;
import android.widget.Button;
import org.json.JSONObject;
import java.io.FileOutputStream;

final class PitchSettingRenderTest {
    private static void check(boolean yes,String why){if(!yes)throw new AssertionError(why);}
    private static JSONObject setting(double degrees,long seq,String state)throws Exception{return new JSONObject().put("configured_deg",degrees).put("persisted",true).put("request_seq",seq).put("state",state).put("feedback_valid",false);}
    static void render(Context context,String destination)throws Exception{
        int[] requests={0};double[] requested={0};
        PitchSettingView view=new PitchSettingView(context,angle->{requests[0]++;requested[0]=angle;return true;});
        Button minus=view.findViewWithTag("pitch_minus"),plus=view.findViewWithTag("pitch_plus");
        JSONObject s=setting(24.5,0,"trajectory_complete");
        view.update(s,true,false,1000000,true);
        check(requests[0]==0,"reading a saved angle must not move the robot");
        plus.performClick();check(requests[0]==1&&requested[0]==29.5,"plus immediately applies exactly five degrees");
        plus.performClick();minus.performClick();check(requests[0]==1,"pending request cannot submit twice in either direction");
        s=setting(29.5,1,"waiting_feedback");view.accepted(new JSONObject().put("request_seq",1).put("pitch_setting",s));
        String text=view.getContentDescription().toString();check(text.contains("已保存 29.5°")&&text.contains("实测 —")&&text.contains("等待有效位置反馈"),"saved/actual/execution remain distinct");
        minus.performClick();check(requests[0]==1&&!minus.isEnabled(),"acknowledgment does not bypass trajectory lock");
        view.update(setting(24.5,0,"trajectory_complete"),true,false,1100000,true);minus.performClick();check(requests[0]==1,"late old telemetry cannot unlock the current motion");
        s.put("state","moving");view.update(s,true,false,1200000,true);check(!plus.isEnabled(),"moving remains locked");
        s.put("state","trajectory_complete");view.update(s,true,false,1300000,true);
        check(view.getContentDescription().toString().contains("实际角度请看实测"),"trajectory end must not claim measured arrival");
        minus.performClick();check(requests[0]==2&&requested[0]==24.5,"minus applies exactly five degrees after the previous trajectory");
        view.accepted(new JSONObject().put("request_seq",2).put("pitch_setting",setting(24.5,2,"trajectory_complete")));
        s=setting(24.5,2,"trajectory_complete").put("feedback_valid",true).put("feedback_timestamp_us",1290000).put("measured_deg",24.1);
        view.update(s,true,false,1300000,true);check(view.getContentDescription().toString().contains("实测 24.1°"),"fresh physical feedback shown");
        view.update(s,true,false,2500000,true);check(view.getContentDescription().toString().contains("实测 —"),"stale physical feedback not fabricated");
        for(int mode=0;mode<3;mode++){
            view.update(s,mode!=0,mode==1,2500000,mode!=2);plus.performClick();minus.performClick();check(requests[0]==2&&!plus.isEnabled()&&!minus.isEnabled(),"offline/stale/stopped must reject even synthetic clicks");
        }
        view.update(setting(84.5,2,"trajectory_complete"),true,false,2600000,true);plus.performClick();check(requested[0]==85,"upper boundary clamps safely");
        view.accepted(new JSONObject().put("request_seq",3).put("pitch_setting",setting(85,3,"trajectory_complete")));int count=requests[0];plus.performClick();check(requests[0]==count&&!plus.isEnabled(),"upper limit cannot be exceeded");
        view.update(setting(5.5,3,"trajectory_complete"),true,false,2700000,true);minus.performClick();check(requested[0]==5,"lower boundary clamps safely");
        view.accepted(new JSONObject().put("request_seq",4).put("pitch_setting",setting(5,4,"trajectory_complete")));count=requests[0];minus.performClick();check(requests[0]==count&&!minus.isEnabled(),"lower limit cannot be exceeded");
        for(double invalid:new double[]{-1,90}){view.update(setting(invalid,4,"trajectory_complete"),true,false,2800000,true);plus.performClick();minus.performClick();check(requests[0]==count,"invalid saved configuration cannot create a motion");}
        view.reset();view.update(setting(24.5,0,"trajectory_complete"),true,false,3000000,true);plus.performClick();view.connectionLost("连接中断，设置未确认");check(!plus.isEnabled(),"connection loss does not automatically resend");
        view.reset();view.update(setting(24.5,0,"trajectory_complete"),true,false,3000000,true);
        float d=context.getResources().getDisplayMetrics().density;int width=Math.round(208*d),height=Math.round(44*d);
        view.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(height,View.MeasureSpec.EXACTLY));view.layout(0,0,width,height);
        Bitmap image=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(image);canvas.drawColor(HudTheme.BG);view.draw(canvas);
        try(FileOutputStream file=new FileOutputStream(destination)){check(image.compress(Bitmap.CompressFormat.PNG,100,file),"PNG encode");}image.recycle();
        System.out.println("PASS: native 5-degree step, immediate single request, trajectory lock, old acknowledgment/telemetry, stale/offline/stopped, angle limits and measured/saved distinction");
    }
}
