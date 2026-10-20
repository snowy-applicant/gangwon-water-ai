"""Run real firmware logic on host; never assert physical shutoff from angles."""
import shutil,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'firmware_host'))
from host_link import HostLink,build

@unittest.skipUnless(shutil.which('g++'),'g++ is needed for sketch host checks')
class FirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.exe,warnings=build(cls.temp.name)
        if warnings:raise AssertionError(warnings)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.rig=HostLink(self.exe)
        self.nonce=self.events(1)[0].split()[-1]
    def tearDown(self):
        self.rig.close();self.rig.p.stdout.close()
    def events(self,ms=1):
        self.rig.advance(ms);out=[s for s in self.rig.queue if not s.startswith('ST ')];self.rig.queue.clear();return out
    def command(self,line,ms=1):self.rig.write(line);return self.events(ms)
    def position(self):
        self.rig.raw('?');values=self.rig.p.stdout.readline().split()[1:];self.rig.p.stdout.readline()
        return [(int(values[i]),values[i+1]=='1') for i in range(0,8,2)]
    def prepare(self):
        self.command('ANG R1 100 20');self.command('ARM '+self.nonce)
    def test_uncalibrated_boot_does_not_move_or_supply(self):
        self.assertTrue(all(not attached for _,attached in self.position()))
        self.assertEqual(self.command('OPENM 1000 0 0 0'),['ERR NOT_ARMED'])
        self.command('ARM '+self.nonce)
        self.assertEqual(self.command('OPENM 1000 0 0 0'),['ERR NOT_CALIBRATED R1'])
    def test_timeout_returns_to_stop_angle_and_keeps_torque(self):
        self.prepare();self.command('OPENM 1000 0 0 0')
        self.assertEqual(self.position()[0],(100,True))
        self.assertIn('DONE R1 1000 TIME',self.events(1500))
        self.assertEqual(self.position()[0],(20,True))
    def test_heartbeat_loss_and_explicit_stop(self):
        self.prepare();self.command('OPENM 10000 0 0 0');out=self.events(6000)
        self.assertIn('ALARM HB_LOST',out);self.assertEqual(self.position()[0],(20,True))
        self.assertEqual(self.command('OPENM 1000 0 0 0'),['ERR NOT_ARMED'])
        self.command('ARM '+self.nonce);self.command('OPENM 1000 0 0 0');self.command('STOP')
        self.assertEqual(self.position()[0],(20,True))
    def test_physical_button_latches(self):
        self.prepare();self.command('OPENM 10000 0 0 0');self.rig.raw('!button 1')
        self.assertIn('ALARM BUTTON',self.events(100));self.assertEqual(self.position()[0],(20,True))
        self.assertEqual(self.command('ANG R1 100 40'),['ERR STOP_LATCHED'])
        self.assertEqual(self.position()[0],(20,True))
        self.assertEqual(self.command('RESET'),['ERR BUTTON_STILL_PRESSED'])
        self.rig.raw('!button 0');self.command('RESET');self.assertEqual(self.command('ARM '+self.nonce),['OK ARM'])
    def test_limits_and_bad_nonce(self):
        self.prepare();self.assertEqual(self.command('ARM 1'),['ERR BAD_NONCE'])
        self.assertEqual(self.command('OPENM 70000 0 0 0'),['ERR BAD_MS R1'])
        self.assertEqual(self.command('ANG R1 25 20'),['ERR BAD_ANG'])
    def test_overflow_drops_whole_line(self):
        self.prepare();out=self.command('x'*70+'OPENM 1000 0 0 0')
        self.assertEqual(out,['ERR LINE_TOO_LONG']);self.assertEqual(self.position()[0],(20,True))
    def test_position_trial_cannot_overlap_timed_supply(self):
        self.prepare();self.command('POS R1 90')
        self.assertEqual(self.command('OPENM 1000 0 0 0'),['ERR BUSY'])
    def test_all_hoses_calibration_and_order(self):
        for line in ('R1','R3A','R3B','R2'):self.command('ANG '+line+' 100 20')
        self.command('ARM '+self.nonce);self.command('OPENM 1000 1000 1000 1000')
        self.assertEqual([a for a,_ in self.position()],[100,20,20,20])
        self.events(450);self.assertEqual([a for a,_ in self.position()],[100]*4)
        self.events(1100);self.assertEqual(self.position(),[(20,True)]*4)
