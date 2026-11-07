package dev.stackchan.collector;

import org.json.JSONArray;
import org.json.JSONObject;

/** Bounded metadata carried in the same HTTP response as the exact JPEG. */
final class FaceBoxes {
    static final FaceBoxes NONE=new FaceBoxes(-1,-1,0,new int[0][]);
    final long epoch,frameSeq,inferenceUs;
    final int[][] boxes;
    private FaceBoxes(long epoch,long seq,long duration,int[][] boxes){this.epoch=epoch;frameSeq=seq;inferenceUs=duration;this.boxes=boxes;}
    static FaceBoxes parse(String header,long seq){
        if(header==null||header.length()>2048)return NONE;
        try{
            JSONObject value=new JSONObject(header);long epoch=value.getLong("epoch"),frame=value.getLong("frame_seq"),duration=value.getLong("inference_us");
            JSONArray items=value.getJSONArray("boxes");
            if(epoch<0||frame!=seq||duration<0||items.length()>4)return NONE;
            int[][] boxes=new int[items.length()][5];
            for(int i=0;i<items.length();i++){
                JSONArray box=items.getJSONArray(i);if(box.length()!=5)return NONE;
                for(int j=0;j<5;j++){double n=box.getDouble(j);if(!Double.isFinite(n)||n<0||n>1000||n!=Math.rint(n))return NONE;boxes[i][j]=(int)n;}
                if(boxes[i][2]<=boxes[i][0]||boxes[i][3]<=boxes[i][1])return NONE;
            }
            return new FaceBoxes(epoch,frame,duration,boxes);
        }catch(Exception ignored){return NONE;}
    }
    String json(String boot,long timestampUs){
        try{JSONArray items=new JSONArray();for(int[] box:boxes){JSONArray item=new JSONArray();for(int v:box)item.put(v);items.put(item);}
            return new JSONObject().put("boot_id",boot).put("timestamp_us",timestampUs).put("epoch",epoch).put("frame_seq",frameSeq).put("inference_us",inferenceUs).put("boxes",items).toString();
        }catch(Exception ignored){return "{}";}
    }
}
