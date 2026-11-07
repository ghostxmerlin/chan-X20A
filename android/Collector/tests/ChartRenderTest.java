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
        context.setTheme(android.R.style.Theme_Material_NoActionBar);
        float d=context.getResources().getDisplayMetrics().density;int w=Math.round(420*d),h=Math.round(440*d);
        Bitmap bitmap=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888);Canvas c=new Canvas(bitmap);c.drawColor(HudTheme.BG);
        Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);p.setColor(HudTheme.TEXT);p.setTextSize(14*d);c.drawText("模拟数据 · 图表组件离屏渲染",14*d,25*d,p);c.translate(14*d,35*d);
        MotionHistory history=new MotionHistory();
        for(int i=0;i<=400;i++){double t=i/50.0;history.add("synthetic",i+1,1000000+i*20000L,true,new double[]{Math.sin(t*5)*2,9.8+Math.sin(t*10)*1.1,Math.cos(t*5)*1.4,Math.sin(t)*21,Math.cos(t*1.7)*9,Math.sin(t*2.1)*12});}
        MotionChart accel=new MotionChart(context,history,false),gyro=new MotionChart(context,history,true);accel.refresh(false);gyro.refresh(false);
        draw(accel,c,w-Math.round(28*d),Math.round(130*d),0);draw(gyro,c,w-Math.round(28*d),Math.round(130*d),Math.round(140*d));
        JSONObject remote=new JSONObject("{\"last\":{\"yaw_deg\":25,\"pitch_deg\":55,\"speed\":600,\"rssi_dbm\":-58},\"motor\":{\"feedback_age_ms\":20,\"yaw\":{\"valid\":true,\"angle_deg\":15},\"pitch\":{\"valid\":true,\"angle_deg\":48}}}");
        remote.put("pitch_control_enabled",false);
        remote.put("pitch_input_role","home_reaction");
        remote.put("nod_trigger","joystick_center_v1");
        remote.put("sender_center_supported",false);
        remote.getJSONObject("last").put("auxiliary",true);
        remote.getJSONObject("motor").getJSONObject("pitch").put("angle_deg",12.8);
        RemoteView rv=new RemoteView(context);rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        String description=rv.getContentDescription().toString();
        if(!description.contains("上下输入触发表情")||!description.contains("机器人支持摇杆中心点头")||!description.contains("遥控输入")||!description.contains("辅助按钮状态 1")||!description.contains("中心点头需升级遥控器"))throw new AssertionError("receiver capabilities must not imply an upgraded center-capable sender");
        if(description.contains("按下")||description.contains("松开")||description.contains("状态切换触发点头"))throw new AssertionError("auxiliary toggle state must not be a nod trigger");
        remote.put("sender_center_supported",true);rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        description=rv.getContentDescription().toString();
        if(!description.contains("已收到中心按压扩展协议")||description.contains("需升级遥控器"))throw new AssertionError("an observed extension sender should clear the upgrade hint");
        remote.put("center_press_supported",true);remote.put("nod_trigger","none");rv.update(remote,false);
        c.save();p.setColor(HudTheme.BG);p.setStyle(Paint.Style.FILL);c.drawRect(0,285*d,w,381*d,p);c.restore();
        draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        description=rv.getContentDescription().toString();
        if(!description.contains("中心仅记录，尚未绑定动作")||!description.contains("已收到中心按压扩展协议")||description.contains("点头")||description.contains("需升级遥控器"))throw new AssertionError("center protocol support with no action must stay record-only");
        try(FileOutputStream out=new FileOutputStream(destination)){if(!bitmap.compress(Bitmap.CompressFormat.PNG,100,out))throw new AssertionError("PNG encode");}
        remote.put("sender_center_supported",false);rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        description=rv.getContentDescription().toString();
        if(!description.contains("中心仅记录，尚未绑定动作")||!description.contains("等待中心按压扩展协议")||description.contains("已收到")||description.contains("点头"))throw new AssertionError("unassigned center capability must not imply an observed extension sender");
        remote.put("center_input_role","display_toggle");remote.put("sender_center_supported",true);rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        description=rv.getContentDescription().toString();
        if(!description.contains("中心切换显示屏开关")||!description.contains("已收到中心按压扩展协议")||description.contains("仅记录")||description.contains("点头"))throw new AssertionError("display binding must take precedence over the disabled nod binding");
        remote.put("sender_center_supported",false);rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        description=rv.getContentDescription().toString();
        if(!description.contains("中心切换显示屏开关")||!description.contains("等待中心按压扩展协议")||description.contains("已收到"))throw new AssertionError("display capability alone must not imply an observed extension sender");
        remote.put("center_input_role","future_action");rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        if(rv.getContentDescription().toString().contains("中心仅记录"))throw new AssertionError("unknown assigned action must not be mislabeled record-only");
        remote.remove("center_input_role");
        remote.put("center_press_supported",false);rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        if(rv.getContentDescription().toString().contains("中心"))throw new AssertionError("none without center capability must not advertise center support");
        remote.put("nod_trigger","auxiliary_toggle");rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        if(rv.getContentDescription().toString().contains("点头"))throw new AssertionError("auxiliary toggle must not advertise a center nod");
        remote.remove("nod_trigger");rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        if(rv.getContentDescription().toString().contains("点头"))throw new AssertionError("reaction support alone must not imply a nod trigger");
        remote.remove("pitch_input_role");rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        if(!rv.getContentDescription().toString().contains("上下输入仅记录、不驱动舵机"))throw new AssertionError("older yaw-only firmware must retain its behavior label");
        remote.remove("pitch_control_enabled");rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        description=rv.getContentDescription().toString();
        if(description.contains("不驱动舵机")||!description.contains("遥控目标")||!description.contains("辅助按钮状态 1"))throw new AssertionError("legacy firmware must retain both-axis labels and report the auxiliary toggle state");
        remote.getJSONObject("last").put("auxiliary",false);rv.update(remote,false);draw(rv,c,w-Math.round(28*d),Math.round(96*d),Math.round(285*d));
        if(!rv.getContentDescription().toString().contains("辅助按钮状态 0"))throw new AssertionError("false auxiliary value must be labeled state 0");
        int cw=Math.round(182*d),ch=Math.round(160*d);
        Bitmap compact=Bitmap.createBitmap(cw,ch,Bitmap.Config.ARGB_8888);Canvas compactCanvas=new Canvas(compact);compactCanvas.drawColor(HudTheme.BG);
        draw(accel,compactCanvas,cw,Math.round(75*d),0);draw(gyro,compactCanvas,cw,Math.round(75*d),Math.round(85*d));
        int black=0,green=0;
        for(int y=0;y<ch;y++)for(int x=0;x<cw;x++){int pixel=compact.getPixel(x,y);int r=(pixel>>16)&255,g=(pixel>>8)&255,b=pixel&255;if((pixel&0xffffff)==0)black++;if(g>120&&g>=r&&g>=b)green++;}
        if(black<cw*ch/2||green<100)throw new AssertionError("compact charts must retain black field and visible green traces/text");
        try(FileOutputStream out=new FileOutputStream(new java.io.File(new java.io.File(destination).getParentFile(),"chart-compact.png"))){compact.compress(Bitmap.CompressFormat.PNG,100,out);}compact.recycle();
        renderCombined(context,new java.io.File(destination).getParentFile(),d);
        System.out.println("PASS: native Android chart components rendered offscreen at "+w+"x"+h+"; synthetic data explicitly labeled");
    }
    private static void renderCombined(Context context,java.io.File folder,float density)throws Exception{
        MotionHistory history=new MotionHistory();
        for(int i=0;i<=300;i++){
            double seconds=i/50.0;
            history.add("combined-synthetic",i+1,1000000+i*20000L,i<190||i>210,
                new double[]{Math.sin(seconds*5)*2,9.8+Math.sin(seconds*9),Math.cos(seconds*5),Math.sin(seconds)*1500,Math.cos(seconds*2)*90,Math.sin(seconds*3)*180});
        }
        MotionHistory.Snapshot samples=history.snapshot();
        if(MotionHistory.WINDOW_US!=4000000L||samples.size()!=201||samples.times[samples.size()-1]-samples.times[0]!=4000000L)throw new AssertionError("display history must retain the inclusive four-second sample window");
        CombinedMotionChart chart=new CombinedMotionChart(context,history);chart.refresh(false);
        for(int[] size:new int[][]{{270,180},{230,140},{236,136}}){
            int width=Math.round(size[0]*density),height=Math.round(size[1]*density);
            Bitmap result=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(result);canvas.drawColor(HudTheme.BG);
            draw(chart,canvas,width,height,0);
            String description=chart.getContentDescription().toString();
            if(!description.contains("共用最近4秒时间轴")||!description.contains("加速度包含重力，单位米每二次方秒")||!description.contains("角速度单位度每秒")||!description.contains("独立量程正负20")||!description.contains("独立量程正负1680"))throw new AssertionError("combined chart must keep acceleration and rotation units and scales distinct");
            if(!description.contains("数据间断，不连接缺失段")||description.contains("过期"))throw new AssertionError("invalid samples should be represented as gaps in a current window");
            int black=0,green=0;
            for(int y=0;y<height;y++)for(int x=0;x<width;x++){int pixel=result.getPixel(x,y),r=(pixel>>16)&255,g=(pixel>>8)&255,b=pixel&255;if((pixel&0xffffff)==0)black++;if(g>120&&g>=r&&g>=b)green++;}
            if(black<width*height/2||green<100)throw new AssertionError("combined plot needs visible green data on a black field");
            String name=size[0]==230?"chart-combined-compact.png":size[0]==236?"chart-combined-home.png":"chart-combined.png";
            try(FileOutputStream out=new FileOutputStream(new java.io.File(folder,name))){result.compress(Bitmap.CompressFormat.PNG,100,out);}
            chart.refresh(true);draw(chart,canvas,width,height,0);
            if(!chart.getContentDescription().toString().contains("数据已过期，当前数值不可用"))throw new AssertionError("stale motion must not advertise current numerical readings");
            chart.refresh(false);result.recycle();
        }
        history.add("combined-synthetic",302,7020000L,false,new double[6]);
        Bitmap result=Bitmap.createBitmap(Math.round(230*density),Math.round(140*density),Bitmap.Config.ARGB_8888);
        draw(chart,new Canvas(result),result.getWidth(),result.getHeight(),0);
        if(!chart.getContentDescription().toString().contains("最新样本不可用"))throw new AssertionError("invalid latest IMU samples must stay unavailable");
        history.clear();chart.refresh(true);draw(chart,new Canvas(result),result.getWidth(),result.getHeight(),0);
        if(!chart.getContentDescription().toString().contains("等待采集")||chart.getContentDescription().toString().contains("过期"))throw new AssertionError("empty history is waiting, not an expired measurement");
        result.recycle();
        verifySideBySide(context,density);
    }
    private static MotionHistory motionFixture(double acceleration,double rotation,int pulseCenter,int gapKind){
        MotionHistory history=new MotionHistory();
        for(int i=0;i<=200;i++){
            // Omitted timestamps and explicit invalid samples must both leave a
            // real gap. Values remain well inside each independent default scale.
            boolean inGap=i>=90&&i<=110;
            if(gapKind==2&&inGap)continue;
            boolean pulse=pulseCenter<0||Math.abs(i-pulseCenter)<=5;
            history.add("layout-fixture",i+1,1000000+i*20000L,!(gapKind==1&&inGap),
                new double[]{pulse?acceleration:0,0,0,pulse?rotation:0,0,0});
        }
        return history;
    }
    private static Bitmap chartBitmap(Context context,MotionHistory history,int width,int height,boolean stale){
        CombinedMotionChart chart=new CombinedMotionChart(context,history);chart.refresh(stale);
        Bitmap bitmap=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);
        draw(chart,new Canvas(bitmap),width,height,0);return bitmap;
    }
    /** Bounds of actual changed pixels: no production geometry is used by these assertions. */
    private static int[] changedBounds(Bitmap before,Bitmap after,int left,int right){
        int minX=right,minY=before.getHeight(),maxX=-1,maxY=-1,count=0;
        for(int y=0;y<before.getHeight();y++)for(int x=left;x<right;x++)if(before.getPixel(x,y)!=after.getPixel(x,y)){
            minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);count++;
        }
        return new int[]{minX,minY,maxX,maxY,count};
    }
    private static int green(Bitmap image,int x,int y){return(image.getPixel(x,y)>>8)&255;}
    /** Locate the raised signal, not removals/rasterization differences on its former zero line. */
    private static int[] raisedTraceBounds(Bitmap zero,Bitmap signal,int left,int right){
        int minX=right,minY=zero.getHeight(),maxX=-1,maxY=-1,count=0;
        for(int y=0;y<zero.getHeight();y++)for(int x=left;x<right;x++)if(green(signal,x,y)-green(zero,x,y)>=64){
            minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);count++;
        }
        return new int[]{minX,minY,maxX,maxY,count};
    }
    private static int[] strongestSignal(Bitmap zero,Bitmap signal,int[] bounds,float density){
        int center=(bounds[0]+bounds[2])/2,from=Math.max(bounds[0],center-Math.round(2*density)),to=Math.min(bounds[2],center+Math.round(2*density));
        int best=-1,bx=0,by=0;
        for(int x=from;x<=to;x++)for(int y=bounds[1];y<=bounds[3];y++){
            int difference=green(signal,x,y)-green(zero,x,y);
            if(difference>best){best=difference;bx=x;by=y;}
        }
        if(best<120)throw new AssertionError("fixture must produce an actual visible measured trace");
        return new int[]{bx,by};
    }
    private static void verifySideBySide(Context context,float density){
        for(int[] size:new int[][]{{230,140},{236,136},{270,180}}){
            int width=Math.round(size[0]*density),height=Math.round(size[1]*density),middle=width/2;
            Bitmap zero=chartBitmap(context,motionFixture(0,0,-1,0),width,height,false);
            Bitmap acceleration=chartBitmap(context,motionFixture(10,0,-1,0),width,height,false);
            Bitmap rotation=chartBitmap(context,motionFixture(0,15,-1,0),width,height,false);
            int[] left=changedBounds(zero,acceleration,0,width),right=changedBounds(zero,rotation,0,width);
            if(left[4]<20||right[4]<20||left[2]>=middle||right[0]<middle)
                throw new AssertionError("acceleration changes must draw only on the left, angular velocity only on the right");
            if(Math.abs(left[1]-right[1])>2*density||Math.abs(left[3]-right[3])>2*density)
                throw new AssertionError("equivalent normalized signals must occupy equal-height plots, not stacked lanes");

            Bitmap both=chartBitmap(context,motionFixture(10,15,-1,0),width,height,false);
            Bitmap largeRotation=chartBitmap(context,motionFixture(10,1500,-1,0),width,height,false);
            if(changedBounds(both,largeRotation,0,middle)[4]!=0||changedBounds(both,largeRotation,middle,width)[4]==0)
                throw new AssertionError("changing rotation range must not rescale or redraw acceleration pixels");
            Bitmap early=chartBitmap(context,motionFixture(10,15,50,0),width,height,false);
            Bitmap late=chartBitmap(context,motionFixture(10,15,150,0),width,height,false);
            double[] fractions=new double[4];
            for(int side=0;side<2;side++){
                int from=side==0?0:middle,to=side==0?middle:width;
                int[] span=raisedTraceBounds(zero,both,from,to),a=raisedTraceBounds(zero,early,from,to),b=raisedTraceBounds(zero,late,from,to);
                if(a[4]==0||b[4]==0)throw new AssertionError("both time-position fixtures must be visible");
                fractions[side*2]=((a[0]+a[2])/2.0-span[0])/(span[2]-span[0]);
                fractions[side*2+1]=((b[0]+b[2])/2.0-span[0])/(span[2]-span[0]);
                if(Math.abs(fractions[side*2]-.25)>.06||Math.abs(fractions[side*2+1]-.75)>.06)
                    throw new AssertionError("one-second and three-second sample positions must use the full common four-second span; size="+size[0]+"x"+size[1]+", side="+side+", positions="+fractions[side*2]+","+fractions[side*2+1]+", span="+java.util.Arrays.toString(span)+", early="+java.util.Arrays.toString(a)+", late="+java.util.Arrays.toString(b)+", all-pixel-early="+java.util.Arrays.toString(changedBounds(zero,early,from,to)));
            }
            System.out.println("Combined motion rendered time positions "+size[0]+"x"+size[1]+": "+java.util.Arrays.toString(fractions));
            if(Math.abs(fractions[0]-fractions[2])>.04||Math.abs(fractions[1]-fractions[3])>.04)
                throw new AssertionError("both plots must show the same device timestamp at the same relative horizontal position");

            Bitmap stale=chartBitmap(context,motionFixture(10,15,-1,0),width,height,true);
            Bitmap invalidGap=chartBitmap(context,motionFixture(10,15,-1,1),width,height,false);
            Bitmap timeGap=chartBitmap(context,motionFixture(10,15,-1,2),width,height,false);
            Bitmap empty=chartBitmap(context,new MotionHistory(),width,height,true);
            for(int[] bounds:new int[][]{left,right}){
                int[] point=strongestSignal(zero,both,bounds,density);int freshGreen=green(both,point[0],point[1]);
                if(green(stale,point[0],point[1])>=freshGreen-40)
                    throw new AssertionError("stale traces must visibly dim in both columns");
                if(green(invalidGap,point[0],point[1])>=freshGreen-60||green(timeGap,point[0],point[1])>=freshGreen-60)
                    throw new AssertionError("invalid samples and timestamp gaps must not be bridged in either plot");
                if(green(empty,point[0],point[1])>=freshGreen-60)
                    throw new AssertionError("empty history must not invent traces");
            }
            for(Bitmap bitmap:new Bitmap[]{zero,acceleration,rotation,both,largeRotation,early,late,stale,invalidGap,timeGap,empty})bitmap.recycle();
        }
    }
}
