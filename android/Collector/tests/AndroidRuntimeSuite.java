package dev.stackchan.collector;
import android.app.Instrumentation;
import android.os.Bundle;
import java.io.File;

/** No Activity, window, screenshot or user input. Tests only this APK's own data and native drawing. */
public final class AndroidRuntimeSuite extends Instrumentation {
    private boolean controlOnly;
    @Override public void onCreate(Bundle arguments){super.onCreate(arguments);controlOnly=arguments!=null&&"control".equals(arguments.getString("suite"));start();}
    @Override public void onStart(){
        Bundle results=new Bundle();
        try{
            CollectorControlTest.run();
            if(controlOnly){results.putString("stream","PASS: standalone control, stop/enable, reboot, reconnect and legacy lease compatibility\n");finish(-1,results);return;}
            File files=getTargetContext().getFilesDir();
            RecorderIntegrationTest.main(new String[]{new File(files,"synthetic-sessions").getAbsolutePath()});
            Throwable[] error={null};
            runOnMainSync(()->{try{ChartRenderTest.render(getTargetContext(),new File(files,"chart-render.png").getAbsolutePath());}catch(Throwable failure){error[0]=failure;}});
            if(error[0]!=null)throw new AssertionError("Chart render",error[0]);
            results.putString("stream","PASS: recorder integration and native offscreen chart rendering (synthetic data only)\n");finish(-1,results);
        }catch(Throwable failure){results.putString("stream","FAIL: "+android.util.Log.getStackTraceString(failure));finish(1,results);}
    }
}
