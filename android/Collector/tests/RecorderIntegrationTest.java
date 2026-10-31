package dev.stackchan.collector;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Headless Android-runtime test. Synthetic values only; never reads camera/mic or controls a UI. */
public final class RecorderIntegrationTest {
    private static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    private static JSONObject imu(int seq,boolean valid)throws Exception{
        JSONObject o=new JSONObject().put("seq",seq).put("timestamp_us",seq*20000L).put("valid",valid);
        if(valid)o.put("accel",new JSONArray(new double[]{1,2,9.80665})).put("gyro",new JSONArray(new double[]{4,5,6}));return o;
    }
    private static JSONObject telemetry(String boot,int seq,JSONArray batch,boolean feedback)throws Exception{
        JSONObject packet=new JSONObject().put("seq",1).put("timestamp_us",40000).put("target_id",0).put("yaw_deg",-12.3).put("pitch_deg",45.6).put("speed",600).put("auxiliary",true).put("rssi_dbm",-60).put("channel",6).put("source_mac","00:01:02:03:04:05");
        JSONObject joint=new JSONObject().put("valid",feedback).put("angle_deg",12.5).put("position_raw",500).put("load_raw",2).put("current_raw",3);
        JSONObject motor=new JSONObject().put("timestamp_us",seq*20000).put("feedback_age_ms",10).put("enabled",false).put("applied_seq",0).put("yaw",joint).put("pitch",joint);
        return new JSONObject().put("boot_id",boot).put("seq",seq).put("timestamp_us",seq*20000).put("imu_samples",batch).put("remote",new JSONObject().put("packets",new JSONArray().put(packet)).put("motor",motor));
    }
    private static AudioBlock audio()throws Exception{
        ByteBuffer b=ByteBuffer.allocate(32+1920).order(ByteOrder.LITTLE_ENDIAN);
        b.put(new byte[]{'S','C','A','1'}).putInt(1).putLong(20000).putInt(24000).putInt(480).putShort((short)2).putShort((short)16).putInt(1920);
        return AudioBlock.read(new ByteArrayInputStream(b.array()));
    }
    public static void main(String[] args)throws Exception{
        File root=new File(args[0]);root.mkdirs();CountDownLatch done=new CountDownLatch(1);String[] error={null};
        JSONObject info=new JSONObject().put("device_id","synthetic-fixture").put("boot_id","boot-a");
        SessionRecorder r=new SessionRecorder(root,info,"test-0.3",(folder,failure)->{error[0]=failure;done.countDown();});
        r.telemetry(telemetry("boot-a",3,new JSONArray().put(imu(1,true)).put(imu(2,false)).put(imu(3,true)),false));
        r.telemetry(telemetry("boot-a",5,new JSONArray().put(imu(3,true)).put(imu(4,true)).put(imu(5,true)),true));
        r.audio("boot-a",audio());r.event("marker","synthetic walking marker");
        r.telemetry(telemetry("boot-b",1,new JSONArray().put(imu(1,true)),true));
        r.stop("headless integration test");check(done.await(8,TimeUnit.SECONDS),"writer timeout");check(error[0]==null,"writer failure: "+error[0]);
        JSONObject manifest=new JSONObject(new String(Files.readAllBytes(new File(r.folder,"manifest.json").toPath()),"UTF-8"));
        check(manifest.getString("status").equals("complete"),"not sealed");check(manifest.getLong("imu_samples")==6,"batch dedup");check(manifest.getLong("remote_packets")==2,"remote dedup/reset");check(manifest.getLong("dropped_queue_items")==0,"queue loss");
        List<String> motion=Files.readAllLines(new File(r.folder,"segment-001/motion.csv").toPath());check(motion.size()==6,"motion row count");
        String[] invalid=motion.get(2).split(",",-1);check(invalid.length==10&&invalid[3].equals("false")&&invalid[4].isEmpty()&&invalid[9].isEmpty(),"invalid IMU must be blank");
        List<String> servo=Files.readAllLines(new File(r.folder,"segment-001/servos.csv").toPath());String[] missing=servo.get(1).split(",",-1);check(missing.length==17&&missing[4].isEmpty()&&missing[7].equals("false")&&missing[8].isEmpty(),"feedback may not fabricate zero");
        check(new File(r.folder,"segment-001/audio-001.wav").length()==1964,"PCM WAV seal");check(manifest.getJSONArray("segments").length()==2,"boot segmentation");
        System.out.println("PASS: Android writer, 50Hz-style batch dedup, missing values, remote CSV, actual feedback validity, WAV seal and reboot segmentation");
        System.out.println("Synthetic fixture: "+r.folder.getAbsolutePath());
    }
}
