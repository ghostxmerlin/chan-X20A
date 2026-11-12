import importlib.util, os, pathlib, pty, termios, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('serial_bridge',ROOT/'web/monitor/serial_bridge.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
class SerialBridgeTest(unittest.TestCase):
    @unittest.skipUnless(os.uname().sysname=='Darwin','macOS tty ownership contract')
    def test_raw_port_preserves_bytes_without_hangup(self):
        master,slave=pty.openpty();fd=None
        try:
            path=os.ttyname(slave);fd=bridge.open_port(path)
            self.assertFalse(termios.tcgetattr(fd)[2]&termios.HUPCL)
            data=b'\x00SCM1\r\n\xff';os.write(fd,data);self.assertEqual(os.read(master,100),data)
        finally:
            if fd is not None:os.close(fd)
            os.close(master);os.close(slave)
