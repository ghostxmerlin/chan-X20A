package dev.stackchan.collector;

import android.app.AlertDialog;
import android.app.Instrumentation;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageView;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;

/** Uses an isolated test-package Activity with no binding or robot connection. */
final class HudLayoutTest {
    private static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    private static Object field(Object value,String name)throws Exception{Field f=value.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(value);}
    private static boolean contains(View root,View wanted){if(root==wanted)return true;if(root instanceof ViewGroup){ViewGroup group=(ViewGroup)root;for(int i=0;i<group.getChildCount();i++)if(contains(group.getChildAt(i),wanted))return true;}return false;}
    private static boolean hasScroll(View root){if(root instanceof ScrollView)return true;if(root instanceof ViewGroup){ViewGroup group=(ViewGroup)root;for(int i=0;i<group.getChildCount();i++)if(hasScroll(group.getChildAt(i)))return true;}return false;}
    private static boolean hasText(View root,String text){if(root instanceof TextView&&text.contentEquals(((TextView)root).getText()))return true;if(root instanceof ViewGroup){ViewGroup group=(ViewGroup)root;for(int i=0;i<group.getChildCount();i++)if(hasText(group.getChildAt(i),text))return true;}return false;}
    private static View tagged(View root,String tag){View result=root.findViewWithTag(tag);check(result!=null,"missing "+tag);return result;}
    private static AlertDialog visibleDialog(MainActivity activity)throws Exception{
        for(Field f:MainActivity.class.getDeclaredFields())if(AlertDialog.class.isAssignableFrom(f.getType())){f.setAccessible(true);AlertDialog d=(AlertDialog)f.get(activity);if(d!=null&&d.isShowing())return d;}
        throw new AssertionError("menu did not open");
    }
    private static void assertInside(View view,View ancestor,String label){
        int[] position={0,0},base={0,0};view.getLocationOnScreen(position);ancestor.getLocationOnScreen(base);
        check(view.getWidth()>0&&view.getHeight()>0,label+" has no space");
        check(position[0]>=base[0]&&position[1]>=base[1]&&position[0]+view.getWidth()<=base[0]+ancestor.getWidth()+1&&position[1]+view.getHeight()<=base[1]+ancestor.getHeight()+1,label+" clipped outside home");
    }
    static void run(Instrumentation instrumentation,File files)throws Exception{
        Intent intent=new Intent().setClassName(instrumentation.getTargetContext(),MainActivity.class.getName()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        MainActivity activity=(MainActivity)instrumentation.startActivitySync(intent);
        Throwable[] error={null};
        instrumentation.runOnMainSync(()->{try{
            ViewGroup content=activity.findViewById(android.R.id.content);View root=content.getChildAt(0);
            check(field(activity,"client")==null,"isolated UI test must have no robot connection");
            check(!hasScroll(root),"first-person home must fit without scrolling");
            check(!hasText(root,"机器人第一视角"),"home must not reserve space for the removed title");
            check(tagged(root,"home_audio") instanceof AudioLevelView,"home audio must be an instantaneous level meter");
            View history=(View)field(activity,"sessionList");check(!contains(root,history),"record list must not be attached to home");
            ImageView camera=(ImageView)tagged(root,"home_camera");check(camera.getScaleType()==ImageView.ScaleType.FIT_CENTER,"whole camera field must remain visible");
            check(tagged(root,"home_motion") instanceof CombinedMotionChart,"six axes must share one chart view");
            check(root.findViewWithTag("home_accel")==null&&root.findViewWithTag("home_gyro")==null,"home must not retain separate axis charts");
            check(MotionHistory.WINDOW_US==4000000L,"home motion window must be four seconds");
            check(!contains(root,(View)field(activity,"imuMeta")),"detailed IMU rate/coordinates should move to robot diagnostics without widening the footer");
            TextView environment=(TextView)tagged(root,"home_environment");
            org.json.JSONObject synthetic=TelemetryDisplayTest.addSensors(new org.json.JSONObject().put("timestamp_us",2000000),1990000);synthetic.getJSONObject("light").put("lux",65535.9);
            environment.setText(MainActivity.compactEnvironment(synthetic,false));
            ((MagneticFieldView)tagged(root,"home_magnetic")).refresh(synthetic,false,0);
            check(environment.getTextSize()/activity.getResources().getDisplayMetrics().scaledDensity>=16,"environment text must be large enough for the requested HUD");
            for(String tag:new String[]{"home_camera","home_audio","home_motion","home_parameters","home_environment","home_magnetic","home_menu","home_pitch","home_monitor","home_face","pitch_minus","pitch_plus"})assertInside(tagged(root,tag),root,tag);
            int originalWidth=root.getWidth(),originalHeight=root.getHeight();float density=activity.getResources().getDisplayMetrics().density;
            for(int[] size:new int[][]{{960,450},{800,450},{640,360},{640,480}}){
                int widthDp=size[0];String label=size[1]==480?"fold":String.valueOf(widthDp);
                int w=Math.round(widthDp*density),h=Math.round(size[1]*density);
                root.measure(View.MeasureSpec.makeMeasureSpec(w,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(h,View.MeasureSpec.EXACTLY));root.layout(0,0,w,h);
                for(String tag:new String[]{"home_camera","home_audio","home_motion","home_parameters","home_environment","home_magnetic","home_menu","home_pitch","home_monitor","home_face","pitch_minus","pitch_plus"})assertInside(tagged(root,tag),root,tag);
                check(environment.getLayout()!=null&&environment.getLayout().getLineCount()==2,"environment measurements must stay in the existing two-row footer");
                for(int line=0;line<environment.getLayout().getLineCount();line++)check(environment.getLayout().getEllipsisCount(line)==0,"lux/raw values must not be clipped at narrow home widths");
                View audio=tagged(root,"home_audio"),motion=tagged(root,"home_motion"),magnetic=tagged(root,"home_magnetic");
                check(motion.getHeight()>=Math.round(80*density),"narrow home must retain a drawable six-axis plot");
                int[] ap={0,0},cp={0,0},mp={0,0},bp={0,0};audio.getLocationOnScreen(ap);camera.getLocationOnScreen(cp);motion.getLocationOnScreen(mp);magnetic.getLocationOnScreen(bp);
                check(ap[0]+audio.getWidth()<=cp[0],"instant audio column must sit to the left of the camera");
                check(cp[0]+camera.getWidth()<=mp[0],"signals must remain to the right of the full camera viewport");
                check(bp[1]>=mp[1]+motion.getHeight()&&bp[0]==mp[0],"magnetic display must be below the combined motion panel");
                View parameters=tagged(root,"home_parameters"),menuButton=tagged(root,"home_menu");
                for(String buttonTag:new String[]{"home_monitor","home_face","pitch_minus","pitch_plus"}){View button=tagged(root,buttonTag);check(button.getWidth()>=Math.round(44*density)&&button.getHeight()>=Math.round(44*density),buttonTag+" needs a usable touch target");}
                check(contains(parameters,menuButton)&&contains(parameters,(View)field(activity,"status"))&&contains(parameters,(View)field(activity,"recordState")),"menu and live session state must share the bottom parameter bar");
                check(menuButton.getWidth()>=Math.round(44*density)&&menuButton.getHeight()>=Math.round(44*density),"bottom menu needs a usable touch target");
                int[] cameraPosition={0,0},rootPosition={0,0};camera.getLocationOnScreen(cameraPosition);root.getLocationOnScreen(rootPosition);
                check(Math.abs(cameraPosition[1]-rootPosition[1]-root.getPaddingTop())<=1,"camera must expand into the former title row without a blank spacer");
                View caption=(View)field(activity,"frameMeta");int footerMargin=((ViewGroup.MarginLayoutParams)parameters.getLayoutParams()).topMargin;
                check(camera.getHeight()>=h-root.getPaddingTop()-root.getPaddingBottom()-parameters.getHeight()-footerMargin-caption.getHeight()-1,"camera must use all height released by the title row");
                ((MagneticFieldView)tagged(root,"home_magnetic")).refresh(synthetic,false,0);
                Bitmap bitmap=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888);root.draw(new Canvas(bitmap));
                check(tagged(root,"home_motion").getContentDescription().toString().contains("共用最近4秒时间轴"),"combined chart must expose its common four-second axis");
                try(FileOutputStream out=new FileOutputStream(new File(files,"hud-layout-"+label+".png"))){bitmap.compress(Bitmap.CompressFormat.PNG,100,out);}bitmap.recycle();
            }
            root.measure(View.MeasureSpec.makeMeasureSpec(originalWidth,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(originalHeight,View.MeasureSpec.EXACTLY));root.layout(0,0,originalWidth,originalHeight);
            tagged(root,"home_menu").performClick();AlertDialog menu=visibleDialog(activity);check(menu.getWindow().getDecorView().findViewWithTag("menu_monitor")==null,"monitor must move out of the menu");tagged(menu.getWindow().getDecorView(),"menu_records").performClick();
            AlertDialog records=visibleDialog(activity);check(contains(records.getWindow().getDecorView(),history),"records submenu must contain history");records.dismiss();
            tagged(root,"home_menu").performClick();menu=visibleDialog(activity);tagged(menu.getWindow().getDecorView(),"menu_robot").performClick();
            AlertDialog robot=visibleDialog(activity);check(contains(root,(View)field(activity,"pitchView")),"pitch control must stay on home when settings opens");robot.dismiss();
            check(!contains(root,history),"returning to home must not attach record history");
            check(field(activity,"client")==null&&field(activity,"recorder")==null,"navigation must not start connection or recording by itself");
        }catch(Throwable t){error[0]=t;}finally{activity.finish();}});
        if(error[0]!=null)throw new AssertionError("HUD layout/navigation",error[0]);
    }
}
