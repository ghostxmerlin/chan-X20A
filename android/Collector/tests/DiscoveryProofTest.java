package dev.stackchan.collector;

public final class DiscoveryProofTest {
    private static void check(boolean yes){if(!yes)throw new AssertionError();}
    public static void main(String[] args)throws Exception{
        String token="0123456789abcdef0123456789abcdef",id="unit-A",nonce="0123456789abcdef0123456789abcdef";
        String proof=DiscoveryProof.sign(token,id,nonce);
        check(DiscoveryProof.verify(token,id,nonce,1,id,nonce,proof));
        check(DiscoveryProof.verify(token,"",nonce,1,id,nonce,proof));
        check(!DiscoveryProof.verify(token,"unit-B",nonce,1,id,nonce,proof));
        check(!DiscoveryProof.verify(token,id,DiscoveryProof.nonce(),1,id,nonce,proof));
        check(!DiscoveryProof.verify(token,id,nonce,2,id,nonce,proof));
        check(!DiscoveryProof.verify("00000000000000000000000000000000",id,nonce,1,id,nonce,proof));
        check(!DiscoveryProof.verify(token,id,nonce,1,id,nonce,proof.substring(1)));
        check(!DiscoveryProof.verify(token,id,nonce,1,id,nonce,proof.toUpperCase()));
        check(!DiscoveryProof.verify(token,id,nonce,1,id+"\n",nonce,proof));
        check(DiscoveryProof.nonce().matches("[0-9a-f]{32}"));
        System.out.println("PASS: HMAC identity proof, nonce replay/identity/protocol/key/encoding rejection");
    }
}
