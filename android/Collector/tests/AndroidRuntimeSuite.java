package dev.stackchan.collector;
import android.app.Instrumentation;
import android.os.Bundle;
import java.io.File;

/** Isolated tests use only this APK's data; the hud suite opens its unbound test Activity. */
public final class AndroidRuntimeSuite extends Instrumentation {
    private boolean controlOnly,uiOnly,hudOnly;
    @Override public void onCreate(Bundle arguments){super.onCreate(arguments);controlOnly=arguments!=null&&"control".equals(arguments.getString("suite"));uiOnly=arguments!=null&&"ui".equals(arguments.getString("suite"));hudOnly=arguments!=null&&"hud".equals(arguments.getString("suite"));start();}
    @Override public void onStart(){
        Bundle results=new Bundle();
        try{
            if(hudOnly){HudLayoutTest.run(this,getTargetContext().getFilesDir());results.putString("stream","PASS: isolated HUD layout at 800/640dp and 4:3 foldable proportions, complete camera viewport and secondary menu navigation\n");finish(-1,results);return;}
            if(!uiOnly)CollectorControlTest.run();
            if(controlOnly){results.putString("stream","PASS: standalone control, stop/enable, reboot, reconnect and legacy lease compatibility\n");finish(-1,results);return;}
            File files=getTargetContext().getFilesDir();
            TelemetryDisplayTest.run();
            if(!uiOnly)RecorderIntegrationTest.main(new String[]{new File(files,"synthetic-sessions").getAbsolutePath()});
            Throwable[] error={null};
            runOnMainSync(()->{try{FaceDetectionTest.render(getTargetContext(),new File(files,"face-boxes.png"));ChartRenderTest.render(getTargetContext(),new File(files,"chart-render.png").getAbsolutePath());PitchSettingRenderTest.render(getTargetContext(),new File(files,"pitch-setting.png").getAbsolutePath());AudioWaveformRenderTest.render(getTargetContext(),new File(files,"audio-waveform.png").getAbsolutePath());MagneticFieldRenderTest.render(getTargetContext(),new File(files,"magnetic-field.png").getAbsolutePath());}catch(Throwable failure){error[0]=failure;}});
            if(error[0]!=null)throw new AssertionError("Chart render",error[0]);
            results.putString("stream",uiOnly?"PASS: native offscreen chart, audio waveform and pitch rendering (synthetic data only)\n":"PASS: recorder integration and native offscreen chart rendering (synthetic data only)\n");finish(-1,results);
        }catch(Throwable failure){results.putString("stream","FAIL: "+android.util.Log.getStackTraceString(failure));finish(1,results);}
    }
}
