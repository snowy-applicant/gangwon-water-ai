"""Independent physics, inference, autograd and observational calibration checks."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import numpy as np
from waterai import server
from waterai.hardware import NETWORK_ID
from waterai.rig_twin import reference_config,reference,validate_config
from waterai.rig_pinn import load_model,predict,features,numpy_forward,evaluate,make_torch_system,train
from waterai.rig_calibrate import calibrate,prepare_runs

def calibration_fixture(patterns=None):
    config=reference_config();initial=[1800,1800];z=[.15]*4
    patterns=patterns or np.eye(4,dtype=int).tolist();start=datetime(2026,1,1,tzinfo=timezone.utc)
    runs=[];samples=[]
    for i,mask in enumerate(patterns):
        t=[1.,5.,10.,15.,20.];result=reference(config,initial,z,mask,t);begin=start+timedelta(seconds=100*i)
        cal={channel:dict(device_id='fixture-only',tare_n=1.,direction=1) for channel in ('F1','F3','F2')}
        runs.append(dict(id=f'run-{i}',start_at=begin.isoformat(),duration_s=21,
            initial_volume_ml=initial,outlet_height_m=z,open_mask=mask,force_calibration=cal))
        for j,sec in enumerate(t):
            for k,channel in enumerate(('F1','F3','F2')):
                samples.append(dict(sample_id=f'{i}-{j}-{k}',device_id='fixture-only',kind='measurement',domain='rig',
                    network_id=NETWORK_ID,channel_id=channel,observed_at=(begin+timedelta(seconds=sec)).isoformat(),
                    values={'force_n':1+result['receiver_m3'][j,k]*1000*9.80665}))
    experiment=dict(schema='rig-calibration/1',dataset_type='synthetic-test',physics=deepcopy(config),runs=runs)
    experiment['physics']['hose_coefficient_m2_5_s']=[k*.8 for k in config['hose_coefficient_m2_5_s']]
    export=dict(schema='rig-observations/1',rig={'id':NETWORK_ID},units={'force_n':'N'},samples=samples,total=len(samples))
    return export,experiment

class RigPhysicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.model=load_model();cls.config=reference_config()
    def prediction(self,mask=(1,1,1,1)):
        return predict(self.model,[1800,1800],[.15]*4,list(mask),[0,10,30,60])
    def test_reference_and_neural_global_mass_balance(self):
        ref=reference(self.config,[1800,1800],[.15]*4,[1]*4,[0,30,60])
        np.testing.assert_allclose(ref['source_m3'].sum(1)+ref['receiver_m3'].sum(1),.0036,atol=1e-14)
        pred=self.prediction();sources=np.array(pred['source_volume_ml']);receivers=np.array(pred['receiver_volume_ml'])
        np.testing.assert_allclose(sources.sum(1)+receivers.sum(1),3600,atol=1e-9)
        self.assertTrue(pred['valid']);self.assertFalse(pred['validated_on_observations'])
        self.assertFalse(pred['calibrated_on_observations'])
    def test_initial_and_all_closed_boundaries(self):
        r=self.prediction([0]*4)
        np.testing.assert_array_equal(r['receiver_volume_ml'],np.zeros((4,3)))
        np.testing.assert_array_equal(r['hose_flow_ml_s'],np.zeros((4,4)))
        np.testing.assert_array_equal(r['source_volume_ml'],np.full((4,2),1800))
        np.testing.assert_array_equal(self.prediction()['receiver_volume_ml'][0],[0,0,0])
    def test_central_receiver_is_h2_plus_h3(self):
        r=self.prediction([0,1,1,0]);cum=np.array(r['hose_cumulative_ml']);receiver=np.array(r['receiver_volume_ml'])
        np.testing.assert_allclose(receiver[:,1],cum[:,1]+cum[:,2])
        np.testing.assert_array_equal(receiver[:,[0,2]],np.zeros((4,2)))
        r=self.prediction([0,1,0,0]);np.testing.assert_array_equal(np.array(r['hose_flow_ml_s'])[:,[0,2,3]],np.zeros((4,3)))
    def test_raised_above_surface_has_zero_discharge(self):
        r=predict(self.model,[1200,1200],[.45]*4,[1]*4,[0,60])
        np.testing.assert_array_equal(r['receiver_volume_ml'],np.zeros((2,3)))
        np.testing.assert_array_equal(r['hose_flow_ml_s'],np.zeros((2,4)))
    def test_out_of_domain_input_is_rejected(self):
        for args in [([1199,1800],[.15]*4,[1]*4,[0,60]),([1800]*2,[.09]*4,[1]*4,[0,60]),
                     ([1800]*2,[.15]*4,[1,1,1,True],[0,60]),([1800]*2,[.15]*4,[1]*4,[60,0]),
                     ([1800]*2,[.15]*4,[1]*4,[0,61]),([1800]*2,[.15]*4,[1]*4,[float('nan')])]:
            with self.subTest(args=args),self.assertRaises(ValueError):predict(self.model,*args)
    def test_numpy_derivative_matches_finite_difference(self):
        raw=np.array([[24,1800,1800,.15,.2,.18,.17,1,1,1,1]],dtype=float);x=features(self.config,raw)
        _,_,derivative=numpy_forward(self.model,x);a=x.copy();b=x.copy();a[0,0]+=1e-5;b[0,0]-=1e-5
        numerical=(numpy_forward(self.model,a)[0]-numpy_forward(self.model,b)[0])/(2e-5*self.config['domain']['duration_s'])
        np.testing.assert_allclose(derivative,numerical,rtol=1e-7,atol=1e-12)
    def test_prediction_uses_weights_not_reference_solver(self):
        with patch('waterai.rig_twin.reference',side_effect=AssertionError('ODE called')),patch('waterai.rig_pinn.make_dataset',side_effect=AssertionError('dataset called')):
            original=self.prediction();changed=deepcopy(self.model);changed['layers'][-1]['bias'][4]+=1
            different=predict(changed,[1800]*2,[.15]*4,[1]*4,[0,10,30,60])
        self.assertGreater(different['hose_flow_ml_s'][-1][0],original['hose_flow_ml_s'][-1][0])
        self.assertFalse(different['valid'])
    def test_saved_holdout_metrics_reproduce(self):
        report=evaluate(self.model,48,self.model['test']['seed'])
        for name in ('receiver_volume_rmse_ml','hose_flow_rmse_ml_s','continuity_rmse_ml_s'):
            self.assertAlmostEqual(report[name],self.model['test'][name],places=8)
        self.assertLess(report['receiver_volume_rmse_ml'],5)
        self.assertLess(report['hose_flow_rmse_ml_s'],.15)
        self.assertEqual(report['negative_storage_points'],0);self.assertEqual(report['negative_rate_points'],0)

class RigCalibrationTests(unittest.TestCase):
    def test_known_coefficients_recovered_from_force_fixture(self):
        export,experiment=calibration_fixture();config,report=calibrate(export,experiment)
        np.testing.assert_allclose(config['hose_coefficient_m2_5_s'],reference_config()['hose_coefficient_m2_5_s'],rtol=1e-4)
        self.assertLess(report['fit_receiver_rmse_ml'],.01)
        self.assertFalse(config['calibrated_on_observations']);self.assertFalse(report['validated_on_observations'])
    def test_combined_central_measurement_cannot_identify_h2_and_h3(self):
        export,experiment=calibration_fixture([[1,0,0,0],[0,1,1,0],[0,1,1,0],[0,0,0,1]])
        with self.assertRaisesRegex(ValueError,'H2,H3'):prepare_runs(export,experiment)
    def test_real_geometry_and_valid_force_units_required(self):
        config=reference_config();config['tank_area_m2']=[None,None]
        with self.assertRaises(ValueError):validate_config(config)
        export,experiment=calibration_fixture();export['units']['force_n']='g'
        with self.assertRaises(ValueError):prepare_runs(export,experiment)

@unittest.skipUnless(importlib.util.find_spec('torch'),'PyTorch is optional; install hardware/pinn-requirements.txt for training checks')
class RigAutogradTests(unittest.TestCase):
    def test_actual_physics_loss_backprop_matches_parameter_difference(self):
        import torch
        model=load_model();net=torch.nn.Sequential(torch.nn.Linear(11,48),torch.nn.Tanh(),torch.nn.Linear(48,48),torch.nn.Tanh(),torch.nn.Linear(48,8)).double()
        with torch.no_grad():
            for layer,saved in zip([l for l in net if isinstance(l,torch.nn.Linear)],model['layers']):
                layer.weight.copy_(torch.tensor(saved['weight']));layer.bias.copy_(torch.tensor(saved['bias']))
        forward,physics=make_torch_system(net,model['physics'])
        raw=np.array([[25,1800,1800,.15,.2,.18,.17,1,1,1,1]],dtype=float)
        x=torch.tensor(features(model['physics'],raw),dtype=torch.float64,requires_grad=True)
        c,q,*_=forward(x);nc,nq,_=numpy_forward(model,x.detach().numpy())
        np.testing.assert_allclose(c.detach().numpy()*1e-3,nc,rtol=2e-6,atol=1e-9)
        np.testing.assert_allclose(q.detach().numpy()*1e-5,nq,rtol=2e-6,atol=1e-10)
        loss=sum(physics(x));loss.backward();parameter=net[-1].bias
        analytic=float(parameter.grad[4]);value=float(parameter[4].detach());eps=1e-5;values=[]
        for delta in (eps,-eps):
            with torch.no_grad():parameter[4]=value+delta
            values.append(float(sum(physics(x)).detach()))
        numerical=(values[0]-values[1])/(2*eps)
        self.assertGreater(abs(analytic),1e-9);self.assertAlmostEqual(analytic,numerical,places=7)
    def test_measured_receiver_loss_path_trains_without_relabelling_synthetic_data(self):
        export,experiment=calibration_fixture()
        with tempfile.TemporaryDirectory() as temporary,redirect_stdout(io.StringIO()):
            model=train(epochs=2,count=8,observations=export,experiment=experiment,output=Path(temporary)/'model.json')
        self.assertEqual(model['training']['measured_receiver_points'],len(export['samples']))
        self.assertGreater(model['training']['history'][0]['measured_receiver'],0)
        self.assertFalse(model['physics']['calibrated_on_observations'])

class RigAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.old=(server.DB,server.TOKEN,server.SENSOR_TOKEN)
        server.DB=Path(cls.temp.name)/'test.sqlite';server.TOKEN='test-admin';server.SENSOR_TOKEN='test-sensor'
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=cls.http.serve_forever,daemon=True).start();cls.base='http://127.0.0.1:'+str(cls.http.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();server.DB,server.TOKEN,server.SENSOR_TOKEN=cls.old;cls.temp.cleanup()
    def req(self,path='/api/rig-pinn',data=None,token='test-admin'):
        request=Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
        try:
            with urlopen(request,timeout=10) as response:return response.status,json.load(response)
        except HTTPError as exc:
            with exc:return exc.code,json.load(exc)
    def test_admin_only_model_metadata(self):
        self.assertEqual(self.req(token='test-sensor')[0],401)
        status,metadata=self.req();self.assertEqual(status,200);self.assertNotIn('layers',metadata)
    def test_prediction_cannot_actuate_or_write_hardware_data(self):
        body=dict(initial_volume_ml=[1800]*2,outlet_height_m=[.15]*4,open_mask=[1]*4,times_s=[0,60])
        self.assertEqual(self.req('/api/rig-pinn/predict',body,token='test-sensor')[0],401)
        status,prediction=self.req('/api/rig-pinn/predict',body);self.assertEqual(status,200)
        self.assertTrue(prediction['valid']);self.assertNotIn('command',prediction)
        self.assertEqual(self.req('/api/hardware')[1]['samples'],[])
        body['times_s']=[61];self.assertEqual(self.req('/api/rig-pinn/predict',body)[0],400)
    def test_missing_model_is_explicitly_unavailable(self):
        with patch('waterai.server.load_rig_model',side_effect=FileNotFoundError()):
            self.assertEqual(self.req()[0],503)

if __name__=='__main__':unittest.main()
