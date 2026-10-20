"""Conditional ODE-PINN for the hardware graph, with portable NumPy inference.

Neural outputs: four cumulative deliveries and four flows. Autograd enforces
dC/dt=q and the calibrated lumped hose law. Source and receiver volumes are
constructed from the same C, so their global mass balance is exact.
"""
import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path
import numpy as np
from .rig_twin import (ROOT,SOURCE,WITHDRAW,RECEIVE,reference_config,validate_config,
    validate_conditions,hydraulic_flow,make_dataset)

MODEL_PATH=ROOT/'models/rig-pinn.json'
VOLUME_SCALE=1e-3
FLOW_SCALE=1e-5

def normalizer(config):
    d=config['domain'];vlo,vhi=d['initial_volume_ml'];zlo,zhi=d['outlet_height_m']
    center=np.array([0,*([((vlo+vhi)/2)]*2),*([((zlo+zhi)/2)]*4),*([.5]*4)])
    scale=np.array([d['duration_s'],*([(vhi-vlo)/2]*2),*([(zhi-zlo)/2]*4),*([.5]*4)])
    return center,scale

def features(config,inputs):
    center,scale=normalizer(config)
    return (np.asarray(inputs)-center)/scale

def decode_conditions(config,x):
    center,scale=normalizer(config);raw=x*scale+center
    initial=raw[:,1:3]/1e6;outlet=raw[:,3:7];mask=raw[:,7:11]
    surface=np.asarray(config['tank_datum_m'])+initial/np.asarray(config['tank_area_m2'])
    return initial,outlet,mask*(surface[:,SOURCE]>outlet)

def numpy_forward(model,x):
    """Forward pass and exact derivative wrt normalized time, no ODE calls."""
    y=np.asarray(x,dtype=float);dy=np.zeros_like(y);dy[:,0]=1
    for i,layer in enumerate(model['layers']):
        weight=np.asarray(layer['weight']);y=y@weight.T+np.asarray(layer['bias']);dy=dy@weight.T
        if i<len(model['layers'])-1:y=np.tanh(y);dy=dy*(1-y*y)
    positive=np.logaddexp(0,y);derivative=dy/(1+np.exp(-np.clip(y,-700,700)))
    _,_,gate=decode_conditions(model['physics'],x)
    cumulative=x[:,0:1]*positive[:,:4]*gate
    dc_dt=(positive[:,:4]+x[:,0:1]*derivative[:,:4])*gate*VOLUME_SCALE/model['physics']['domain']['duration_s']
    return cumulative*VOLUME_SCALE,positive[:,4:]*gate*FLOW_SCALE,dc_dt

def load_model(path=MODEL_PATH):
    model=json.loads(Path(path).read_text())
    if model.get('version')!='hose-height-ode-pinn-v1':raise ValueError('실험 PINN 모델 버전 오류')
    validate_config(model['physics'])
    if model.get('architecture')!=[11,48,48,8] or len(model.get('layers',[]))!=3:raise ValueError('PINN 구조 오류')
    for layer,inside,outside in zip(model['layers'],[11,48,48],[48,48,8]):
        weights=np.asarray(layer['weight']);bias=np.asarray(layer['bias'])
        if weights.shape!=(outside,inside) or bias.shape!=(outside,) or not np.isfinite(weights).all() or not np.isfinite(bias).all():raise ValueError('PINN 가중치 오류')
    return model

def predict(model,initial_volume_ml,outlet_height_m,open_mask,times_s):
    config=model['physics'];initial,z,g,t=validate_conditions(config,initial_volume_ml,outlet_height_m,open_mask,times_s)
    inputs=np.array([[s,*initial_volume_ml,*outlet_height_m,*open_mask] for s in t]);x=features(config,inputs)
    cumulative,flows,dc_dt=numpy_forward(model,x)
    sources=initial-cumulative@WITHDRAW.T;receivers=cumulative@RECEIVE.T
    expected=hydraulic_flow(config,initial,cumulative,z,g)
    mass=np.max(np.abs(sources.sum(axis=1)+receivers.sum(axis=1)-initial.sum()))
    monotonic=np.min(dc_dt)
    continuity_error=float(np.sqrt(np.mean((dc_dt-flows)**2))*1e6)
    hydraulic_error=float(np.sqrt(np.mean((flows-expected)**2))*1e6)
    # Do not silently clip predictions into a plausible physical trajectory.
    valid=bool(np.min(sources)>=-1e-9 and monotonic>=-1e-8 and np.all(np.isfinite(flows))
        and continuity_error<=.25 and hydraulic_error<=.25)
    return dict(model_version=model['version'],network_id=config['network_id'],
        provenance=config['provenance'],calibrated_on_observations=config['calibrated_on_observations'],
        validated_on_observations=model.get('validated_on_observations',False),
        valid=valid,times_s=t.tolist(),source_volume_ml=(sources*1e6).tolist(),
        receiver_volume_ml=(receivers*1e6).tolist(),hose_cumulative_ml=(cumulative*1e6).tolist(),
        hose_flow_ml_s=(flows*1e6).tolist(),
        diagnostics=dict(mass_balance_max_ml=float(mass*1e6),
            continuity_rmse_ml_s=continuity_error,hydraulic_rmse_ml_s=hydraulic_error,
            physics_check_tolerance_ml_s=.25,
            minimum_source_volume_ml=float(np.min(sources)*1e6),minimum_cumulative_derivative_ml_s=float(monotonic*1e6)),
        assumptions=config['assumptions'],warning='실물 검증 전 연구용 PINN. 실제 장치 제어 명령이 아닙니다.')

def evaluate(model,count=48,seed=9042):
    config=model['physics'];inputs,target=make_dataset(config,count,seed,steps=17)
    cumulative,flow,dc_dt=numpy_forward(model,features(config,inputs))
    initial=inputs[:,1:3]/1e6;sources=initial-cumulative@WITHDRAW.T
    expected=hydraulic_flow(config,initial,cumulative,inputs[:,3:7],inputs[:,7:11])
    error=cumulative-target[:,:4]*VOLUME_SCALE
    metrics=dict(scenarios=count,points=len(inputs),seed=seed,
        receiver_volume_rmse_ml=float(np.sqrt(np.mean((error@RECEIVE.T)**2))*1e6),
        source_volume_rmse_ml=float(np.sqrt(np.mean((error@WITHDRAW.T)**2))*1e6),
        hose_flow_rmse_ml_s=float(np.sqrt(np.mean((flow-target[:,4:]*FLOW_SCALE)**2))*1e6),
        continuity_rmse_ml_s=float(np.sqrt(np.mean((dc_dt-flow)**2))*1e6),
        hydraulic_rmse_ml_s=float(np.sqrt(np.mean((flow-expected)**2))*1e6),
        mass_balance_max_ml=float(np.max(np.abs(sources.sum(1)+(cumulative@RECEIVE.T).sum(1)-initial.sum(1)))*1e6),
        negative_storage_points=int(np.sum(np.min(sources,axis=1)<-1e-9)),
        negative_rate_points=int(np.sum(np.min(dc_dt,axis=1)<-1e-8)))
    return metrics

def make_torch_system(net,config):
    """Actual differentiable forward/physics terms shared by training and checks."""
    import torch
    from torch.nn import functional as F
    options=dict(dtype=next(net.parameters()).dtype,device=next(net.parameters()).device)
    center,scale=normalizer(config);center=torch.tensor(center,**options);scale=torch.tensor(scale,**options)
    area=torch.tensor(config['tank_area_m2'],**options);datum=torch.tensor(config['tank_datum_m'],**options);coeff=torch.tensor(config['hose_coefficient_m2_5_s'],**options)
    withdraw=torch.tensor(WITHDRAW,**options)
    def forward(x):
        raw=x*scale+center;initial=raw[:,1:3]/1e6;z=raw[:,3:7];gate=raw[:,7:11]
        gate=gate*((datum+initial/area)[:,SOURCE]>z)
        positive=F.softplus(net(x));c=x[:,0:1]*positive[:,:4]*gate;q=positive[:,4:]*gate
        return c,q,initial,z,gate
    def physics_losses(x):
        c,q,initial,z,gate=forward(x)
        gradients=torch.stack([torch.autograd.grad(c[:,i].sum(),x,create_graph=True)[0][:,0] for i in range(4)],dim=1)
        source=initial-c*VOLUME_SCALE@withdraw.T
        head=(datum+source/area)[:,SOURCE]-z
        # sqrt has an unbounded derivative at zero; clamp only its training
        # derivative in a 1e-8 m neighbourhood. Evaluation uses the exact max law.
        physical=gate*coeff*torch.where(head>0,torch.sqrt(head.clamp_min(1e-8)),torch.zeros_like(head))/FLOW_SCALE
        continuity=F.mse_loss(gradients,config['domain']['duration_s']*FLOW_SCALE/VOLUME_SCALE*q)
        hydraulic=F.mse_loss(q,physical)
        storage=F.relu(-source/VOLUME_SCALE).square().mean()
        return continuity,hydraulic,storage
    return forward,physics_losses

def train(config=None,epochs=3200,count=160,seed=42,output=MODEL_PATH,validation_seed=1042,test_seed=19042,
          observations=None,experiment=None):
    import torch
    from torch.nn import functional as F
    observation_runs=[]
    if observations is not None or experiment is not None:
        if observations is None or experiment is None:raise ValueError('관측 자료와 실험 설명을 함께 제공하세요.')
        from .rig_calibrate import calibrate,prepare_runs
        config,_=calibrate(observations,experiment);_,observation_runs=prepare_runs(observations,experiment)
    config=validate_config(config or reference_config());torch.manual_seed(seed);torch.set_num_threads(2)
    rng=np.random.default_rng(seed);start=time.perf_counter()
    inputs,targets=make_dataset(config,count,seed)
    xx=torch.tensor(features(config,inputs),dtype=torch.float32);yy=torch.tensor(targets,dtype=torch.float32)
    obs_inputs=[];obs_values=[];obs_channels=[]
    for run in observation_runs:
        for (row,channel),value in zip(run['rows'],run['volume_m3']):
            obs_inputs.append([run['times_s'][row],*run['initial_volume_ml'],*run['outlet_height_m'],*run['open_mask']])
            obs_values.append(value/VOLUME_SCALE);obs_channels.append(channel)
    if obs_inputs:
        obs_x=torch.tensor(features(config,np.asarray(obs_inputs)),dtype=torch.float32)
        obs_y=torch.tensor(obs_values,dtype=torch.float32);obs_channel=torch.tensor(obs_channels)
    net=torch.nn.Sequential(torch.nn.Linear(11,48),torch.nn.Tanh(),torch.nn.Linear(48,48),torch.nn.Tanh(),torch.nn.Linear(48,8))
    with torch.no_grad():net[-1].bias[:4].fill_(-1.3);net[-1].bias[4:].fill_(-.7)
    optimizer=torch.optim.Adam(net.parameters(),lr=.002)
    forward,physics_losses=make_torch_system(net,config);history=[]
    for epoch in range(epochs):
        indices=rng.integers(0,len(xx),size=128)
        c,q,*_=forward(xx[indices]);y=yy[indices]
        supervised=F.mse_loss(c,y[:,:4])+.3*F.mse_loss(q,y[:,4:])
        # Fresh collocation conditions every epoch, not just the finite labelled
        # training runs. Their targets come only from the coupled physics.
        collocation=rng.uniform(-1,1,size=(256,11));collocation[:,0]=rng.uniform(0,1,256)
        collocation[:,7:]=rng.integers(0,2,size=(256,4))*2-1
        x=torch.tensor(collocation,dtype=torch.float32,requires_grad=True)
        continuity,hydraulic,storage=physics_losses(x)
        measured=torch.zeros(())
        if obs_inputs:
            oi=rng.integers(0,len(obs_x),size=min(128,len(obs_x)))
            oc,*_=forward(obs_x[oi]);receiver=oc@torch.tensor(RECEIVE.T,dtype=torch.float32)
            measured=F.mse_loss(receiver[torch.arange(len(oi)),obs_channel[oi]],obs_y[oi])
        loss=supervised+2*continuity+2*hydraulic+10*storage+measured
        terms=(supervised,continuity,hydraulic,storage,measured)
        optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),10);optimizer.step()
        if epoch==0 or (epoch+1)%400==0 or epoch==epochs-1:
            record=dict(epoch=epoch+1,loss=float(loss.detach()),**{k:float(v.detach()) for k,v in zip(('data','continuity','hydraulic','storage','measured_receiver'),terms)})
            history.append(record);print(json.dumps(record),flush=True)
    model=dict(version='hose-height-ode-pinn-v1',network_id=config['network_id'],physics=config,
        architecture=[11,48,48,8],activation='tanh; softplus nonnegative cumulative/flow heads',
        input_order=['time_s','VA0_ml','VB0_ml','zH1_m','zH2_m','zH3_m','zH4_m','gH1','gH2','gH3','gH4'],
        output_order=['CH1','CH2','CH3','CH4','qH1','qH2','qH3','qH4'],
        scales=dict(volume_m3=VOLUME_SCALE,flow_m3_s=FLOW_SCALE),
        layers=[dict(weight=l.weight.detach().tolist(),bias=l.bias.detach().tolist()) for l in net if isinstance(l,torch.nn.Linear)],
        training=dict(seed=seed,scenarios=count,points=len(inputs),epochs=epochs,history=history,
            measured_receiver_points=len(obs_inputs),fresh_collocation_points_per_epoch=256,
            seconds=time.perf_counter()-start,physics_weights=dict(continuity=2,hydraulic=2,negative_storage=10),
            reference_solver='SciPy solve_ivp, independent of NN',
            data_sha256=hashlib.sha256(inputs.tobytes()+targets.tobytes()).hexdigest()),
        validated_on_observations=False,
        limitations=['0D graph ODE-PINN, not a spatial Navier–Stokes/transient pipe PDE solver',
            'Synthetic reference parameters until measured geometry/coefficient calibration',
            'Fixed outlet height/mask during each prediction; no automatic valve control',
            'F3 observes H2+H3 only; separate hose experiments needed for identification'])
    model['validation']=evaluate(model,32,validation_seed)
    model['test']=evaluate(model,48,test_seed)
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=Path(output).parent,suffix='.tmp',delete=False,encoding='utf-8') as file:
        json.dump(model,file,ensure_ascii=False,allow_nan=False);temporary=Path(file.name)
    temporary.replace(output)
    print(json.dumps(dict(validation=model['validation'],test=model['test']),indent=2),flush=True)
    return model

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--epochs',type=int,default=3200);parser.add_argument('--scenarios',type=int,default=160)
    parser.add_argument('--config',type=Path);parser.add_argument('--output',type=Path,default=MODEL_PATH)
    parser.add_argument('--observations',type=Path);parser.add_argument('--experiment',type=Path)
    args=parser.parse_args()
    if not 1<=args.epochs<=200000 or not 8<=args.scenarios<=10000:parser.error('학습 크기 범위 오류')
    if bool(args.observations)!=bool(args.experiment):parser.error('--observations와 --experiment를 함께 제공하세요.')
    if args.config and args.observations:parser.error('실측 학습의 물리 설정은 experiment.physics에서 읽습니다.')
    config=json.loads(args.config.read_text()) if args.config else reference_config()
    train(config,args.epochs,args.scenarios,output=args.output,
        observations=json.loads(args.observations.read_text()) if args.observations else None,
        experiment=json.loads(args.experiment.read_text()) if args.experiment else None)

if __name__=='__main__':main()
