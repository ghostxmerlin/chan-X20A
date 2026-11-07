package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.view.View;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;

/** Native synthetic-only field rendering; never connects to or controls a robot. */
public final class MagneticFieldRenderTest {
    private static void check(boolean value,String reason){if(!value)throw new AssertionError(reason);}
    private static JSONObject telemetry(long timestamp,double x,double y,double z)throws Exception{
        return new JSONObject().put("timestamp_us",2000000)
            .put("pitch_setting",new JSONObject().put("configured_deg",24.5))
            .put("magnetometer",new JSONObject().put("available",true).put("valid",true).put("error","none")
                .put("timestamp_us",timestamp).put("overflow",false).put("unit","uT")
                .put("field_ut",new JSONArray().put(x).put(y).put(z)));
    }
    private static Bitmap draw(MagneticFieldView view,int width,int height){
        view.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(height,View.MeasureSpec.EXACTLY));
        view.layout(0,0,width,height);Bitmap result=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);view.draw(new Canvas(result));return result;
    }
    private static int arrowPixels(Bitmap image,float d){
        int count=0;for(int y=Math.round(28*d);y<image.getHeight()-Math.round(44*d);y++)
            for(int x=Math.round(8*d);x<Math.min(image.getWidth(),Math.round(94*d));x++)
                if(image.getPixel(x,y)==HudTheme.ACCENT)count++;
        return count;
    }
    private static void assertHidden(MagneticFieldView view,Bitmap image,float d,String reason){
        String description=view.getContentDescription().toString();
        check(description.contains(reason)&&description.contains("XYZ数值及投影箭头不可用"),"invalid values must state reason and hide vector: "+reason);
        check(!description.contains("-205.9")&&arrowPixels(image,d)==0,"invalid/stale field must not retain data or its arrow");
    }
    private static void save(Bitmap image,File path)throws Exception{
        try(FileOutputStream output=new FileOutputStream(path)){check(image.compress(Bitmap.CompressFormat.PNG,100,output),"PNG encode");}
    }
    private static void gutter(Bitmap image,float d){
        int edge=Math.max(1,Math.round(2*d));
        for(int y=0;y<image.getHeight();y++)for(int x=0;x<edge;x++)
            check(image.getPixel(x,y)==HudTheme.BG&&image.getPixel(image.getWidth()-1-x,y)==HudTheme.BG,"field text must not clip at left/right edge");
    }
    public static void render(Context context,String destination)throws Exception{
        float d=context.getResources().getDisplayMetrics().density;
        int width=Math.round(270*d),height=Math.round(180*d),compactWidth=Math.round(236*d),compactHeight=Math.round(136*d);
        long[] elapsed={1000};MagneticFieldView view=new MagneticFieldView(context,()->elapsed[0]);
        JSONObject normal=telemetry(1990000,-205.9,-100.5,-89.4);String original=normal.toString();
        view.refresh(normal,false,0);Bitmap valid=draw(view,width,height);
        String description=view.getContentDescription().toString();
        check(description.contains("X -205.9")&&description.contains("Y -100.5")&&description.contains("Z -89.4")&&description.contains("µT"),"all three real field values and unit");
        check(description.contains("航向未校准")&&description.contains("不表示机器人位置或地理朝向"),"field projection must not pretend to be calibrated heading");
        check(arrowPixels(valid,d)>0,"nonzero valid XY vector needs its projection arrow");gutter(valid,d);
        save(valid,new File(destination));
        Bitmap compact=draw(view,compactWidth,compactHeight);gutter(compact,d);
        check(arrowPixels(compact,d)>0,"compact view must retain its readable projection");
        save(compact,new File(new File(destination).getParentFile(),"magnetic-field-compact.png"));
        valid.recycle();compact.recycle();

        // Receive age is fixed at refresh, but a delayed actual draw must still
        // expire the vector without another network packet or refresh callback.
        elapsed[0]=1989;Bitmap before=draw(view,compactWidth,compactHeight);
        check(arrowPixels(before,d)>0,"999ms total age remains fresh");before.recycle();
        elapsed[0]=1990;Bitmap stale=draw(view,compactWidth,compactHeight);assertHidden(view,stale,d,"数据已过期");stale.recycle();
        elapsed[0]=2000;view.refresh(normal,false,989999);Bitmap boundary=draw(view,compactWidth,compactHeight);
        check(arrowPixels(boundary,d)>0,"one microsecond before exact 1s boundary remains fresh");boundary.recycle();
        view.refresh(normal,false,990000);Bitmap receiveStale=draw(view,compactWidth,compactHeight);assertHidden(view,receiveStale,d,"数据已过期");receiveStale.recycle();
        view.refresh(normal,true,0);Bitmap disconnected=draw(view,compactWidth,compactHeight);assertHidden(view,disconnected,d,"数据已过期");disconnected.recycle();

        JSONObject newer=telemetry(2250000,-205.9,-100.5,-89.4);view.refresh(newer,false,999999);Bitmap allowedFuture=draw(view,compactWidth,compactHeight);
        check(arrowPixels(allowedFuture,d)>0,"async sample up to 250ms newer than top IMU is permitted");allowedFuture.recycle();
        view.refresh(newer,false,1000000);Bitmap futureExpired=draw(view,compactWidth,compactHeight);assertHidden(view,futureExpired,d,"数据已过期");futureExpired.recycle();
        view.refresh(telemetry(2250001,-205.9,-100.5,-89.4),false,0);Bitmap future=draw(view,compactWidth,compactHeight);assertHidden(view,future,d,"时间无效");future.recycle();

        for(String error:new String[]{"i2c","overflow","trim","chip_id"}){
            JSONObject broken=telemetry(1990000,-205.9,-100.5,-89.4);broken.getJSONObject("magnetometer").put("valid",false).put("error",error);
            view.refresh(broken,false,0);Bitmap image=draw(view,compactWidth,compactHeight);
            assertHidden(view,image,d,"i2c".equals(error)?"读取失败":"overflow".equals(error)?"读数溢出":"trim".equals(error)?"补偿参数无效":"未检出传感器");image.recycle();
        }
        JSONObject absent=telemetry(1990000,-205.9,-100.5,-89.4);absent.getJSONObject("magnetometer").put("available",false);
        view.refresh(absent,false,0);Bitmap unavailable=draw(view,compactWidth,compactHeight);assertHidden(view,unavailable,d,"未就绪");unavailable.recycle();
        for(Object invalid:new Object[]{JSONObject.NULL,"12.3",Double.NaN,Double.POSITIVE_INFINITY}){
            final Object bad=invalid;JSONArray vector=new JSONArray(){
                @Override public int length(){return 3;}
                @Override public Object opt(int index){return index==1?bad:-205.9;}
            };
            JSONObject malformed=telemetry(1990000,1,2,3);malformed.getJSONObject("magnetometer").put("field_ut",vector);
            view.refresh(malformed,false,0);Bitmap image=draw(view,compactWidth,compactHeight);assertHidden(view,image,d,"读数无效");image.recycle();
        }
        JSONObject missingTime=telemetry(1990000,1,2,3);missingTime.getJSONObject("magnetometer").remove("timestamp_us");
        view.refresh(missingTime,false,0);Bitmap noTime=draw(view,compactWidth,compactHeight);assertHidden(view,noTime,d,"时间无效");noTime.recycle();
        view.refresh(new JSONObject().put("timestamp_us",2000000),false,0);Bitmap legacy=draw(view,compactWidth,compactHeight);assertHidden(view,legacy,d,"未提供");legacy.recycle();
        view.refresh(telemetry(1990000,0,0,3.5),false,0);Bitmap zero=draw(view,compactWidth,compactHeight);
        check(arrowPixels(zero,d)==0&&view.getContentDescription().toString().contains("XY投影为零"),"zero XY must not invent a direction");zero.recycle();
        view.refresh(telemetry(1990000,1e300,-1e300,1e300),false,0);Bitmap huge=draw(view,compactWidth,compactHeight);
        check(arrowPixels(huge,d)>0&&!view.getContentDescription().toString().contains("NaN"),"finite large vectors must normalize without overflow");gutter(huge,d);huge.recycle();
        check(original.equals(normal.toString())&&normal.getJSONObject("pitch_setting").getDouble("configured_deg")==24.5,"rendering must not mutate telemetry or configured angle");
        System.out.println("PASS: magnetic XYZ/XY projection at 270x180 and 236x136dp; invalid, exact 1s, delayed draw, future tolerance, nonfinite, zero XY and immutable settings (synthetic data only)");
    }
}
