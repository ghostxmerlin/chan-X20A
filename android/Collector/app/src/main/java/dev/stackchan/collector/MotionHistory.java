package dev.stackchan.collector;

/** Bounded raw samples in device time. No smoothing, fabricated interpolation or gravity removal. */
public final class MotionHistory {
    private static final int CAPACITY=600;
    private final double[][] values=new double[CAPACITY][6];
    private final long[] times=new long[CAPACITY];
    private final boolean[] valid=new boolean[CAPACITY];
    private int count=0,next=0;
    private String boot="";
    private long lastSequence=-1;
    public static final class Snapshot {
        public final double[][] values;
        public final long[] times;
        public final boolean[] valid;
        Snapshot(int size){values=new double[size][6];times=new long[size];valid=new boolean[size];}
        public int size(){return times.length;}
    }
    public synchronized void clear(){count=next=0;lastSequence=-1;boot="";}
    public synchronized boolean add(String id,long sequence,long timestamp,boolean good,double[] sample){
        if(!id.equals(boot)){count=next=0;lastSequence=-1;boot=id;}
        if(sequence<=lastSequence||timestamp<=0)return false;
        if(count>0&&timestamp<=times[(next+CAPACITY-1)%CAPACITY])return false;
        if(sample==null||sample.length!=6)good=false;
        if(good)for(double v:sample)if(!Double.isFinite(v))good=false;
        times[next]=timestamp;valid[next]=good;
        if(good)System.arraycopy(sample,0,values[next],0,6);
        else java.util.Arrays.fill(values[next],Double.NaN);
        next=(next+1)%CAPACITY;count=Math.min(count+1,CAPACITY);lastSequence=sequence;
        return true;
    }
    public synchronized Snapshot snapshot(){
        long end=count==0?0:times[(next+CAPACITY-1)%CAPACITY];int size=0;
        for(int n=0;n<count;n++){int i=(next-count+n+CAPACITY)%CAPACITY;if(times[i]>=end-8000000)size++;}
        Snapshot result=new Snapshot(size);int out=0;
        for(int n=0;n<count;n++){int i=(next-count+n+CAPACITY)%CAPACITY;if(times[i]<end-8000000)continue;
            result.times[out]=times[i];result.valid[out]=valid[i];System.arraycopy(values[i],0,result.values[out],0,6);out++;}
        return result;
    }
}
