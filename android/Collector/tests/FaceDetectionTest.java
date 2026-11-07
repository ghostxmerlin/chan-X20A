package dev.stackchan.collector;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.os.SystemClock;
import android.view.View;
import java.io.File;
import java.io.FileOutputStream;

final class FaceDetectionTest {
    private static void check(boolean ok,String why){if(!ok)throw new AssertionError(why);}
    static void render(Context context,File file)throws Exception{
        String header="{\"epoch\":2,\"frame_seq\":7,\"inference_us\":50000,\"boxes\":[[250,250,750,750,900]]}";
        FaceBoxes boxes=FaceBoxes.parse(header,7);check(boxes.boxes.length==1,"valid box");
        check(FaceBoxes.parse(header,8)==FaceBoxes.NONE,"other frame rejected");
        check(FaceBoxes.parse(header.replace("750,750","250,750"),7)==FaceBoxes.NONE,"empty box rejected");
        check(FaceBoxes.parse(header.replace("900","1001"),7)==FaceBoxes.NONE,"score range rejected");
        check(FaceBoxes.parse(header.replace("250,250","-1,250"),7)==FaceBoxes.NONE,"negative bounds rejected");
        check(FaceBoxes.parse(header.replace("250,250","250.5,250"),7)==FaceBoxes.NONE,"fractional wire coordinate rejected");
        check(FaceBoxes.parse(null,7)==FaceBoxes.NONE,"old firmware supported");
        FaceCameraView view=new FaceCameraView(context);view.setBackgroundColor(0xff000000);
        view.measure(View.MeasureSpec.makeMeasureSpec(400,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(400,View.MeasureSpec.EXACTLY));view.layout(0,0,400,400);
        Bitmap source=Bitmap.createBitmap(320,240,Bitmap.Config.ARGB_8888);source.eraseColor(0xff000000);
        view.show(source,boxes,SystemClock.elapsedRealtime());view.detection(true,2);check(view.visibleBoxes()==1,"matching enabled epoch");
        Bitmap rendered=Bitmap.createBitmap(400,400,Bitmap.Config.ARGB_8888);view.draw(new Canvas(rendered));
        check((rendered.getPixel(110,125)&0xffffff)!=0,"letterbox offset applied");
        check((rendered.getPixel(110,100)&0xffffff)==0,"box not stretched to viewport");
        try(FileOutputStream out=new FileOutputStream(file)){rendered.compress(Bitmap.CompressFormat.PNG,100,out);}
        view.detection(true,3);check(view.visibleBoxes()==0,"OFF/ON epoch hides old result");
        view.detection(false,2);check(view.visibleBoxes()==0,"OFF immediately hides boxes");
        view.detection(true,2);view.show(source,boxes,SystemClock.elapsedRealtime()-1600);check(view.visibleBoxes()==0,"stopped frame expires");
        view.show(source,FaceBoxes.NONE,SystemClock.elapsedRealtime());check(view.visibleBoxes()==0,"new frame cannot inherit previous boxes");
        rendered.recycle();source.recycle();
    }
}
