package dev.stackchan.collector;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Prove possession of the existing credential before sending any Bearer token. */
public final class DiscoveryProof {
    private static final SecureRandom random=new SecureRandom();
    private static final char[] HEX="0123456789abcdef".toCharArray();
    public static String nonce(){byte[] bytes=new byte[16];random.nextBytes(bytes);return hex(bytes);}
    private static String hex(byte[] bytes){StringBuilder text=new StringBuilder(bytes.length*2);for(byte b:bytes){text.append(HEX[(b&255)>>>4]);text.append(HEX[b&15]);}return text.toString();}
    public static String sign(String token,String deviceId,String nonce)throws Exception{
        Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec(token.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
        return hex(mac.doFinal(("chan-discovery-v1\n"+deviceId+"\n"+nonce).getBytes(StandardCharsets.UTF_8)));
    }
    public static boolean verify(String token,String expectedId,String expectedNonce,int protocol,String deviceId,String nonce,String proof){
        if(protocol!=1||token==null||!token.matches("[0-9a-fA-F]{32}")||deviceId==null||!deviceId.matches("[A-Za-z0-9._:-]{1,128}")||
           (expectedId!=null&&!expectedId.isEmpty()&&!expectedId.equals(deviceId))||expectedNonce==null||!expectedNonce.matches("[0-9a-f]{32}")||
           !expectedNonce.equals(nonce)||proof==null||!proof.matches("[0-9a-f]{64}"))return false;
        try{return MessageDigest.isEqual(sign(token,deviceId,nonce).getBytes(StandardCharsets.US_ASCII),proof.getBytes(StandardCharsets.US_ASCII));}
        catch(Exception ignored){return false;}
    }
}
