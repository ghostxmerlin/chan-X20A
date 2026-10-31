package dev.stackchan.collector;

import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Exercises the actual HTTP client against a local receiver, without robot hardware or credentials. */
final class CollectorControlTest {
    private static final String TOKEN="00000000000000000000000000000000";
    private static void check(boolean value,String reason){if(!value)throw new AssertionError(reason);}
    private static void await(java.util.function.BooleanSupplier condition,String reason)throws Exception{
        long end=System.nanoTime()+TimeUnit.SECONDS.toNanos(7);
        while(!condition.getAsBoolean()&&System.nanoTime()<end)Thread.sleep(25);
        check(condition.getAsBoolean(),reason);
    }
    private static final class Receiver implements AutoCloseable {
        final ServerSocket socket=new ServerSocket(0,16,InetAddress.getByName("127.0.0.1"));
        final ExecutorService workers=Executors.newCachedThreadPool();
        final AtomicInteger posts=new AtomicInteger(),telemetry=new AtomicInteger();
        volatile boolean enabled=true,standalone=true,closed=false;
        volatile String boot="first";
        Receiver()throws Exception{workers.execute(()->{while(!closed)try{
            Socket peer=socket.accept();workers.execute(()->handle(peer));
        }catch(IOException ignored){if(!closed)throw new RuntimeException(ignored);}});}
        void handle(Socket peer){try(Socket connection=peer){
            connection.setSoTimeout(3000);
            BufferedReader in=new BufferedReader(new InputStreamReader(connection.getInputStream(),"UTF-8"));
            String first=in.readLine();if(first==null)return;
            int length=0;String line;
            while((line=in.readLine())!=null&&!line.isEmpty())if(line.toLowerCase(Locale.ROOT).startsWith("content-length:"))length=Integer.parseInt(line.substring(15).trim());
            char[] body=new char[length];for(int read=0;read<length;){int n=in.read(body,read,length-read);if(n<0)throw new EOFException();read+=n;}
            int status=200;String result="{}";
            if(first.startsWith("POST /v1/remote ")){enabled=new JSONObject(new String(body)).getBoolean("enabled");posts.incrementAndGet();}
            else if(first.startsWith("GET /v1/info "))result=new JSONObject().put("protocol_version",1).put("boot_id",boot).put("audio_port",socket.getLocalPort()).put("remote",new JSONObject().put("control_mode",standalone?"standalone":"lease")).toString();
            else if(first.startsWith("GET /v1/telemetry"))result=new JSONObject().put("boot_id",boot).put("seq",telemetry.incrementAndGet()).put("remote",new JSONObject().put("rx_packets",0).put("motor",new JSONObject().put("requested",enabled).put("enabled",enabled))).toString();
            else status=503;
            byte[] bytes=result.getBytes("UTF-8");OutputStream out=connection.getOutputStream();
            out.write(("HTTP/1.1 "+status+" Test\r\nContent-Type: application/json\r\nContent-Length: "+bytes.length+"\r\nConnection: close\r\n\r\n").getBytes("UTF-8"));out.write(bytes);out.flush();
        }catch(Exception ignored){/* A client can disconnect while its request is outstanding. */}}
        CollectorClient client(AtomicInteger samples)throws Exception{return new CollectorClient("127.0.0.1:"+socket.getLocalPort(),TOKEN,new CollectorClient.Listener(){
            public void info(JSONObject v){} public void status(String s,boolean c){}
            public void frame(String b,long s,long t,int w,int h,byte[] j){}
            public void telemetry(JSONObject v){samples.incrementAndGet();}
            public void audio(String b,AudioBlock a){} public void event(String t,String d){}
        });}
        public void close()throws IOException{closed=true;socket.close();workers.shutdownNow();}
    }
    static void run()throws Exception{
        try(Receiver receiver=new Receiver()){
            AtomicInteger samples=new AtomicInteger();CollectorClient client=receiver.client(samples);client.start();
            try{
                await(()->samples.get()>=5,"standalone telemetry");
                check(receiver.posts.get()==0&&receiver.enabled,"connection must not disable the remote");
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
    }
}
