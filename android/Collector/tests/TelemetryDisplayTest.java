package dev.stackchan.collector;

import org.json.JSONArray;
import org.json.JSONObject;

/** Presentation semantics for new sensor groups and older recordings without them. */
final class TelemetryDisplayTest {
    private static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    private static JSONObject group(long timestamp)throws Exception{return new JSONObject().put("available",true).put("valid",true).put("timestamp_us",timestamp).put("seq",7).put("error","none");}
    static JSONObject addSensors(JSONObject telemetry,long timestamp)throws Exception{
        telemetry.put("magnetometer",group(timestamp).put("field_ut",new JSONArray(new double[]{12.25,-8.5,42.75})).put("unit","uT").put("raw",new JSONArray(new int[]{120,-90,440})).put("rhall",6380).put("overflow",false));
        telemetry.put("light",group(timestamp).put("lux",123.4).put("unit","lx").put("raw_ch0",2345).put("raw_ch1",456).put("gain",1).put("integration_ms",100).put("saturated",false));
        telemetry.put("proximity",group(timestamp).put("raw",2047).put("unit","count").put("saturated",false));
        return telemetry;
    }
    static void run()throws Exception{
        JSONObject telemetry=addSensors(new JSONObject().put("timestamp_us",2000000),1990000);String before=telemetry.toString();
        String summary=MainActivity.compactEnvironment(telemetry,false),details=MainActivity.formatTelemetry(telemetry);
        check(summary.equals("光 123.4 lx\n接近 2047 raw"),"home must label illuminance and proximity raw counts");
        check(details.contains("磁场 (µT，传感器坐标)")&&details.contains("X 12.25")&&details.contains("Y -8.50")&&details.contains("Z 42.75"),"magnetic axes must retain engineering values and units");
        check(details.contains("光照 (lx)  123.4")&&details.contains("接近 (原始计数)  2047"),"details must not imply proximity distance or compass heading");
        check(before.equals(telemetry.toString()),"formatting must not mutate telemetry recorded verbatim");
        check(MainActivity.compactEnvironment(telemetry,false,990000).equals(summary),"device age plus elapsed receive age must keep exact one-second boundary");
        check(MainActivity.compactEnvironment(telemetry,false,990001).equals("光 — lx · 过期\n接近 — raw · 过期"),"fresh device sample must expire after a receive gap without waiting for another telemetry packet");
        String gap=MainActivity.formatTelemetry(telemetry,990001);
        check(gap.contains("磁场 (µT，传感器坐标)\n数据已过期")&&gap.contains("光照 (lx)  数据已过期")&&gap.contains("接近 (原始计数)  数据已过期"),"replay cursor gaps must not keep old environmental measurements current");
        check(MainActivity.compactEnvironment(telemetry,false,-1).equals(summary),"negative elapsed age cannot make a newly received packet expire");
        check(before.equals(telemetry.toString()),"aging a displayed snapshot must not rewrite original sensor timestamps");

        JSONObject light=telemetry.getJSONObject("light"),proximity=telemetry.getJSONObject("proximity"),magnetic=telemetry.getJSONObject("magnetometer");
        light.put("valid",false).put("error","i2c");
        check(MainActivity.compactEnvironment(telemetry,false).equals("光 — lx\n接近 2047 raw"),"failed light must not hide independently valid proximity or publish retained lux");
        check(MainActivity.formatTelemetry(telemetry).contains("光照 (lx)  读取失败"),"read failure needs an explicit state");
        light.put("valid",true).put("error","none").put("timestamp_us",1000000);
        check(MainActivity.compactEnvironment(telemetry,false).contains("123.4"),"exact one-second freshness boundary should remain available");
        light.put("timestamp_us",999999);
        check(MainActivity.compactEnvironment(telemetry,false).contains("光 — lx · 过期"),"old light data may not look current");
        check(MainActivity.formatTelemetry(telemetry).contains("光照 (lx)  数据已过期"),"replay should respect device-time freshness");
        light.put("timestamp_us",2250000);
        check(MainActivity.compactEnvironment(telemetry,false).contains("123.4"),"a newer asynchronous sample within tolerance is permitted");
        light.put("timestamp_us",2250001);
        check(!MainActivity.compactEnvironment(telemetry,false).contains("123.4"),"implausibly future sensor time must be unavailable");
        light.put("timestamp_us",1990000).put("lux",JSONObject.NULL);
        check(MainActivity.compactEnvironment(telemetry,false).contains("光 — lx"),"null lux must not become zero");
        light.put("lux",-1);
        check(MainActivity.compactEnvironment(telemetry,false).contains("光 — lx"),"negative illuminance must not appear valid");
        light.put("lux",123.4).put("saturated",true);proximity.put("saturated",true);magnetic.put("overflow",true);
        details=MainActivity.formatTelemetry(telemetry);
        check(details.contains("读数饱和")&&details.contains("读数溢出")&&!details.contains("12.25")&&!details.contains("2047")&&!details.contains("123.4"),"saturated or overflow data must not advertise retained raw values as valid readings");
        proximity.put("saturated",false).put("raw",2048);
        check(MainActivity.compactEnvironment(telemetry,false).contains("接近 — raw"),"proximity count outside 11-bit range must be rejected");
        proximity.put("raw",1.5);
        check(MainActivity.compactEnvironment(telemetry,false).contains("接近 — raw"),"fractional raw proximity is not a valid ADC count");
        proximity.put("raw",2047).put("available",false);
        check(MainActivity.compactEnvironment(telemetry,false).contains("接近 — raw"),"code support alone cannot imply detected hardware");
        magnetic.put("overflow",false).put("field_ut",new JSONArray().put(1).put(JSONObject.NULL).put(3));
        check(MainActivity.formatTelemetry(telemetry).contains("磁场 (µT，传感器坐标)\n不可用"),"partial magnetic vectors must remain invalid");
        addSensors(telemetry,1990000);
        telemetry.getJSONObject("light").remove("timestamp_us");
        check(MainActivity.compactEnvironment(telemetry,false).contains("光 — lx"),"untimestamped samples must not be treated as fresh");
        check(MainActivity.compactEnvironment(telemetry,true).equals("光 — lx · 过期\n接近 — raw · 过期"),"lost telemetry must mask every home environmental value");

        JSONObject legacy=new JSONObject().put("timestamp_us",2000000);
        details=MainActivity.formatTelemetry(legacy);
        check(details.contains("磁场 (µT，传感器坐标)\n未提供")&&details.contains("光照 (lx)  未提供")&&details.contains("接近 (原始计数)  未提供"),"older firmware and recordings must show absent groups explicitly");
        check(MainActivity.compactEnvironment(legacy,false).equals("光 — lx\n接近 — raw"),"missing groups must not fabricate zero");
        System.out.println("PASS: magnetic XYZ, lux and raw proximity display; independent validity, freshness, overflow, nulls and legacy recordings");
    }
}
