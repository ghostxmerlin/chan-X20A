#!/usr/bin/env python3
"""macOS USB byte bridge. Never issues modem-control (DTR/RTS) ioctls."""
import errno, fcntl, json, os, plistlib, select, signal, subprocess, sys, termios, time

def find_port(serial):
    expected=serial.lower().replace(':','').replace('-','')
    if not expected or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('invalid USB serial identity')
    tree=plistlib.loads(subprocess.check_output(['/usr/sbin/ioreg','-r','-c','IOUSBHostDevice','-a','-l'],timeout=5))
    found=set()
    def ports(d):
        if d.get('IOCalloutDevice'):found.add(d['IOCalloutDevice'])
        for child in d.get('IORegistryEntryChildren',[]):ports(child)
    def walk(items):
        for d in items:
            value=str(d.get('USB Serial Number','')).lower().replace(':','').replace('-','')
            if value==expected and d.get('idVendor')==0x303a and d.get('idProduct')==0x1001:ports(d)
            walk(d.get('IORegistryEntryChildren',[]))
    walk(tree)
    if len(found)!=1:raise OSError('expected one matching StackChan USB device')
    return found.pop()

def open_port(port):
    fd=os.open(port,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    try:
        # TIOCEXCL stops a second monitor/esptool owner opening this tty.
        fcntl.ioctl(fd,0x2000740d)
        a=termios.tcgetattr(fd);a[0]=a[1]=a[3]=0
        a[2]=(a[2]&~(termios.CSIZE|termios.PARENB|termios.CSTOPB|termios.HUPCL|getattr(termios,'CRTSCTS',0)))|termios.CS8|termios.CREAD|termios.CLOCAL
        a[4]=a[5]=termios.B115200;a[6][termios.VMIN]=0;a[6][termios.VTIME]=0
        termios.tcsetattr(fd,termios.TCSANOW,a)
        return fd
    except BaseException:os.close(fd);raise

def bridge(serial):
    fd=open_port(find_port(serial));to_usb=bytearray();to_host=bytearray()
    os.set_blocking(0,False);os.set_blocking(1,False)
    signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
    try:
        while True:
            reads=[fd]+([0] if len(to_usb)<4096 else [])
            if len(to_host)>=262144:reads.remove(fd)
            r,w,_=select.select(reads,([fd] if to_usb else [])+([1] if to_host else []),[],0.2)
            for src in r:
                try:data=os.read(src,4096)
                except BlockingIOError:continue
                if not data:return
                (to_usb if src==0 else to_host).extend(data)
            for dst in w:
                buf=to_usb if dst==fd else to_host
                try:n=os.write(dst,buf[:4096])
                except BlockingIOError:continue
                del buf[:n]
    finally:os.close(fd)

if __name__=='__main__':
    try:bridge(sys.argv[1])
    except (OSError,ValueError,subprocess.SubprocessError) as e:
        print('USB bridge: '+str(e),file=sys.stderr);sys.exit(1)
