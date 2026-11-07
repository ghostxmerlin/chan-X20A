package dev.stackchan.collector;

import android.os.Handler;
import org.json.JSONObject;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Credential-free UDP discovery; only signed replies for the bound device are accepted. */
final class RobotDiscovery {
    interface Listener {void found(String deviceId,String address);}
    private final Handler ui;
    private final Listener listener;
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private volatile DatagramSocket socket;
    private volatile long generation=0;
    private boolean scanning=false;
    RobotDiscovery(Handler ui,Listener listener){this.ui=ui;this.listener=listener;}
    void scan(DeviceBinding binding){
        if(scanning||!binding.hasCredentials()||binding.deviceId.isEmpty())return;
        scanning=true;final long epoch=++generation;
        worker.execute(()->{
            try(DatagramSocket peer=new DatagramSocket()){
                socket=peer;peer.setBroadcast(true);peer.setSoTimeout(250);
                String nonce=DiscoveryProof.nonce();
                byte[] request=new JSONObject().put("protocol",1).put("device_id",binding.deviceId).put("nonce",nonce).toString().getBytes(StandardCharsets.UTF_8);
                Set<InetAddress> destinations=new LinkedHashSet<>();destinations.add(InetAddress.getByName("255.255.255.255"));
                try{for(NetworkInterface network:Collections.list(NetworkInterface.getNetworkInterfaces()))if(network.isUp()&&!network.isLoopback())
                    for(InterfaceAddress address:network.getInterfaceAddresses())if(address.getBroadcast()!=null)destinations.add(address.getBroadcast());}catch(Exception ignored){}
                for(InetAddress target:destinations)if(epoch==generation)try{peer.send(new DatagramPacket(request,request.length,target,8082));}catch(Exception ignored){}
                long until=System.nanoTime()+1200000000L;
                byte[] bytes=new byte[513];
                while(epoch==generation&&System.nanoTime()<until){
                    DatagramPacket packet=new DatagramPacket(bytes,bytes.length);
                    try{peer.receive(packet);}catch(SocketTimeoutException ignored){continue;}
                    if(packet.getLength()>512)continue;
                    JSONObject reply;
                    try{reply=new JSONObject(new String(packet.getData(),packet.getOffset(),packet.getLength(),StandardCharsets.UTF_8));}catch(Exception ignored){continue;}
                    String id=reply.optString("device_id");
                    if(!DiscoveryProof.verify(binding.token,binding.deviceId,nonce,reply.optInt("protocol"),id,reply.optString("nonce"),reply.optString("proof")))continue;
                    int port=reply.optInt("port",0);if(port!=8080)continue; // Port is fixed, not covered by the proof.
                    String host=packet.getAddress().getHostAddress();if(host.contains(":"))host="["+host+"]";
                    String address=host+":"+port;
                    ui.post(()->{if(epoch==generation)listener.found(id,address);});break;
                }
            }catch(Exception ignored){/* A missing LAN route is retried while disconnected. */}
            finally{socket=null;ui.post(()->{if(epoch==generation)scanning=false;});}
        });
    }
    void stop(){generation++;scanning=false;DatagramSocket peer=socket;if(peer!=null)peer.close();}
    void close(){stop();worker.shutdownNow();}
}
