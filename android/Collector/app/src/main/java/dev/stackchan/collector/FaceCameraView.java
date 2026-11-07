package dev.stackchan.collector;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.SystemClock;
import android.widget.ImageView;

/** Green corner brackets follow the FIT_CENTER image, including letterboxing. */
final class FaceCameraView extends ImageView {
    private final Paint ink=new Paint(Paint.ANTI_ALIAS_FLAG);
    private FaceBoxes faces=FaceBoxes.NONE;
    private long receivedMs=0,allowedEpoch=-1;
    private boolean enabled=false;
    FaceCameraView(Context context){super(context);setScaleType(ScaleType.FIT_CENTER);ink.setColor(HudTheme.ACCENT);ink.setStrokeWidth(2*getResources().getDisplayMetrics().density);ink.setStyle(Paint.Style.STROKE);}
    void show(Bitmap bitmap,FaceBoxes value,long received){setImageBitmap(bitmap);faces=value;receivedMs=received;invalidate();}
    void detection(boolean allowed,long epoch){enabled=allowed;allowedEpoch=epoch;invalidate();}
    int visibleBoxes(){return enabled&&faces.epoch==allowedEpoch&&receivedMs>0&&SystemClock.elapsedRealtime()-receivedMs<1500?faces.boxes.length:0;}
    @Override protected void onDraw(Canvas canvas){
        super.onDraw(canvas);if(visibleBoxes()==0||getDrawable()==null)return;
        int save=canvas.save();canvas.clipRect(getPaddingLeft(),getPaddingTop(),getWidth()-getPaddingRight(),getHeight()-getPaddingBottom());
        float width=getDrawable().getIntrinsicWidth(),height=getDrawable().getIntrinsicHeight();
        for(int[] box:faces.boxes){
            RectF r=new RectF(width*box[0]/1000f,height*box[1]/1000f,width*box[2]/1000f,height*box[3]/1000f);
            getImageMatrix().mapRect(r);r.offset(getPaddingLeft(),getPaddingTop());
            float corner=Math.min(r.width(),r.height())*.22f;
            canvas.drawLine(r.left,r.top,r.left+corner,r.top,ink);canvas.drawLine(r.left,r.top,r.left,r.top+corner,ink);
            canvas.drawLine(r.right-corner,r.top,r.right,r.top,ink);canvas.drawLine(r.right,r.top,r.right,r.top+corner,ink);
            canvas.drawLine(r.left,r.bottom-corner,r.left,r.bottom,ink);canvas.drawLine(r.left,r.bottom,r.left+corner,r.bottom,ink);
            canvas.drawLine(r.right,r.bottom-corner,r.right,r.bottom,ink);canvas.drawLine(r.right-corner,r.bottom,r.right,r.bottom,ink);
        }
        canvas.restoreToCount(save);
    }
}
