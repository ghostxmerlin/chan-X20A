package dev.stackchan.collector;

import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Exercises the actual HTTP client against a local receiver, without robot hardware or credentials. */
final class CollectorControlTest {
    private static final String TOKEN="00000000000000000000000000000000",DEVICE="synthetic-unit";
    private static void check(boolean value,String reason){if(!value)throw new AssertionError(reason);}
    private static void await(java.util.function.BooleanSupplier condition,String reason)throws Exception{
        long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(7);
        while(!condition.getAsBoolean()&&System.nanoTime()<end)Thread.sleep(25);
        check(condition.getAsBoolean(),reason);
    }
    private static final class Receiver implements AutoCloseable {
        final ServerSocket socket=new ServerSocket(0,16,InetAddress.getByName("127.0.0.1"));
        final ExecutorService workers=Executors.newCachedThreadPool();
        final AtomicInteger posts=new AtomicInteger(),telemetry=new AtomicInteger(),challenges=new AtomicInteger(),bearers=new AtomicInteger(),violations=new AtomicInteger(),pitchPosts=new AtomicInteger(),facePosts=new AtomicInteger(),faceAccepted=new AtomicInteger(),faceFailed=new AtomicInteger();
        volatile String proofMode="ok",savedNonce="",savedProof="";
        volatile boolean dropPitchResponse=false,failTelemetry=false,faceEnabled=false,dropFaceResponse=false;
        volatile double configuredPitch=13;
        volatile int pitchStatus=202;
        volatile boolean enabled=true,standalone=true,closed=false;
        volatile String boot="first";
        Receiver()throws Exception{workers.execute(()->{while(!closed)try{
            Socket peer=socket.accept();workers.execute(()->handle(peer));
        }catch(IOException ignored){if(!closed)throw new RuntimeException(ignored);}});}
        void handle(Socket peer){try(Socket connection=peer){
            connection.setSoTimeout(3000);
            BufferedReader in=new BufferedReader(new InputStreamReader(connection.getInputStream(),"UTF-8"));
            String first=in.readLine();if(first==null)return;
            int length=0;String line,authorization="";
            while((line=in.readLine())!=null&&!line.isEmpty()){
                if(line.toLowerCase(Locale.ROOT).startsWith("content-length:"))length=Integer.parseInt(line.substring(15).trim());
                if(line.toLowerCase(Locale.ROOT).startsWith("authorization:"))authorization=line.substring(14).trim();
            }
            boolean challenge=first.startsWith("GET /v1/discovery?");
            if(!authorization.isEmpty()){bearers.incrementAndGet();if(challenge||challenges.get()==0||!("Bearer "+TOKEN).equals(authorization))violations.incrementAndGet();}
            else if(!challenge)violations.incrementAndGet();
            char[] body=new char[length];for(int read=0;read<length;){int n=in.read(body,read,length-read);if(n<0)throw new EOFException();read+=n;}
            int status=200;String result="{}",extra="";
            if(challenge){
                challenges.incrementAndGet();String nonce=first.split(" ")[1].split("nonce=")[1];String id=DEVICE;
                if(proofMode.equals("id"))id="another-unit";
                if(proofMode.equals("nonce"))nonce="11111111111111111111111111111111";
                String proof=DiscoveryProof.sign(TOKEN,id,nonce);
                if(proofMode.equals("bad"))proof="0000000000000000000000000000000000000000000000000000000000000000";
                if(proofMode.equals("replay")){if(savedNonce.isEmpty()){savedNonce=nonce;savedProof=proof;}else{nonce=savedNonce;proof=savedProof;}}
                if(proofMode.equals("redirect")){status=302;extra="Location: http://127.0.0.1:"+socket.getLocalPort()+"/redirected\r\n";}
                result=new JSONObject().put("device_id",id).put("protocol",1).put("nonce",nonce).put("proof",proof).toString();
            }
            else if(first.startsWith("POST /v1/settings/pitch ")){
                pitchPosts.incrementAndGet();status=pitchStatus;
                if(status==202)configuredPitch=new JSONObject(new String(body)).getDouble("angle_deg");
                if(dropPitchResponse)return;
                result=new JSONObject().put("accepted",status==202).put("persisted",status==202).put("request_seq",pitchPosts.get()).put("pitch_setting",new JSONObject().put("configured_deg",configuredPitch).put("persisted",true).put("request_seq",pitchPosts.get()).put("state","waiting_feedback")).toString();
            }
            else if(first.startsWith("POST /v1/settings/face-detection ")){
                faceEnabled=new JSONObject(new String(body)).getBoolean("enabled");facePosts.incrementAndGet();
                if(dropFaceResponse){dropFaceResponse=false;return;}
                result=new JSONObject().put("accepted",true).put("face_detection",new JSONObject().put("enabled",faceEnabled).put("epoch",facePosts.get())).toString();
            }
            else if(first.startsWith("POST /v1/remote ")){enabled=new JSONObject(new String(body)).getBoolean("enabled");posts.incrementAndGet();}
            else if(first.startsWith("GET /v1/info "))result=new JSONObject().put("protocol_version",1).put("device_id",DEVICE).put("boot_id",boot).put("audio_port",socket.getLocalPort()).put("remote",new JSONObject().put("control_mode",standalone?"standalone":"lease")).toString();
            else if(first.startsWith("GET /v1/telemetry")){if(failTelemetry)status=503;result=new JSONObject().put("boot_id",boot).put("seq",telemetry.incrementAndGet()).put("remote",new JSONObject().put("rx_packets",0).put("motor",new JSONObject().put("requested",enabled).put("enabled",enabled))).toString();}
            else status=503;
            byte[] bytes=result.getBytes("UTF-8");OutputStream out=connection.getOutputStream();
            out.write(("HTTP/1.1 "+status+" Test\r\n"+extra+"Content-Type: application/json\r\nContent-Length: "+bytes.length+"\r\nConnection: close\r\n\r\n").getBytes("UTF-8"));out.write(bytes);out.flush();
        }catch(Exception ignored){/* A client can disconnect while its request is outstanding. */}}
        String address(){return "127.0.0.1:"+socket.getLocalPort();}
        CollectorClient client(AtomicInteger samples)throws Exception{return client(samples,new AtomicInteger(),new AtomicInteger());}
        CollectorClient client(AtomicInteger samples,AtomicInteger accepted,AtomicInteger failed)throws Exception{return new CollectorClient(address(),TOKEN,DEVICE,new CollectorClient.Listener(){
            public void info(JSONObject v){} public void status(String s,boolean c){}
            public void frame(String b,long s,long t,int w,int h,byte[] j){}
            public void telemetry(JSONObject v){samples.incrementAndGet();}
            public void audio(String b,AudioBlock a){} public void event(String t,String d){if(t.equals("face_setting_accepted"))faceAccepted.incrementAndGet();if(t.equals("face_setting_failed"))faceFailed.incrementAndGet();if(t.equals("pitch_setting_accepted"))accepted.incrementAndGet();if(t.equals("pitch_setting_failed"))failed.incrementAndGet();}
        });}
        public void close()throws IOException{closed=true;socket.close();workers.shutdownNow();}
    }
    static void run()throws Exception{
        try(Receiver receiver=new Receiver()){
            AtomicInteger samples=new AtomicInteger();CollectorClient client=receiver.client(samples);client.start();
            try{
                await(()->samples.get()>=5,"standalone telemetry");
                check(receiver.posts.get()==0&&receiver.enabled,"connection must not disable the remote");
                check(receiver.challenges.get()>0&&receiver.bearers.get()>0&&receiver.violations.get()==0,"all media/control authentication must follow a credential-free challenge");
                client.setMotorEnabled(false);await(()->receiver.posts.get()==1,"explicit stop");check(!receiver.enabled,"stop applied");
                client.setMotorEnabled(true);await(()->receiver.posts.get()==2,"explicit enable");check(receiver.enabled,"enable applied");
                Thread.sleep(1800);check(receiver.posts.get()==2,"standalone must not renew a lease");
                receiver.boot="second";await(()->"second".equals(client.boot()),"boot change observed");
                Thread.sleep(300);check(receiver.posts.get()==2&&receiver.enabled,"boot refresh must not disable receiver");
            }finally{client.close();}
            samples.set(0);CollectorClient reconnected=receiver.client(samples);reconnected.start();
            try{await(()->samples.get()>=5,"reconnected telemetry");check(receiver.posts.get()==2&&receiver.enabled,"observer reconnection preserves enabled receiver");}finally{reconnected.close();}
        }
        try(Receiver receiver=new Receiver()){
            receiver.standalone=false;AtomicInteger samples=new AtomicInteger();CollectorClient client=receiver.client(samples);client.start();
            try{await(()->samples.get()>=2,"legacy telemetry");client.setMotorEnabled(true);await(()->receiver.posts.get()>=3,"legacy lease renewals retained");}finally{client.close();}
        }
        for(String mode:new String[]{"bad","id","nonce","replay","redirect"})try(Receiver receiver=new Receiver()){
            receiver.proofMode=mode;
            if(mode.equals("replay"))CollectorClient.verifyEndpoint(receiver.address(),TOKEN,DEVICE);
            AtomicInteger samples=new AtomicInteger();CollectorClient client=receiver.client(samples);client.start();
            try{await(()->receiver.challenges.get()>=(mode.equals("replay")?2:1),"challenge attempted: "+mode);Thread.sleep(350);
                check(receiver.bearers.get()==0&&receiver.violations.get()==0&&samples.get()==0,"failed identity proof must not release credentials/media: "+mode);
            }finally{client.close();}
        }
        try(Receiver receiver=new Receiver()){
            AtomicInteger samples=new AtomicInteger(),accepted=new AtomicInteger(),failed=new AtomicInteger();CollectorClient client=receiver.client(samples,accepted,failed);client.start();
            try{
                await(()->samples.get()>=2,"pitch receiver connected");
                check(receiver.pitchPosts.get()==0,"connection does not reapply a saved angle");
                check(client.setPitchAngle(18.3),"explicit pitch accepted into queue");await(()->accepted.get()==1,"pitch acknowledged");
                check(receiver.pitchPosts.get()==1&&receiver.configuredPitch==18.3&&receiver.posts.get()==0&&receiver.enabled,"pitch saves once and does not modify yaw");
                receiver.boot="new-pitch-boot";await(()->client.boot().equals("new-pitch-boot"),"pitch boot observed");Thread.sleep(300);
                check(receiver.pitchPosts.get()==1,"boot/reconnect must not replay an accepted pitch");
                receiver.pitchStatus=409;receiver.enabled=false;check(client.setPitchAngle(19),"disabled pitch request queued");await(()->failed.get()==1,"409 surfaced");
                check(receiver.configuredPitch==18.3&&!receiver.enabled&&receiver.posts.get()==0,"rejected pitch neither persists nor enables yaw");
                receiver.pitchStatus=202;receiver.enabled=true;receiver.dropPitchResponse=true;check(client.setPitchAngle(20),"uncertain pitch request queued");await(()->failed.get()==2,"lost response surfaced");
                int sent=receiver.pitchPosts.get();Thread.sleep(500);check(receiver.pitchPosts.get()==sent,"uncertain POST must not automatically retry");
                receiver.failTelemetry=true;int before=receiver.challenges.get();await(()->receiver.challenges.get()>before,"connection loss revokes proof");
                receiver.failTelemetry=false;int count=samples.get();await(()->samples.get()>count,"reconnected after new challenge");Thread.sleep(200);
                check(receiver.pitchPosts.get()==sent&&receiver.violations.get()==0,"recovery must not replay the pitch or bypass authentication");
            }finally{client.close();}
        }
        try(Receiver receiver=new Receiver()){
            AtomicInteger samples=new AtomicInteger();CollectorClient client=receiver.client(samples);client.start();
            try{
                await(()->samples.get()>=2,"face connected");check(receiver.facePosts.get()==0,"face OFF by default, no automatic POST");
                check(client.setFaceDetection(true),"enable queued");await(()->receiver.faceAccepted.get()==1,"face enabled ack");
                check(receiver.faceEnabled&&receiver.posts.get()==0&&receiver.pitchPosts.get()==0,"face must not alter motors or pitch");
                check(client.setFaceDetection(false),"disable queued");await(()->receiver.faceAccepted.get()==2,"face disabled ack");
                check(!receiver.faceEnabled,"disabled on robot");
                receiver.dropFaceResponse=true;check(client.setFaceDetection(true),"uncertain face request queued");await(()->receiver.faceFailed.get()==1,"lost face response surfaced");
                int sent=receiver.facePosts.get();Thread.sleep(500);check(receiver.facePosts.get()==sent,"lost face response no retry");
                receiver.boot="new-face-boot";await(()->client.boot().equals("new-face-boot"),"face reboot");Thread.sleep(300);
                check(receiver.facePosts.get()==sent&&receiver.violations.get()==0,"face not replayed after reboot; authenticated only");
            }finally{client.close();}
        }
        System.out.println("PASS: challenge before every client, proof/ID/nonce/replay/redirect rejection without Bearer, one-shot pitch, 409/yaw isolation, no replay after lost response/reconnect");

    }
}
