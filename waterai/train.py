"""Collect NASA POWER gridded estimates; train with a 90-day embargo.
Run: python -m waterai.train --fetch (network required), or --synthetic.
Synthetic mode is explicit and never silently substituted for real data.
"""
import argparse,csv,json,math,random,gzip
from pathlib import Path
from datetime import date,timedelta,datetime,timezone
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss,precision_score,recall_score,f1_score
from sklearn.calibration import calibration_curve
from .core import REGIONS,features
ROOT=Path(__file__).resolve().parents[1]

def collect():
    import requests
    rows=[];metadata=[]
    for name,rid,lat,lon in [REGIONS[i] for i in (0,2,4)]:
        params=dict(parameters='PRECTOTCORR,T2M',community='AG',latitude=lat,longitude=lon,
                    start='20150101',end='20251231',format='JSON')
        r=requests.get('https://power.larc.nasa.gov/api/temporal/daily/point',params=params,timeout=120)
        r.raise_for_status();data=r.json();p=data['properties']['parameter']
        metadata.append(dict(region_id=rid,url=r.url,header=data.get('header'),geometry=data.get('geometry')))
        for day,rain in p['PRECTOTCORR'].items():
            temp=p['T2M'].get(day,-999)
            if rain<=-900 or temp<=-900:continue
            rows.append(dict(date=date(int(day[:4]),int(day[4:6]),int(day[6:8])).isoformat(),region_id=rid,
              precipitation_mm=rain,temperature_c=temp,provenance='NASA POWER gridded estimate',imputed=False))
    (ROOT/'data/weather_metadata.json').write_text(json.dumps(dict(sources=metadata,retrieved_at=datetime.now(timezone.utc).isoformat(),
      spatial_resolution='Native POWER meteorological grid, not station observations',missing='missing rows omitted; windows with gaps rejected'),ensure_ascii=False,indent=2),encoding='utf-8')
    return rows

def synthetic():
    rng=random.Random(42);rows=[]
    for rid in [r[1] for r in REGIONS[:3]]:
        for i in range(4018):
            d=date(2015,1,1)+timedelta(days=i);season=math.sin((d.month-4)*math.pi/6)
            rain=max(0,rng.expovariate(1/(2.3+1.8*season))) if rng.random()<.4 else 0
            rows.append(dict(date=d.isoformat(),region_id=rid,precipitation_mm=rain,
              temperature_c=12+14*season+rng.gauss(0,3),provenance='synthetic',imputed=False))
    return rows

def train(rows):
    horizons={};report={};groups={}
    for row in rows:groups.setdefault(row['region_id'],[]).append(row)
    for horizon in (7,30,90):
        xx=[];yy=[];dates=[]
        for group in groups.values():
            group=sorted(group,key=lambda r:r['date']);rain=[float(r['precipitation_mm']) for r in group]
            for i in range(29,len(group)-horizon):
                d=date.fromisoformat(group[i]['date'])
                if (date.fromisoformat(group[i+horizon]['date'])-date.fromisoformat(group[i-29]['date'])).days!=horizon+29:continue
                # Research label: mean precipitation below 0.8 mm/day in NEXT h days.
                xx.append(features(sum(rain[i-29:i+1]),float(group[i]['temperature_c']),d.month))
                yy.append(int(sum(rain[i+1:i+1+horizon])/horizon<.8));dates.append(d)
        X=np.asarray(xx);y=np.asarray(yy)
        tr=np.array([d<date(2022,10,3) for d in dates]) # last label ends before 2023
        va=np.array([date(2023,1,1)<=d<date(2023,10,3) for d in dates])
        te=np.array([date(2024,1,1)<=d<date(2025,10,3) for d in dates])
        if any(len(set(y[m]))<2 for m in (tr,va,te)):raise ValueError('학습/검증/시험에 두 클래스 모두 필요합니다.')
        estimator=LogisticRegression(C=1,max_iter=2000).fit(X[tr],y[tr])
        # Platt calibration fit only on 2023, then fold into portable coefficients.
        calibrator=LogisticRegression(C=10).fit(estimator.decision_function(X[va]).reshape(-1,1),y[va])
        scale=float(calibrator.coef_[0,0]);weights=(estimator.coef_[0]*scale).tolist()
        bias=float(estimator.intercept_[0]*scale+calibrator.intercept_[0])
        p=1/(1+np.exp(-np.clip(X[te]@weights+bias,-35,35)));pred=p>=.5
        fraction,means=calibration_curve(y[te],p,n_bins=5,strategy='uniform')
        horizons[str(horizon)]=dict(weights=weights,bias=bias)
        report[str(horizon)]=dict(brier=float(brier_score_loss(y[te],p)),precision=float(precision_score(y[te],pred,zero_division=0)),
          recall=float(recall_score(y[te],pred,zero_division=0)),f1=float(f1_score(y[te],pred,zero_division=0)),
          baseline_brier=float(brier_score_loss(y[te],np.full(sum(te),y[tr].mean()))),
          train_samples=int(sum(tr)),validation_samples=int(sum(va)),test_samples=int(sum(te)),
          calibration=dict(predicted=means.tolist(),observed=fraction.tolist()))
    model=dict(version='power-logistic-v1',horizons=horizons,features=['rain30/100','temperature/20','sin_month','cos_month'],
      label='향후 기간 평균 강수량 < 0.8 mm/day (연구용 강수 부족 지표, 공식 가뭄 진단 아님)',
      provenance=sorted(set(r['provenance'] for r in rows)),training_end='2022-10-02',calibration_year=2023,test_period='2024-01-01..2025-10-02',
      embargo_days=90,limitations=['세 지점 격자 추정치; 강원도 전역 공간 검증 미완료','농업·수문학적 가뭄 모델 별도 필요','실시간 기상예보 모델 아님'])
    (ROOT/'models/risk.json').write_text(json.dumps(model,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'models/metrics.json').write_text(json.dumps(dict(risk=report,data=model['provenance'],split=model['test_period']),ensure_ascii=False,indent=2),encoding='utf-8')
    # Historical snapshot only. Do not call this current weather.
    latest={}
    for rid,g in groups.items():
        g=sorted(g,key=lambda r:r['date']);r=g[-1]
        latest[rid]=dict(rainfall_30d_mm=sum(float(r['precipitation_mm']) for r in g[-30:]),
          temperature_c=float(r['temperature_c']),month=date.fromisoformat(r['date']).month,date=r['date'],provenance=r['provenance'])
    (ROOT/'data/weather_snapshot.json').write_text(json.dumps(latest,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--fetch',action='store_true');parser.add_argument('--synthetic',action='store_true');parser.add_argument('--csv')
    a=parser.parse_args()
    if a.fetch:rows=collect()
    elif a.synthetic:rows=synthetic()
    elif a.csv:
        opener=gzip.open if a.csv.endswith('.gz') else open
        with opener(a.csv,'rt',encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    else:parser.error('--fetch, --synthetic 또는 --csv 필요')
    if not a.csv:
        with open(ROOT/'data/weather.csv','w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print(json.dumps(train(rows),indent=2))
if __name__=='__main__':main()
