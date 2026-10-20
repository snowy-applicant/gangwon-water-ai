"""Measured observations for the physical rig, separate from provincial simulations.

Force is retained in N. It is NOT silently relabelled as pressure or flow.
No device driver, valve command, synthetic measurement or scale conversion here.
"""
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

RIG = json.loads((Path(__file__).resolve().parents[1] / 'hardware/rig.json').read_text())
NETWORK_ID = RIG['id']
CHANNELS = {c['id'] for c in RIG['channels']}
UNITS = {'force_n':'N', 'volume_m3':'m3', 'flow_m3_s':'m3/s', 'head_m':'m'}
LIMITS = {'force_n':(-1e6,1e6), 'volume_m3':(0,100), 'flow_m3_s':(-1,1), 'head_m':(-100,100)}

def identifier(value, name):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value):
        raise ValueError(name + ': 영문/숫자/_/- 식별자가 필요합니다.')
    return value

def validate_sample(data, now=None):
    if not isinstance(data,dict):raise ValueError('JSON 객체가 필요합니다.')
    allowed={'sample_id','device_id','network_id','domain','channel_id','observed_at','kind','values'}
    if set(data)-allowed:raise ValueError('알 수 없는 측정 필드입니다.')
    if data.get('domain')!='rig' or data.get('network_id')!=NETWORK_ID:
        raise ValueError('이 수신 경로는 hose-height-rig-v1 탁상 실험 전용입니다.')
    normalized={k:identifier(data.get(k),k) for k in ('sample_id','device_id','channel_id')}
    raw=data.get('observed_at')
    if not isinstance(raw,str) or len(raw)>40:raise ValueError('시간대를 포함한 observed_at이 필요합니다.')
    try:t=datetime.fromisoformat(raw.replace('Z','+00:00'))
    except ValueError:raise ValueError('관측 시각 형식 오류') from None
    if t.tzinfo is None or t.utcoffset() is None:raise ValueError('관측 시각의 시간대가 필요합니다.')
    if t.timestamp()>(now or datetime.now(timezone.utc)).timestamp()+60:
        raise ValueError('관측 시각이 미래입니다.')
    values=data.get('values');kind=data.get('kind')
    if not isinstance(values,dict) or not values:raise ValueError('측정 values가 필요합니다.')
    if kind=='measurement':
        if normalized['channel_id'] not in CHANNELS:raise ValueError('F1/F3/F2 측정 채널을 선택하세요.')
        if set(values)-set(UNITS):raise ValueError('지원하지 않는 측정값 또는 단위입니다.')
        for key,value in values.items():
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ValueError('측정값은 유한한 숫자여야 합니다.')
            if not LIMITS[key][0]<=value<=LIMITS[key][1]:raise ValueError('측정값의 범위를 확인하세요.')
    elif kind=='controller_status':
        if normalized['channel_id']!='controller':raise ValueError('제어기 채널 오류')
        if set(values)!={'uptime_ms','active','latched','armed','heartbeat','calibrated'}:
            raise ValueError('제어기 상태 필드 오류')
        if type(values['uptime_ms']) is not int or not 0<=values['uptime_ms']<=2**32-1:
            raise ValueError('제어기 시간 오류')
        if not isinstance(values['active'],list) or len(values['active'])!=4 or any(type(v) is not bool for v in values['active']):
            raise ValueError('호스 4개 상태가 필요합니다.')
        if any(type(values[k]) is not bool for k in ('latched','armed','heartbeat')):raise ValueError('제어기 플래그 오류')
        if type(values['calibrated']) is not int or not 0<=values['calibrated']<=15:raise ValueError('보정 상태 오류')
    else:raise ValueError('측정 종류 오류')
    return dict(**normalized,network_id=NETWORK_ID,domain='rig',observed_at=t.astimezone(timezone.utc).isoformat(),kind=kind,values=values)

def force_to_volume(force_n, tare_n, direction, density_kg_m3=1000.0):
    """Calibrated vertical support load -> contained water volume, not pipe pressure.

    direction must be measured as +1 or -1 for this physical mounting.
    Negative net weight is rejected, never turned into a plausible positive volume.
    """
    for value in (force_n,tare_n,density_kg_m3):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('힘 보정값 오류')
    if type(direction) is not int or direction not in (-1,1) or density_kg_m3<=0:raise ValueError('방향/밀도 보정 오류')
    weight=direction*(force_n-tare_n)
    if weight<0:raise ValueError('영점 또는 센서 방향을 확인하세요.')
    return weight/(9.80665*density_kg_m3)

def volume_rate(points):
    """Least-squares volume slope in m³/s. Needs monotonic elapsed seconds.

    A receiver that is drained, touched or removed invalidates the window; callers
    must split those runs. A negative slope is rejected instead of clamped to zero.
    """
    if len(points)<3:raise ValueError('유량 추정에는 최소 3개 시간 표본이 필요합니다.')
    if any(not math.isfinite(v) for p in points for v in p):raise ValueError('유한한 표본이 필요합니다.')
    if any(b[0]<=a[0] for a,b in zip(points,points[1:])):raise ValueError('시각은 증가해야 합니다.')
    start=points[0][0];ts=[p[0]-start for p in points];vs=[p[1] for p in points]
    mt=sum(ts)/len(ts);mv=sum(vs)/len(vs)
    rate=sum((t-mt)*(v-mv) for t,v in zip(ts,vs))/sum((t-mt)**2 for t in ts)
    if rate<0:raise ValueError('받는 용기를 비우거나 움직인 구간을 분리하세요.')
    return rate
