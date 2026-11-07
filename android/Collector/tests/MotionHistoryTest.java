package dev.stackchan.collector;
public final class MotionHistoryTest {
    private static void check(boolean value){if(!value)throw new AssertionError();}
    public static void main(String[] args){
        MotionHistory h=new MotionHistory();double[] sample={1,2,9.8,4,5,6};
        check(h.add("boot-a",10,1000000,true,sample));sample[0]=100;
        check(h.snapshot().values[0][0]==1); // producer cannot mutate captured sample
        check(!h.add("boot-a",10,1020000,true,sample)); // duplicate
        check(!h.add("boot-a",9,1040000,true,sample)); // old sequence
        check(!h.add("boot-a",11,900000,true,sample)); // backward device clock
        check(h.add("boot-a",11,1020000,false,null));check(!h.snapshot().valid[1]);
        sample[0]=Double.NaN;check(h.add("boot-a",12,1040000,true,sample));check(!h.snapshot().valid[2]);
        sample[0]=1;for(int i=13;i<1013;i++)h.add("boot-a",i,1040000+(i-12)*20000L,true,sample);
        check(MotionHistory.WINDOW_US==4000000L);
        check(h.snapshot().size()==201);check(h.snapshot().times[200]-h.snapshot().times[0]==4000000L);
        check(h.add("boot-b",1,20000,true,sample));check(h.snapshot().size()==1); // reboot starts fresh timebase
        h.clear();check(h.add("boundary",0,1,true,sample));check(h.add("boundary",1,4000001,true,sample));
        check(h.snapshot().size()==2); // both endpoints of the four-second display window are included
        check(h.add("boundary",2,4000002,true,sample));check(h.snapshot().size()==2&&h.snapshot().times[0]==4000001);
        h.clear();for(int i=0;i<700;i++)check(h.add("dense",i,1000000+i*10000L,true,sample));
        check(h.snapshot().size()==300&&h.snapshot().times[0]==5000000); // bounded capacity even above the normal 50 Hz rate
        check(h.snapshot().times[299]-h.snapshot().times[0]==2990000);
        h.clear();check(h.snapshot().size()==0);
        System.out.println("PASS: immutable sample ownership, dedup, clock order, invalid data, four-second inclusive window, bounded dense stream and reboot reset");
    }
}
