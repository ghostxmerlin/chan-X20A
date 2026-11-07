package dev.stackchan.collector;

import java.net.URL;

/** An already provisioned credential. Discovery can change its route, not its identity. */
public final class DeviceBinding {
    public final String address,token,deviceId;
    public DeviceBinding(String address,String token,String deviceId){
        this.address=address==null?"":address.trim();
        this.token=token==null?"":token.trim();
        this.deviceId=deviceId==null?"":deviceId.trim();
    }
    public boolean hasCredentials(){
        if(!token.matches("[0-9a-fA-F]{32}"))return false;
        try{normalizeAddress(address);return true;}catch(Exception ignored){return false;}
    }
    public boolean matches(String advertisedId){return !deviceId.isEmpty()&&deviceId.equals(advertisedId);}
    public DeviceBinding at(String nextAddress){return new DeviceBinding(nextAddress,token,deviceId);}
    public DeviceBinding identified(String id){return new DeviceBinding(address,token,id);}
    public static String normalizeAddress(String address)throws Exception{
        String raw=address==null?"":address.trim();
        if(!raw.contains("://"))raw="http://"+raw;
        URL u=new URL(raw);
        if(!"http".equals(u.getProtocol())||u.getHost().isEmpty()||u.getUserInfo()!=null||u.getQuery()!=null||u.getRef()!=null||
           (!u.getPath().isEmpty()&&!"/".equals(u.getPath())))throw new IllegalArgumentException("机器人地址无效");
        int port=u.getPort()<0?8080:u.getPort();
        if(port<1||port>65535)throw new IllegalArgumentException("机器人端口无效");
        String host=u.getHost();
        if(host.contains(":")&&!host.startsWith("["))host="["+host+"]";
        return host+":"+port;
    }
}
