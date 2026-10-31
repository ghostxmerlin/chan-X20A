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
        check(h.snapshot().size()==401);check(h.snapshot().times[400]-h.snapshot().times[0]==8000000);
        check(h.add("boot-b",1,20000,true,sample));check(h.snapshot().size()==1); // reboot starts fresh timebase
        h.clear();check(h.snapshot().size()==0);
        System.out.println("PASS: immutable sample ownership, dedup, clock order, invalid data, eight-second bound and reboot reset");
    }
}
