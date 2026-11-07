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
        motor.put("pitch_target_deg",JSONObject.NULL);
        return new JSONObject().put("boot_id",boot).put("seq",seq).put("timestamp_us",seq*20000).put("imu_samples",batch).put("remote",new JSONObject().put("pitch_control_enabled",false).put("packets",new JSONArray().put(packet)).put("motor",motor));
    }
    private static AudioBlock audio()throws Exception{
        ByteBuffer b=ByteBuffer.allocate(32+1920).order(ByteOrder.LITTLE_ENDIAN);
        b.put(new byte[]{'S','C','A','1'}).putInt(1).putLong(20000).putInt(24000).putInt(480).putShort((short)2).putShort((short)16).putInt(1920);
        return AudioBlock.read(new ByteArrayInputStream(b.array()));
    }
    private static JSONObject centerPacket(int seq,long session,long presses)throws Exception{
        JSONObject packet=telemetry("fixture",seq,new JSONArray(),false).getJSONObject("remote").getJSONArray("packets").getJSONObject(0);
        return packet.put("seq",seq).put("center_protocol","joystick_center_v1").put("sender_boot_nonce",session).put("center_press_seq",presses);
    }
    public static void main(String[] args)throws Exception{
        File root=new File(args[0]);root.mkdirs();CountDownLatch done=new CountDownLatch(1);String[] error={null};
        JSONObject info=new JSONObject().put("device_id","synthetic-fixture").put("boot_id","boot-a");
        SessionRecorder r=new SessionRecorder(root,info,"test-0.3",(folder,failure)->{error[0]=failure;done.countDown();});
        r.telemetry(TelemetryDisplayTest.addSensors(telemetry("boot-a",3,new JSONArray().put(imu(1,true)).put(imu(2,false)).put(imu(3,true)),false),60000));
        JSONObject extended=telemetry("boot-a",5,new JSONArray().put(imu(3,true)).put(imu(4,true)).put(imu(5,true)),true);
        TelemetryDisplayTest.addSensors(extended,80000);
        extended.getJSONObject("magnetometer").put("valid",false).put("field_ut",JSONObject.NULL).put("overflow",true).put("error","overflow");
        extended.getJSONObject("light").put("valid",false).put("lux",JSONObject.NULL).put("saturated",true).put("error","saturated");
        extended.getJSONObject("proximity").put("valid",false).put("saturated",true).put("error","saturated");
        extended.getJSONObject("remote").put("center_press_supported",true).put("nod_trigger","none");
        extended.getJSONObject("remote").getJSONArray("packets").put(centerPacket(2,4294967295L,0)).put(centerPacket(3,4294967295L,4294967295L)).put(centerPacket(4,42,0)).put(centerPacket(4,42,0));
        r.telemetry(extended);
        r.audio("boot-a",audio());r.event("marker","synthetic walking marker");
        JSONObject legacy=telemetry("boot-b",1,new JSONArray().put(imu(1,true)),true);
        legacy.getJSONObject("remote").getJSONArray("packets").getJSONObject(0).put("center_protocol","none");
        r.telemetry(legacy);
        r.stop("headless integration test");check(done.await(8,TimeUnit.SECONDS),"writer timeout");check(error[0]==null,"writer failure: "+error[0]);
        JSONObject manifest=new JSONObject(new String(Files.readAllBytes(new File(r.folder,"manifest.json").toPath()),"UTF-8"));
        check(manifest.getString("status").equals("complete"),"not sealed");check(manifest.getLong("imu_samples")==6,"batch dedup");check(manifest.getLong("remote_packets")==5,"remote dedup/reset");check(manifest.getLong("dropped_queue_items")==0,"queue loss");
        check(manifest.getInt("remote_csv_schema_version")==2&&manifest.getString("remote_note").contains("Receiver capability alone"),"center CSV schema and receiver/sender distinction");
        check(manifest.getString("remote_note").contains("remote.nod_trigger and may be none"),"center input must not imply a bound action");
        List<String> motion=Files.readAllLines(new File(r.folder,"segment-001/motion.csv").toPath());check(motion.size()==6,"motion row count");
        String[] invalid=motion.get(2).split(",",-1);check(invalid.length==10&&invalid[3].equals("false")&&invalid[4].isEmpty()&&invalid[9].isEmpty(),"invalid IMU must be blank");
        List<String> servo=Files.readAllLines(new File(r.folder,"segment-001/servos.csv").toPath());String[] missing=servo.get(1).split(",",-1);check(missing.length==17&&missing[4].isEmpty()&&missing[7].equals("false")&&missing[8].isEmpty(),"feedback may not fabricate zero");
        check(missing[5].isEmpty(),"ignored pitch target must remain blank, never become zero or copied input");
        List<String> remote=Files.readAllLines(new File(r.folder,"segment-001/remote.csv").toPath());
        check(remote.get(1).split(",",-1)[5].equals("45.6"),"reserved vertical input must still be recorded unchanged");
        check(remote.size()==5&&remote.get(0).endsWith(",center_supported,center_protocol,sender_boot_nonce,center_press_seq"),"center fields append to the existing CSV schema");
        String[] oldPacket=remote.get(1).split(",",-1),firstCenter=remote.get(2).split(",",-1),nextCenter=remote.get(3).split(",",-1),newSession=remote.get(4).split(",",-1);
        check(oldPacket.length==15&&oldPacket[11].equals("false")&&oldPacket[12].equals("none")&&oldPacket[13].isEmpty()&&oldPacket[14].isEmpty(),"legacy packets must leave center values missing, not zero");
        check(firstCenter[11].equals("true")&&firstCenter[12].equals("joystick_center_v1")&&firstCenter[13].equals("4294967295")&&firstCenter[14].equals("0"),"extended sender session and zero press baseline must remain exact");
        check(nextCenter[14].equals("4294967295")&&newSession[13].equals("42")&&newSession[14].equals("0"),"unsigned press sequence and changed sender session must remain exact");
        String[] explicitLegacy=Files.readAllLines(new File(r.folder,"segment-002/remote.csv").toPath()).get(1).split(",",-1);
        check(explicitLegacy[11].equals("false")&&explicitLegacy[13].isEmpty()&&explicitLegacy[14].isEmpty(),"explicit legacy protocol retains missing center state");
        JSONObject raw=new JSONObject(Files.readAllLines(new File(r.folder,"segment-001/telemetry.jsonl").toPath()).get(1));
        JSONObject firstRaw=new JSONObject(Files.readAllLines(new File(r.folder,"segment-001/telemetry.jsonl").toPath()).get(0));
        check(firstRaw.getJSONObject("magnetometer").getJSONArray("field_ut").getDouble(1)==-8.5&&firstRaw.getJSONObject("light").getDouble("lux")==123.4&&firstRaw.getJSONObject("proximity").getInt("raw")==2047,"engineering magnetic/lux values and raw proximity must survive recording exactly");
        check(raw.getJSONObject("magnetometer").isNull("field_ut")&&raw.getJSONObject("magnetometer").getJSONArray("raw").getInt(1)==-90&&raw.getJSONObject("magnetometer").getBoolean("overflow"),"invalid magnetic data must retain null engineering values and diagnostic raw data");
        check(raw.getJSONObject("light").isNull("lux")&&raw.getJSONObject("light").getInt("raw_ch0")==2345&&raw.getJSONObject("proximity").getInt("raw")==2047&&!raw.getJSONObject("proximity").getBoolean("valid"),"saturated raw values must remain recorded without being promoted to valid readings");
        check(raw.getJSONObject("remote").getJSONArray("packets").getJSONObject(2).getLong("center_press_seq")==4294967295L,"raw telemetry must preserve center extension fields");
        check(raw.getJSONObject("remote").getString("nod_trigger").equals("none")&&raw.getJSONObject("remote").getBoolean("center_press_supported"),"record-only center inputs must retain receiver capability and unassigned action");
        check(new File(r.folder,"segment-001/audio-001.wav").length()==1964,"PCM WAV seal");check(manifest.getJSONArray("segments").length()==2,"boot segmentation");
        checkReplayCompatibility(r.folder);
        System.out.println("PASS: Android writer, batch dedup, legacy/center CSV schema, unsigned session/press fields, raw telemetry, feedback validity, WAV seal and reboot segmentation");
        System.out.println("Synthetic fixture: "+r.folder.getAbsolutePath());
    }
    private static void checkReplayCompatibility(File folder)throws Exception{
        SessionPlayer player=new SessionPlayer(null,folder);
        try{
            java.lang.reflect.Method load=SessionPlayer.class.getDeclaredMethod("load");load.setAccessible(true);load.invoke(player);
            java.lang.reflect.Field samplesField=SessionPlayer.class.getDeclaredField("sensors");samplesField.setAccessible(true);
            boolean newSensors=false,legacy=false;
            for(Object sample:(List<?>)samplesField.get(player)){
                java.lang.reflect.Field valueField=sample.getClass().getDeclaredField("value");valueField.setAccessible(true);JSONObject value=(JSONObject)valueField.get(sample);
                String rendered=MainActivity.formatTelemetry(value);
                if(value.has("magnetometer")){
                    if(value.getJSONObject("magnetometer").optBoolean("valid")){newSensors=true;check(rendered.contains("12.25")&&rendered.contains("123.4")&&rendered.contains("2047"),"replay must load and display the new raw telemetry groups");}
                    else check(rendered.contains("读数溢出")&&rendered.contains("读数饱和"),"replay must retain invalid sensor state instead of showing old values");
                }
                else{legacy=true;check(rendered.contains("未提供"),"legacy replay cannot fabricate environmental measurements");}
            }
            check(newSensors&&legacy,"actual replay loader must accept both extended and legacy segments");
        }finally{
            java.lang.reflect.Field io=SessionPlayer.class.getDeclaredField("io");io.setAccessible(true);((java.util.concurrent.ExecutorService)io.get(player)).shutdownNow();
        }
    }
}
