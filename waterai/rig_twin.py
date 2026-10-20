"""Independent reduced hydraulic reference for the two-tank/four-hose rig.

The numerical ODE solver generates pretraining data and checks the neural model.
It is NOT called to generate the neural predictions returned by rig_pinn.predict.
All calculations use seconds, m³, m and m³/s internally.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
SOURCE=np.array([0,0,1,1])
WITHDRAW=np.array([[1.,1.,0.,0.],[0.,0.,1.,1.]])
RECEIVE=np.array([[1.,0.,0.,0.],[0.,1.,1.,0.],[0.,0.,0.,1.]])

def reference_config():
    return json.loads((ROOT/'data/rig_reference.json').read_text())

def validate_config(config):
    if not isinstance(config,dict):raise ValueError('물리 설정은 JSON 객체여야 합니다.')
    c=deepcopy(config)
    if c.get('schema')!='rig-hydraulics/1' or c.get('network_id')!='hose-height-rig-v1':
        raise ValueError('하드웨어 물리 설정 버전 오류')
    for name,count,lo,hi in [('tank_area_m2',2,1e-5,1),('tank_datum_m',2,-10,10),('hose_coefficient_m2_5_s',4,1e-9,.01)]:
        values=c.get(name)
        if not isinstance(values,list) or len(values)!=count:raise ValueError(name+' 길이 오류')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi for v in values):
            raise ValueError(name+' 범위 오류')
    d=c.get('domain',{})
    duration=d.get('duration_s')
    if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not 1<=duration<=3600:raise ValueError('학습 시간 범위 오류')
    for key,lo,hi in [('initial_volume_ml',1,100000),('outlet_height_m',-10,10)]:
        bounds=d.get(key)
        if not isinstance(bounds,list) or len(bounds)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in bounds) or not lo<=bounds[0]<bounds[1]<=hi:
            raise ValueError(key+' 학습 범위 오류')
    if type(c.get('calibrated_on_observations')) is not bool:raise ValueError('보정 상태가 필요합니다.')
    if not isinstance(c.get('provenance'),str) or not c['provenance'].strip():raise ValueError('물리 설정 출처가 필요합니다.')
    if not isinstance(c.get('assumptions'),list) or any(not isinstance(a,str) for a in c['assumptions']):raise ValueError('모델 가정 목록이 필요합니다.')
    return c

def validate_conditions(config,initial_volume_ml,outlet_height_m,open_mask,times_s):
    arrays=[]
    for values,count,name in [(initial_volume_ml,2,'초기 물량'),(outlet_height_m,4,'출구 높이'),(open_mask,4,'공급 설정')]:
        if not isinstance(values,(list,tuple,np.ndarray)) or len(values)!=count:raise ValueError(name+' 개수 오류')
        if any(isinstance(v,(bool,np.bool_)) or not isinstance(v,(int,float,np.number)) or not math.isfinite(v) for v in values):
            raise ValueError(name+'는 유한한 숫자여야 합니다.')
        arrays.append(np.asarray(values,dtype=float))
    v,z,g=arrays;domain=config['domain']
    if any((g!=0)&(g!=1)):raise ValueError('공급 설정은 0 또는 1입니다.')
    for a,name,key in [(v,'초기 물량','initial_volume_ml'),(z,'출구 높이','outlet_height_m')]:
        lo,hi=domain[key]
        if np.any((a<lo)|(a>hi)):raise ValueError(name+'가 PINN 학습 범위를 벗어났습니다.')
    if not isinstance(times_s,(list,tuple,np.ndarray)) or not 1<=len(times_s)<=241:raise ValueError('예측 시각은 1~241개가 필요합니다.')
    if any(isinstance(t,(bool,np.bool_)) or not isinstance(t,(int,float,np.number)) or not math.isfinite(t) for t in times_s):raise ValueError('예측 시각 오류')
    t=np.asarray(times_s,dtype=float)
    if np.any((t<0)|(t>domain['duration_s'])) or np.any(np.diff(t)<=0):raise ValueError('예측 시각은 학습 시간 범위 안에서 증가해야 합니다.')
    return v/1e6,z,g,t

def hydraulic_flow(config,initial,cumulative,outlet,mask):
    volumes=np.asarray(initial)-np.asarray(cumulative)@WITHDRAW.T
    surface=np.asarray(config['tank_datum_m'])+volumes/np.asarray(config['tank_area_m2'])
    head=np.maximum(surface[...,SOURCE]-outlet,0.)
    return np.asarray(mask)*np.asarray(config['hose_coefficient_m2_5_s'])*np.sqrt(head)*(volumes[...,SOURCE]>0)

def reference(config,initial_volume_ml,outlet_height_m,open_mask,times_s):
    from scipy.integrate import solve_ivp
    config=validate_config(config)
    initial,z,g,t=validate_conditions(config,initial_volume_ml,outlet_height_m,open_mask,times_s)
    if t[-1]==0:cumulative=np.zeros((1,4))
    else:
        solution=solve_ivp(lambda _,c:hydraulic_flow(config,initial,c,z,g),[0,float(t[-1])],np.zeros(4),
            t_eval=t,rtol=1e-9,atol=1e-12,max_step=.5)
        if not solution.success:raise RuntimeError('실험 기준 ODE 적분 실패')
        cumulative=solution.y.T
    flow=hydraulic_flow(config,initial,cumulative,z,g)
    return dict(cumulative_m3=cumulative,flow_m3_s=flow,
        source_m3=initial-cumulative@WITHDRAW.T,receiver_m3=cumulative@RECEIVE.T)

def sample_conditions(config,count,seed):
    rng=np.random.default_rng(seed);d=config['domain']
    v=rng.uniform(*d['initial_volume_ml'],size=(count,2))
    z=rng.uniform(*d['outlet_height_m'],size=(count,4))
    g=rng.integers(0,2,size=(count,4)).astype(float)
    # Cover independently observable H2/H3 and the all-closed boundary explicitly.
    patterns=np.array([[0,0,0,0],[1,1,1,1],[0,1,0,0],[0,0,1,0],[1,0,0,1]])
    for i in range(min(count,len(patterns))):g[i]=patterns[i]
    return [(v[i].tolist(),z[i].tolist(),g[i].tolist()) for i in range(count)]

def make_dataset(config,count=128,seed=42,steps=13):
    times=np.linspace(0,config['domain']['duration_s'],steps);inputs=[];targets=[]
    for v,z,g in sample_conditions(config,count,seed):
        result=reference(config,v,z,g,times)
        inputs.extend([[t,*v,*z,*g] for t in times])
        targets.extend(np.column_stack((result['cumulative_m3']/1e-3,result['flow_m3_s']/1e-5)))
    return np.asarray(inputs),np.asarray(targets)
