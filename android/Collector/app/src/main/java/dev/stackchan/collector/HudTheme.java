package dev.stackchan.collector;

import android.app.AlertDialog;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.view.Window;
import android.widget.Button;
import android.widget.SeekBar;

/** Shared black/green visual tokens. No changes to data, controls, or activity lifecycle. */
public final class HudTheme {
    public static final int BG=0xff000000;
    public static final int TEXT=0xff70ff8e;
    public static final int ACCENT=0xff36ef72;
    public static final int MUTED=0xff83b892;
    public static final int GRID=0xff174e28;
    public static final int WARNING=0xffd4ff62;
    public static final int AXIS_Z=0xffb4ffc4;
    public static final Typeface REGULAR=Typeface.create("sans-serif",Typeface.NORMAL);
    public static final Typeface MEDIUM=Typeface.create("sans-serif-medium",Typeface.NORMAL);
    private HudTheme(){}

    private static ColorStateList enabledColor(int color){
        return new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled},new int[]{}},new int[]{MUTED,color});
    }
    private static GradientDrawable outline(float density,int color,int width){
        GradientDrawable shape=new GradientDrawable();shape.setColor(BG);
        shape.setCornerRadius(4*density);shape.setStroke(Math.max(1,Math.round(width*density)),color);return shape;
    }
    public static void styleButton(Button button){
        float d=button.getResources().getDisplayMetrics().density;
        StateListDrawable background=new StateListDrawable();
        background.addState(new int[]{-android.R.attr.state_enabled},outline(d,GRID,1));
        background.addState(new int[]{android.R.attr.state_pressed},outline(d,TEXT,2));
        background.addState(new int[]{android.R.attr.state_focused},outline(d,TEXT,2));
        background.addState(new int[]{},outline(d,ACCENT,1));
        button.setBackgroundTintList(null);button.setBackground(background);button.setTextColor(enabledColor(TEXT));
        button.setTypeface(MEDIUM);button.setAllCaps(false);button.setElevation(0);button.setStateListAnimator(null);
        button.setMinWidth(0);button.setMinimumWidth(0);button.setMinHeight(0);button.setMinimumHeight(0);
    }
    public static void styleSeekBar(SeekBar bar){
        bar.setProgressTintList(enabledColor(ACCENT));bar.setSecondaryProgressTintList(ColorStateList.valueOf(GRID));
        bar.setProgressBackgroundTintList(ColorStateList.valueOf(GRID));bar.setThumbTintList(enabledColor(TEXT));
        bar.setBackgroundColor(BG);
    }
    /** Call after show(); content views retain their own state colors. */
    public static void applyDialog(AlertDialog dialog){
        Window window=dialog.getWindow();
        if(window!=null){window.setBackgroundDrawable(new ColorDrawable(BG));window.getDecorView().setBackgroundColor(BG);}
        for(int which:new int[]{AlertDialog.BUTTON_POSITIVE,AlertDialog.BUTTON_NEGATIVE,AlertDialog.BUTTON_NEUTRAL}){
            Button button=dialog.getButton(which);if(button!=null)styleButton(button);
        }
    }
}
