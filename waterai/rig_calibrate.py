"""Fit four hose coefficients from calibrated receiver force observations.

Measured geometry and fixed-height run descriptions are REQUIRED. Independent
H2/H3 excitation is checked before fitting; a combined F3 trace alone cannot
identify both hoses. Fit error is not independent experimental validation.
"""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from .hardware import force_to_volume,NETWORK_ID
from .rig_twin import validate_config,validate_conditions,reference,RECEIVE

def epoch(text):
    t=datetime.fromisoformat(text.replace('Z','+00:00'))
    if t.tzinfo is None or t.utcoffset() is None:raise ValueError('실험 시작 시각의 시간대가 필요합니다.')
    return t.timestamp()

def prepare_runs(export,experiment):
    if export.get('schema')!='rig-observations/1' or export.get('rig',{}).get('id')!=NETWORK_ID:raise ValueError('실측 내보내기 형식 오류')
    if export.get('units',{}).get('force_n')!='N':raise ValueError('힘 단위는 N이어야 합니다.')
    if export.get('total',len(export['samples']))!=len(export['samples']):raise ValueError('잘린 내보내기 자료입니다. 해당 실험의 완전한 원본을 제공하세요.')
    if experiment.get('schema')!='rig-calibration/1':raise ValueError('보정 실험 형식 오류')
    if experiment.get('dataset_type') not in ('observed','synthetic-test'):raise ValueError('실측/합성 시험 출처가 필요합니다.')
    config=validate_config(experiment['physics']);runs=[];windows=[];excited=np.zeros(4,dtype=bool)
    if not isinstance(experiment.get('runs'),list) or not 4<=len(experiment['runs'])<=100:raise ValueError('개별 보정 실험은 4~100개가 필요합니다.')
    for raw in experiment['runs']:
        start=epoch(raw['start_at']);duration=raw['duration_s'];end=start+duration
        if duration<=0:raise ValueError('실험 길이 오류')
        validate_conditions(config,raw['initial_volume_ml'],raw['outlet_height_m'],raw['open_mask'],[0,duration])
        if any(start<b and end>a for a,b in windows):raise ValueError('실험 시간 구간이 겹칩니다.')
        windows.append((start,end));records=[];seen=set()
        for sample in export['samples']:
            if sample.get('kind')!='measurement' or sample.get('domain')!='rig' or sample.get('network_id')!=NETWORK_ID:continue
            channel=sample['channel_id'];cal=raw['force_calibration'].get(channel)
            if cal is None or sample['device_id']!=cal['device_id'] or 'force_n' not in sample['values']:continue
            absolute=epoch(sample['observed_at']);t=absolute-start
            if not 0<=t<=duration:continue
            key=(sample['device_id'],sample['sample_id'])
            if key in seen:continue
            seen.add(key)
            volume=force_to_volume(sample['values']['force_n'],cal['tare_n'],cal['direction'],cal.get('density_kg_m3',1000.))
            if channel not in ('F1','F3','F2'):raise ValueError('센서 채널 오류')
            records.append((t,('F1','F3','F2').index(channel),volume))
        if len(records)<3:raise ValueError('실험별 힘 관측이 부족합니다.')
        records.sort();t=np.array(sorted(set(r[0] for r in records)));index={v:i for i,v in enumerate(t)}
        if len(t)<3:raise ValueError('실험별 관측 시각이 최소 3개 필요합니다.')
        rows=np.array([(index[s],channel) for s,channel,_ in records],dtype=int)
        values=np.array([value for _,_,value in records]);gate=raw['open_mask']
        # Identification must have actual positive excitation, not just open commands.
        for hose,receiver in enumerate((0,1,1,2)):
            observed=[v for _,r,v in records if r==receiver]
            separate=(hose not in (1,2) or not gate[3-hose])
            if gate[hose] and separate and len(observed)>=3 and max(observed)-min(observed)>1e-5:excited[hose]=True
        runs.append(dict(id=raw['id'],initial_volume_ml=raw['initial_volume_ml'],outlet_height_m=raw['outlet_height_m'],
            open_mask=gate,times_s=t,rows=rows,volume_m3=values))
    if not np.all(excited):raise ValueError('독립적으로 보정하지 못한 호스: '+','.join(f'H{i+1}' for i,v in enumerate(excited) if not v))
    return config,runs

def calibrate(export,experiment):
    config,runs=prepare_runs(export,experiment);guess=np.array(config['hose_coefficient_m2_5_s'])
    def residual(logk):
        c=deepcopy(config);c['hose_coefficient_m2_5_s']=np.exp(logk).tolist();errors=[]
        for run in runs:
            r=reference(c,run['initial_volume_ml'],run['outlet_height_m'],run['open_mask'],run['times_s'])
            rows=run['rows'];errors.extend((r['receiver_m3'][rows[:,0],rows[:,1]]-run['volume_m3'])/1e-3)
        return np.asarray(errors)
    fit=least_squares(residual,np.log(guess),bounds=(np.log(guess/10),np.log(guess*10)),max_nfev=100,xtol=1e-10,gtol=1e-10,ftol=1e-10)
    rank=int(np.linalg.matrix_rank(fit.jac));condition=float(np.linalg.cond(fit.jac))
    if not fit.success or rank<4 or not np.isfinite(condition) or condition>1e8:raise ValueError('호스 계수 식별이 불안정합니다. 개별 호스 실험과 측정 범위를 확인하세요.')
    result=deepcopy(config);result['hose_coefficient_m2_5_s']=np.exp(fit.x).tolist()
    observed=experiment['dataset_type']=='observed';result['calibrated_on_observations']=observed
    result['provenance']='measured geometry and force-calibrated coefficients' if observed else 'synthetic calibration test, NOT measured apparatus'
    report=dict(schema='rig-calibration-result/1',dataset_type=experiment['dataset_type'],runs=len(runs),
        points=len(fit.fun),fit_receiver_rmse_ml=float(np.sqrt(np.mean(fit.fun**2))*1000),
        jacobian_rank=rank,jacobian_condition=condition,validated_on_observations=False,
        data_sha256=hashlib.sha256(json.dumps(export,sort_keys=True).encode()).hexdigest(),
        warning='보정에 사용한 실험의 적합 오차입니다. 별도 실험 검증 성능이 아닙니다.')
    result['calibration']=report
    return result,report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observations',type=Path,required=True);parser.add_argument('--experiment',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('runtime/rig-calibrated.json'))
    args=parser.parse_args()
    config,report=calibrate(json.loads(args.observations.read_text()),json.loads(args.experiment.read_text()))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(config,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
