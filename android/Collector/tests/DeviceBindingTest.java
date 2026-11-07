package dev.stackchan.collector;

public final class DeviceBindingTest {
    private static void check(boolean yes){if(!yes)throw new AssertionError();}
    public static void main(String[] args)throws Exception{
        String token="0123456789abcdef0123456789abcdef";
        DeviceBinding bound=new DeviceBinding("192.0.2.4:8080",token,"unit-A");
        check(bound.hasCredentials()&&bound.matches("unit-A")&&!bound.matches("unit-B")&&!bound.matches(""));
        DeviceBinding routed=bound.at("192.0.2.9:8080");
        check(routed.deviceId.equals(bound.deviceId)&&routed.token.equals(token));
        check(!new DeviceBinding("192.0.2.4","","unit-A").hasCredentials());
        check(!new DeviceBinding("",token,"unit-A").hasCredentials());
        check(!new DeviceBinding("192.0.2.4",token,"").matches("unit-A"));
        check(DeviceBinding.normalizeAddress("http://192.0.2.4/").equals("192.0.2.4:8080"));
        check(DeviceBinding.normalizeAddress("[2001:db8::1]:8080").equals("[2001:db8::1]:8080"));
        for(String address:new String[]{"https://192.0.2.4","http://u:p@192.0.2.4","http://192.0.2.4/?q=x","http://192.0.2.4/#fragment","http://192.0.2.4/other","192.0.2.4:0","192.0.2.4:65536"}){
            boolean rejected=false;try{DeviceBinding.normalizeAddress(address);}catch(Exception expected){rejected=true;}check(rejected);
        }
        System.out.println("PASS: provisioned device binding, identity matching, address migration preserves credentials, endpoint validation");
    }
}
