package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;
import org.json.JSONObject;
import java.io.FileOutputStream;

/** Offscreen component rendering, not a screenshot of or interaction with a running application. */
public final class ChartRenderTest {
    private static void draw(View view,Canvas canvas,int width,int height,int top){
        view.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(height,View.MeasureSpec.EXACTLY));view.layout(0,0,width,height);canvas.save();canvas.translate(0,top);view.draw(canvas);canvas.restore();
    }
    public static void render(Context context,String destination)throws Exception{
        context.setTheme(android.R.style.Theme_Material_Light_NoActionBar);
        float d=context.getResources().getDisplayMetrics().density;int w=Math.round(420*d),h=Math.round(440*d);
        Bitmap bitmap=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888);Canvas c=new Canvas(bitmap);c.drawColor(0xfff3f5f2);
        Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);p.setColor(0xff233b4a);p.setTextSize(14*d);c.drawText("模拟数据 · 图表组件离屏渲染",14*d,25*d,p);c.translate(14*d,35*d);
        MotionHistory history=new MotionHistory();
        for(int i=0;i<=400;i++){double t=i/50.0;history.add("synthetic",i+1,1000000+i*20000L,true,new double[]{Math.sin(t*5)*2,9.8+Math.sin(t*10)*1.1,Math.cos(t*5)*1.4,Math.sin(t)*21,Math.cos(t*1.7)*9,Math.sin(t*2.1)*12});}
        MotionChart accel=new MotionChart(context,history,false),gyro=new MotionChart(context,history,true);accel.refresh(false);gyro.refresh(false);
        draw(accel,c,w-Math.round(28*d),Math.round(130*d),0);draw(gyro,c,w-Math.round(28*d),Math.round(130*d),Math.round(140*d));
        JSONObject remote=new JSONObject("{\"last\":{\"yaw_deg\":25,\"pitch_deg\":55,\"speed\":600,\"rssi_dbm\":-58},\"motor\":{\"feedback_age_ms\":20,\"yaw\":{\"valid\":true,\"angle_deg\":15},\"pitch\":{\"valid\":true,\"angle_deg\":48}}}");
        remote.put("pitch_control_enabled",false);
        remote.getJSONObject("motor").getJSONObject("pitch").put("angle_deg",12.8);
        RemoteView rv=new RemoteView(context);rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        if(!rv.getContentDescription().toString().contains("上下输入仅记录、不驱动舵机"))throw new AssertionError("yaw-only input must not be labeled as applied pitch");
        try(FileOutputStream out=new FileOutputStream(destination)){if(!bitmap.compress(Bitmap.CompressFormat.PNG,100,out))throw new AssertionError("PNG encode");}
        remote.remove("pitch_control_enabled");rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        if(rv.getContentDescription().toString().contains("不驱动舵机"))throw new AssertionError("legacy firmware must retain both-axis labels");
        System.out.println("PASS: native Android chart components rendered offscreen at "+w+"x"+h+"; synthetic data explicitly labeled");
    }
}
