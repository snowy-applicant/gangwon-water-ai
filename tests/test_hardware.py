import importlib.util
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import datetime,timedelta,timezone
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from waterai import server
from waterai.hardware import validate_sample,force_to_volume,volume_rate,NETWORK_ID

spec=importlib.util.spec_from_file_location('rig_bridge',Path(__file__).resolve().parents[1]/'hardware/bridge.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

def observation(**updates):
    data=dict(sample_id='s1',device_id='pasco-1',domain='rig',network_id=NETWORK_ID,
        channel_id='F1',observed_at=datetime.now(timezone.utc).isoformat(),kind='measurement',values={'force_n':0.0})
    data.update(updates);return data

class MeasurementTests(unittest.TestCase):
    def test_force_is_not_relabelled_as_flow(self):
        sample=validate_sample(observation(values={'force_n':-2.5}))
        self.assertEqual(sample['values'],{'force_n':-2.5})
        self.assertEqual(validate_sample(observation())['values']['force_n'],0)
    def test_invalid_units_scope_numbers_and_time(self):
        invalid=[{'domain':'field'},{'network_id':'gangwon-demo-v1'},{'channel_id':'H2'},
            {'values':{'force_n':True}},{'values':{'force_n':math.nan}},{'values':{'force_n':math.inf}},
            {'values':{'pressure_kpa':1}},{'observed_at':'2026-01-01T12:00:00'},
            {'observed_at':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}]
        for updates in invalid:
            with self.subTest(updates=updates),self.assertRaises(ValueError):validate_sample(observation(**updates))
    def test_calibrated_force_volume_and_rate(self):
        self.assertAlmostEqual(force_to_volume(3+9.80665,3,1),.001)
        self.assertAlmostEqual(force_to_volume(3-9.80665,3,-1),.001)
        with self.assertRaises(ValueError):force_to_volume(2,3,1)
        self.assertAlmostEqual(volume_rate([(0,0),(1,.00001),(2,.00002)]),.00001)
        with self.assertRaises(ValueError):volume_rate([(0,.001),(1,.0005),(2,0)])
        with self.assertRaises(ValueError):volume_rate([(1,0),(1,.00001),(2,.00002)])
    def test_gateway_status_is_not_a_measurement(self):
        sample=bridge.make_sample('ST 1000 L=1010 B=0 A=1 HB=1 C=15','uno-1')
        self.assertEqual(sample['kind'],'controller_status');self.assertNotIn('force_n',sample['values'])
        self.assertEqual(sample['values']['active'],[True,False,True,False])
        for line in ('ST 1 L=000 B=0 A=0 HB=1 C=15','{"force_n":3}'):
            with self.assertRaises(ValueError):bridge.make_sample(line,'uno-1')
    def test_gateway_only_explicit_commands(self):
        for command in ('PING','STOP','ARM 1234','ANG R3A 100 20','OPENM 1000 0 0 0'):
            self.assertEqual(bridge.validate_command(command),command)
        for command in ('ARM 1\nOPENM 1 2 3 4','POS R1 181','ANG R1 20','OPEN 1000','python anything'):
            with self.assertRaises(ValueError):bridge.validate_command(command)

class HardwareServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.old=(server.DB,server.TOKEN,server.SENSOR_TOKEN)
        server.DB=Path(cls.temp.name)/'test.sqlite';server.TOKEN='test-admin';server.SENSOR_TOKEN='test-sensor'
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();server.DB,server.TOKEN,server.SENSOR_TOKEN=cls.old;cls.temp.cleanup()
    def req(self,path='/api/hardware',data=None,token=None):
        request=Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers={
            'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        try:
            with urlopen(request,timeout=5) as response:return response.status,json.load(response)
        except HTTPError as exc:
            with exc:return exc.code,json.load(exc)
    def test_separate_admin_and_sensor_auth(self):
        for token in (None,'bad','test-admin'):
            self.assertEqual(self.req(data=observation(),token=token)[0],401)
        for token in (None,'bad','test-sensor'):
            self.assertEqual(self.req(token=token)[0],401)
        saved=server.SENSOR_TOKEN;server.SENSOR_TOKEN=''
        try:self.assertEqual(self.req(data=observation(),token='test-sensor')[0],401)
        finally:server.SENSOR_TOKEN=saved
    def test_idempotent_ingest_conflict_and_no_conversion(self):
        sample=observation(sample_id='dedupe')
        code,body=self.req(data=sample,token='test-sensor');self.assertEqual(code,201)
        code,again=self.req(data=sample,token='test-sensor');self.assertEqual(code,200)
        self.assertEqual(body['sequence'],again['sequence'])
        changed={**sample,'values':{'force_n':1}}
        self.assertEqual(self.req(data=changed,token='test-sensor')[0],409)
        self.assertEqual(self.req(data=observation(domain='field'),token='test-sensor')[0],400)
    def test_latest_observation_order_staleness_and_export(self):
        now=datetime.now(timezone.utc)
        for sid,seconds,value in [('new',0,4.2),('delayed',-100,2.0)]:
            sample=observation(sample_id=sid,device_id='order-test',values={'force_n':value},observed_at=(now+timedelta(seconds=seconds)).isoformat())
            self.assertEqual(self.req(data=sample,token='test-sensor')[0],201)
        stale=observation(sample_id='old',device_id='old-device',channel_id='F3',observed_at=(now-timedelta(seconds=20)).isoformat())
        self.req(data=stale,token='test-sensor')
        code,state=self.req(token='test-admin');self.assertEqual(code,200)
        latest=next(s for s in state['samples'] if s['device_id']=='order-test')
        self.assertEqual(latest['values'],{'force_n':4.2});self.assertFalse(latest['stale'])
        self.assertTrue(next(s for s in state['samples'] if s['device_id']=='old-device')['stale'])
        code,data=self.req('/api/hardware/export',token='test-admin');self.assertEqual(code,200)
        self.assertEqual(data['units']['force_n'],'N');self.assertEqual(data['rig']['id'],NETWORK_ID)
        self.assertTrue(any(s['sample_id']=='delayed' for s in data['samples']))
        self.assertNotIn('test-sensor',json.dumps(data))
        self.assertNotIn('prediction',latest)

if __name__=='__main__':unittest.main()
