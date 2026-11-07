package dev.stackchan.collector;

import android.content.Context;
import android.view.View;
import android.view.ViewGroup;

/** On wide HUDs controls occupy the central gap; narrow windows use one extra row. */
final class HomeParameterBar extends ViewGroup {
    private boolean wide;
    HomeParameterBar(Context context,View... children){super(context);for(View child:children)addView(child);}
    private int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
    private void measureChildAt(int index,int width,int height){getChildAt(index).measure(MeasureSpec.makeMeasureSpec(Math.max(0,width),MeasureSpec.EXACTLY),MeasureSpec.makeMeasureSpec(height,index==1||index==2||index==5?MeasureSpec.EXACTLY:MeasureSpec.AT_MOST));}
    @Override protected void onMeasure(int widthSpec,int heightSpec){
        int width=MeasureSpec.getSize(widthSpec);wide=width>=dp(800);
        // Child order: parameters, pitch, monitor, environment, connection, menu.
        int left=width-dp(wide?558:338);
        measureChildAt(0,left,dp(68));measureChildAt(1,dp(208),dp(44));measureChildAt(2,dp(208),dp(44));
        measureChildAt(3,dp(180),dp(68));measureChildAt(4,dp(90),dp(68));measureChildAt(5,dp(44),dp(44));
        setMeasuredDimension(width,dp(wide?92:116));
    }
    private void place(int index,int x,int y){View v=getChildAt(index);v.layout(x,y,x+v.getMeasuredWidth(),y+v.getMeasuredHeight());}
    private void centered(int index,int x,int height){place(index,x,(height-getChildAt(index).getMeasuredHeight())/2);}
    @Override protected void onLayout(boolean changed,int l,int t,int r,int b){
        int gap=dp(8),left=getChildAt(0).getMeasuredWidth(),row=dp(wide?92:68);centered(0,0,row);
        int x=left+gap;
        if(wide){place(1,x,0);place(2,x,dp(48));x+=dp(208)+gap;}
        else{place(1,0,dp(72));place(2,dp(220),dp(72));}
        centered(3,x,row);x+=dp(180)+gap;centered(4,x,row);x+=dp(90)+gap;centered(5,x,row);
    }
    @Override protected LayoutParams generateDefaultLayoutParams(){return new LayoutParams(LayoutParams.WRAP_CONTENT,LayoutParams.WRAP_CONTENT);}
}
