"""Portable Python engine, shared by CPython server and Pyodide browser.

Units: m³, m³/day, metres. Each LP step is one hour. Fixed design heads,
instantaneously available pipes: no transient/water-quality certification.
"""
import math
import json
import hashlib
from datetime import datetime, timezone
from copy import deepcopy

REGIONS = [
 ('춘천시','51110',37.8813,127.7298),('원주시','51130',37.3422,127.9202),
 ('강릉시','51150',37.7519,128.8761),('동해시','51170',37.5247,129.1143),
 ('태백시','51190',37.1641,128.9856),('속초시','51210',38.2070,128.5918),
 ('삼척시','51230',37.4499,129.1652),('홍천군','51720',37.6972,127.8889),
 ('횡성군','51730',37.4919,127.9850),('영월군','51750',37.1836,128.4615),
 ('평창군','51760',37.3708,128.3904),('정선군','51770',37.3806,128.6607),
 ('철원군','51780',38.1467,127.3132),('화천군','51790',38.1062,127.7082),
 ('양구군','51800',38.1101,127.9897),('인제군','51810',38.0695,128.1704),
 ('고성군','51820',38.3800,128.4678),('양양군','51830',38.0754,128.6189)]
SCENARIOS = {
 'normal':('정상',1.,1.,1.),'dry':('장기 강수 감소',.55,1.1,1.),
 'prolonged':('30일 이상 강수 부족',.3,1.15,1.),'heat':('극심한 고온',.7,1.45,1.),
 'inflow':('저수지 유입 급감',.1,1.1,1.),'outage':('공급 시설 제한',.8,1.1,.35),
 'compound':('동시 극한 가뭄',.15,1.6,.55)}
RATES = {'농업':{'벼':.006,'채소':.004,'과일':.0035},
 '축산업':{'소':.0008,'돼지':.0012},'공업':{'식품':.008,'제조':.003},
 '일반':{'주거':.001,'상업':.0015,'의료':.003}}

def demand(user):
    category=user.get('category','농업'); sub=user.get('subtypes',['채소'])
    if category not in RATES or not sub or any(s not in RATES[category] for s in sub):
        raise ValueError('업종과 세부 분류를 확인하세요.')
    area=float(user.get('area',0))
    if not math.isfinite(area) or area<=0: raise ValueError('면적은 0보다 큰 유한한 숫자여야 합니다.')
    estimate=area*sum(RATES[category][s] for s in sub)/len(sub)
    basis='복수 분류 면적을 균등 분배한 시험용 원단위'
    if category=='축산업' and user.get('heads'):
        estimate=float(user['heads'])*sum({'소':.07,'돼지':.015}[s] for s in sub)/len(sub)
        basis='입력 마릿수 × 시험용 음용·세척 원단위'
    if user.get('actual_usage') is not None:
        estimate=float(user['actual_usage']);basis='이용자가 입력한 실제 사용량 (미검증)'
    if not math.isfinite(estimate) or estimate<0: raise ValueError('사용량을 확인하세요.')
    return dict(value=estimate,lower=estimate*.6,upper=estimate*1.5,basis=basis,
                warning='업종 대비 큰 면적입니다. 단위를 확인하세요.' if area>({'농업':1e6,'축산업':1e5,'공업':2e5,'일반':5e4}[category]) else '')

def locate(region_id):
    r=next((r for r in REGIONS if r[1]==region_id),None)
    if r is None: raise ValueError('강원특별자치도 지역을 선택하세요.')
    return dict(name=r[0],region_id=r[1],latitude=r[2],longitude=r[3],
                grid_id=f'{math.floor(r[2]/.05)}:{math.floor(r[3]/.05)}',
                accuracy='시군 중심점, 상세 주소 지오코딩 아님')

def demo_network():
    nodes=[];edges=[]
    for i,r in enumerate(REGIONS):
        nodes.append(dict(id=r[1],name=r[0],latitude=r[2],longitude=r[3],
                          elevation_m=30+(i%5)*15,head_m=100+(i%5)*15,node_type='demand',
                          demand_m3_day=220+i*24,essential_demand_m3_day=(220+i*24)*.6))
    for j in range(3):
        r=REGIONS[j*6]; sid=f'S{j}'
        nodes.append(dict(id=sid,name=f'가상 공급원 {j+1}',latitude=r[2]+.08,longitude=r[3]-.08,
                          elevation_m=180,head_m=330,node_type='source',
                          reservoir_storage_m3=4200+j*1200,inflow_m3_day=2100+j*200,
                          max_supply_m3_day=3400+j*400))
        for i in range(j*6,j*6+6):
            edges.append(dict(id=f'E{i}',from_node=sid,to_node=REGIONS[i][1],
              pipe_length_m=1600+(i%6)*600,pipe_diameter_mm=90+(i%3)*20,
              pipe_roughness=120,pipe_max_flow_m3_day=350+(i%3)*90,loss_fraction=.04,
              kind='pressure',cost=1+(i%6)*.1,reliability=.95,provenance='synthetic'))
    for j in range(2):
        edges.append(dict(id=f'T{j}',from_node=f'S{j}',to_node=f'S{j+1}',pipe_length_m=4000,
          pipe_diameter_mm=180,pipe_roughness=120,pipe_max_flow_m3_day=500,
          loss_fraction=.03,kind='pressure',cost=2,reliability=.92,provenance='synthetic'))
    return dict(nodes=nodes,edges=edges,provenance='synthetic',version='gangwon-demo-v1',
                timestamp='2026-10-20T00:00:00Z')

def validate_network(network):
    nodes=network['nodes']; ids=[n['id'] for n in nodes]
    if not nodes or len(ids)!=len(set(ids)): raise ValueError('노드 ID는 고유해야 합니다.')
    if len(nodes)>250 or len(network['edges'])>1000: raise ValueError('시험판 입력 규모 초과')
    for n in nodes:
        for k in ('demand_m3_day','essential_demand_m3_day','reservoir_storage_m3','inflow_m3_day','max_supply_m3_day'):
            x=float(n.get(k,0))
            if not math.isfinite(x) or x<0: raise ValueError(f'{k}: 음수/비유한 값')
        if n.get('essential_demand_m3_day',0)>n.get('demand_m3_day',0): raise ValueError('필수 수요가 총 수요보다 큽니다.')
    for e in network['edges']:
        if e['from_node'] not in ids or e['to_node'] not in ids: raise ValueError('미등록 노드 연결')
        if e['from_node']==e['to_node']: raise ValueError('자기 연결 불가')
        for k in ('pipe_length_m','pipe_diameter_mm','pipe_roughness','pipe_max_flow_m3_day'):
            v=float(e[k])
            if not math.isfinite(v) or v<=0: raise ValueError(f'{k}: 양수 필요')
        if not 0<=e.get('loss_fraction',0)<1: raise ValueError('손실률 범위 오류')
        if e.get('kind') not in ('pressure','open'): raise ValueError('수로 종류 오류')
    return network

def hydraulic_capacity(e,nodes):
    """Fixed-head steady Hazen-Williams or rectangular Manning, m³/day."""
    a=nodes[e['from_node']];b=nodes[e['to_node']]
    dh=max(0,a['head_m']-b['head_m'])
    d=e['pipe_diameter_mm']/1000;length=e['pipe_length_m']
    if e['kind']=='pressure':
        q=(dh*e['pipe_roughness']**1.852*d**4.87/(10.67*length))**(1/1.852) if dh else 0
    else:
        width=e.get('channel_width_m',d);depth=e.get('channel_depth_m',d/2)
        area=width*depth;radius=area/(width+2*depth)
        q=area*radius**(2/3)*math.sqrt(dh/length)/e.get('manning_n',.035)
    return min(q*86400,e['pipe_max_flow_m3_day'])

def features(rain30,temp,month):
    return [rain30/100,temp/20,math.sin(2*math.pi*month/12),math.cos(2*math.pi*month/12)]

def predict(model,rain30,temp,month):
    x=features(rain30,temp,month);out={}
    for h,m in model['horizons'].items():
        z=m['bias']+sum(a*b for a,b in zip(x,m['weights']))
        out[h]=1/(1+math.exp(-max(-35,min(35,z))))
    return out

def allocate(network,storage,scenario,extra=None):
    """Global lossy flow LP: lexicographic essential, total, then cost."""
    import numpy as np
    from scipy.optimize import linprog
    ns=network['nodes'];es=network['edges'];by={n['id']:n for n in ns}
    sources=[n for n in ns if n['node_type']=='source']; demands=[n for n in ns if n['node_type']=='demand']
    _,inflow_mult,demand_mult,cap_mult=SCENARIOS[scenario]
    E=len(es);S=len(sources);D=len(demands);N=E+S+2*D
    bounds=[];caps=[]
    for e in es:
        cap=hydraulic_capacity(e,by)*cap_mult/24;caps.append(cap);bounds.append((0,cap))
    for s in sources:
        avail=storage[s['id']]+s['inflow_m3_day']*inflow_mult/24
        bounds.append((0,min(avail,s['max_supply_m3_day']*cap_mult/24)))
    requested=[];essential=[]
    for n in demands:
        add=(extra or {}).get(n['id'],0)
        requested.append((n['demand_m3_day']+add)*demand_mult/24)
        essential.append((n['essential_demand_m3_day']+add*.6)*demand_mult/24)
    bounds += [(0,v) for v in essential]
    bounds += [(0,max(0,t-e)) for t,e in zip(requested,essential)]
    A=np.zeros((len(ns),N))
    for i,n in enumerate(ns):
        for j,e in enumerate(es):
            if e['from_node']==n['id']:A[i,j]-=1
            if e['to_node']==n['id']:A[i,j]+=1-e.get('loss_fraction',0)
        for j,s in enumerate(sources):
            if s['id']==n['id']:A[i,E+j]=1
        for j,d in enumerate(demands):
            if d['id']==n['id']:A[i,E+S+j]=-1;A[i,E+S+D+j]=-1
    c1=np.zeros(N);c1[E+S:E+S+D]=-1
    result=linprog(c1,A_eq=A,b_eq=np.zeros(len(ns)),bounds=bounds,method='highs')
    if not result.success: raise RuntimeError('필수 수요 최적화 실패: '+result.message)
    Aub=[c1];bub=[float(c1@result.x)+1e-7]
    c2=c1.copy();c2[E+S+D:]=-1
    result=linprog(c2,A_ub=Aub,b_ub=bub,A_eq=A,b_eq=np.zeros(len(ns)),bounds=bounds,method='highs')
    if not result.success:raise RuntimeError('총 수요 최적화 실패')
    Aub.append(c2);bub.append(float(c2@result.x)+1e-7)
    cost=np.zeros(N)
    for j,e in enumerate(es):cost[j]=max(.001,e.get('cost',1))+1-e.get('reliability',1)
    result=linprog(cost,A_ub=Aub,b_ub=bub,A_eq=A,b_eq=np.zeros(len(ns)),bounds=bounds,method='highs')
    if not result.success:raise RuntimeError('비용 최적화 실패')
    x=result.x
    return dict(flows=[float(v) for v in x[:E]],withdrawals={s['id']:float(x[E+j]) for j,s in enumerate(sources)},
        delivered={d['id']:float(x[E+S+j]+x[E+S+D+j]) for j,d in enumerate(demands)},
        essential_delivered={d['id']:float(x[E+S+j]) for j,d in enumerate(demands)},
        requested=dict(zip([d['id'] for d in demands],requested)),essential=dict(zip([d['id'] for d in demands],essential)),
        mass_balance_residual=float(np.max(np.abs(A@x))),cost=float(cost@x),capacities=caps)

def terrain_path(start,end,blocked=None):
    """A* over explicitly synthetic GIS grid; excludes blocked cells."""
    import heapq
    blocked=set(blocked or []);queue=[(0,start)];prev={};dist={start:0}
    def elevation(p):return 30+2*p[0]+8*math.sin(p[1]/3)
    while queue:
        _,p=heapq.heappop(queue)
        if p==end:
            path=[p]
            while p in prev:p=prev[p];path.append(p)
            return list(reversed(path)),dist[end]
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            n=(p[0]+dx,p[1]+dy)
            if not(0<=n[0]<24 and 0<=n[1]<24) or n in blocked:continue
            c=dist[p]+1+abs(elevation(n)-elevation(p))*.2
            if c<dist.get(n,1e30):
                dist[n]=c;prev[n]=p
                heapq.heappush(queue,(c+abs(end[0]-n[0])+abs(end[1]-n[1]),n))
    return [],None

def simulate(network=None,scenario='normal',users=None,model=None,weather=None,hours=48,make_plans=True):
    if scenario not in SCENARIOS:raise ValueError('알 수 없는 시나리오')
    network=validate_network(deepcopy(network or demo_network()));users=users or []
    if not 1<=hours<=2160:raise ValueError('시간 범위 오류')
    extra={}
    for u in users:
        locate(u['region_id']);extra[u['region_id']]=extra.get(u['region_id'],0)+demand(u)['value']
    storage={n['id']:n['reservoir_storage_m3'] for n in network['nodes'] if n['node_type']=='source'}
    ds=[n for n in network['nodes'] if n['node_type']=='demand'];tot={n['id']:dict(supply=0,essential_supply=0,demand=0,essential=0,first_shortage_hour=None) for n in ds}
    timeline=[];edge_tot=[0.]*len(network['edges']);max_res=0
    for hour in range(hours):
        step=allocate(network,storage,scenario,extra);max_res=max(max_res,step['mass_balance_residual'])
        for sid in storage:
            source=next(n for n in network['nodes'] if n['id']==sid)
            storage[sid]=max(0,storage[sid]+source['inflow_m3_day']*SCENARIOS[scenario][1]/24-step['withdrawals'][sid])
        for j,v in enumerate(step['flows']):edge_tot[j]+=v
        for n in ds:
            k=n['id'];t=tot[k];t['supply']+=step['delivered'][k];t['essential_supply']+=step['essential_delivered'][k]
            t['demand']+=step['requested'][k];t['essential']+=step['essential'][k]
            if step['essential_delivered'][k]<step['essential'][k]-1e-5 and t['first_shortage_hour'] is None:t['first_shortage_hour']=hour
        timeline.append(dict(hour=hour+1,storage_m3=sum(storage.values()),supply_m3=sum(step['delivered'].values()),shortage_m3=sum(step['requested'].values())-sum(step['delivered'].values())))
    rows=[]
    for n in ds:
        t=tot[n['id']];w=(weather or {}).get(n['id'],dict(rainfall_30d_mm=65,temperature_c=24,month=8))
        risk=predict(model,w['rainfall_30d_mm'],w['temperature_c'],w['month']) if model else None
        shortage=max(0,t['demand']-t['supply']);vulnerability=shortage/max(t['demand'],1e-9)
        rows.append(dict(id=n['id'],name=n['name'],latitude=n['latitude'],longitude=n['longitude'],**t,
            shortage=shortage,coverage=t['essential_supply']/t['essential'] if t['essential'] else 1,
            risk=risk,vulnerability=vulnerability,priority=100*(.4*(risk or {}).get('30',.5)+.6*vulnerability)))
    rows.sort(key=lambda r:-r['priority'])
    plans=[]
    if make_plans:
        for r in [r for r in rows if r['coverage']<.999][:3]:
            candidates=[]
            for s in [n for n in network['nodes'] if n['node_type']=='source']:
                def cell(lat,lon):
                    return (max(0,min(23,round((lon-127.1)/2.35*23))),max(0,min(23,round((lat-37)/1.6*23))))
                target_node=next(n for n in network['nodes'] if n['id']==r['id'])
                start=cell(s['latitude'],s['longitude']);end=cell(target_node['latitude'],target_node['longitude'])
                blocked={(10,y) for y in range(15)}-{start,end}
                path,c=terrain_path(start,end,blocked=blocked)
                if not path:continue
                geometry=[dict(latitude=37+y/23*1.6,longitude=127.1+x/23*2.35) for x,y in path]
                geometry=[dict(latitude=s['latitude'],longitude=s['longitude'])]+geometry+[dict(latitude=r['latitude'],longitude=r['longitude'])]
                def metres(a,b):
                    dy=math.radians(b['latitude']-a['latitude']);dx=math.radians(b['longitude']-a['longitude'])
                    h=math.sin(dy/2)**2+math.cos(math.radians(a['latitude']))*math.cos(math.radians(b['latitude']))*math.sin(dx/2)**2
                    return 6371000*2*math.asin(min(1,math.sqrt(h)))
                length=max(100,sum(metres(a,b) for a,b in zip(geometry,geometry[1:])))
                e=dict(id='candidate',from_node=s['id'],to_node=r['id'],pipe_length_m=length,
                       pipe_diameter_mm=200,pipe_roughness=120,pipe_max_flow_m3_day=1200,
                       loss_fraction=.05,kind='pressure',cost=4,reliability=.9)
                changed=deepcopy(network);changed['edges'].append(e)
                after=simulate(changed,scenario,users,model,weather,hours,False)
                improvement=max(0,sum(x['shortage'] for x in rows)-after['shortage_m3'])
                target=next(x for x in after['regions'] if x['id']==r['id'])
                cost=length*650000
                candidates.append(dict(source=s['id'],target=r['id'],region=r['name'],length_m=length,
                   diameter_mm=200,capacity_m3_day=hydraulic_capacity(e,{n['id']:n for n in network['nodes']}),
                   cost_krw=cost,improvement_m3=improvement,target_improvement_m3=max(0,r['shortage']-target['shortage']),
                   priority=r['priority']*improvement/max(cost/1e8,1),terrain_path=path,geometry=geometry,
                   explanation=f'전체 공급망 재최적화 후 {improvement:.1f} m³ 부족 감소. 지형·토지이용은 가상 격자이며 현장 검증 필요.',
                   status='AI 자동 제안',provenance='synthetic GIS / screening only'))
            useful=[c for c in candidates if c['improvement_m3']>1e-3]
            if useful:
                best=deepcopy(max(useful,key=lambda c:c['priority']));best['alternatives']=candidates;plans.append(best)
    payload=dict(scenario=scenario,hours=hours,regions=rows,timeline=timeline,plans=sorted(plans,key=lambda p:-p['priority']),
       edges=[dict(**e,volume_m3=edge_tot[i]) for i,e in enumerate(network['edges'])],nodes=network['nodes'],
       demand_m3=sum(t['demand'] for t in tot.values()),supply_m3=sum(t['supply'] for t in tot.values()),
       shortage_m3=sum(max(0,t['demand']-t['supply']) for t in tot.values()),
       mass_balance_residual=max_res,final_storage=storage,data_provenance=network.get('provenance','unverified'),
       assumptions=['고정 설계 수두 정상류, 수격·충수 지연 미포함','48시간 물량 및 시간별 필수 수요 평가','가상 공급망·시험용 수요 원단위','후보별 독립 비교: 복수 후보 동시 효과 합산 금지'],
       model_version=(model or {}).get('version','unavailable'),created_at=datetime.now(timezone.utc).isoformat())
    payload['input_hash']=hashlib.sha256(json.dumps(dict(network=network,scenario=scenario,users=users,weather=weather,model=model,hours=hours),sort_keys=True).encode()).hexdigest()
    return payload
