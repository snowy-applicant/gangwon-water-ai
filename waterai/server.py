"""Small Python/SQLite research service; deploy behind an HTTPS reverse proxy.
Never serves project files outside web/. Admin data requires bearer token.
"""
import os,json,sqlite3,secrets,time,hashlib,threading
from pathlib import Path
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from contextlib import contextmanager
from .core import simulate,demand,locate
from .hardware import validate_sample,RIG,UNITS
from .rig_pinn import load_model as load_rig_model,predict as predict_rig
ROOT=Path(__file__).resolve().parents[1]
DB=Path(os.environ.get('WATERAI_DB',str(ROOT/'runtime/waterai.sqlite3')))
TOKEN=os.environ.get('WATERAI_ADMIN_TOKEN','')
SENSOR_TOKEN=os.environ.get('WATERAI_SENSOR_TOKEN','')
LOCK=threading.Lock()

@contextmanager
def connect():
    DB.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DB,timeout=20)
    db.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, reporter TEXT, region TEXT, timestamp REAL, payload TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, timestamp REAL, payload TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, status TEXT, timestamp REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS hardware (sequence INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT, sample TEXT, channel TEXT, received REAL, payload TEXT, UNIQUE(device,sample))')
    try:
        with db:
            yield db
    finally:
        db.close()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'web'),**kw)
    def log_message(self,fmt,*a):pass # avoid storing addresses/auth headers in logs
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
        super().end_headers()
    def send_json(self,status,data):
        body=json.dumps(data,ensure_ascii=False).encode();self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def admin(self):
        provided=self.headers.get('Authorization','').removeprefix('Bearer ')
        if not TOKEN or not secrets.compare_digest(provided.encode(),TOKEN.encode()):self.send_json(401,dict(error='관리자 인증이 필요합니다.'));return False
        return True
    def same_origin(self):
        origin=self.headers.get('Origin')
        if origin:
            from urllib.parse import urlparse
            if urlparse(origin).netloc!=self.headers.get('Host'):
                self.send_json(403,dict(error='교차 출처 요청 금지'));return False
        return True
    def sensor(self):
        provided=self.headers.get('Authorization','').removeprefix('Bearer ')
        if not SENSOR_TOKEN or not secrets.compare_digest(provided.encode(),SENSOR_TOKEN.encode()):
            self.send_json(401,dict(error='장치 인증이 필요합니다.'));return False
        return True
    def do_GET(self):
        path=self.path.split('?')[0]
        if path=='/api/health':return self.send_json(200,dict(mode='server',admin_configured=bool(TOKEN),sensor_configured=bool(SENSOR_TOKEN)))
        if path=='/api/rig-pinn':
            if not self.admin():return
            try:model=load_rig_model()
            except (OSError,ValueError):return self.send_json(503,dict(error='실험 PINN 모델이 없거나 유효하지 않습니다.'))
            return self.send_json(200,{k:model[k] for k in ('version','network_id','physics','architecture','training','validation','test','validated_on_observations','limitations')})
        if path in ('/api/hardware','/api/hardware/export'):
            if not self.admin():return
            with connect() as db:
                if path.endswith('/export'):
                    # Ascending sequence retains exact ingest order; timestamps may
                    # be delayed/out of order when replaying recorded experiments.
                    rows=db.execute('SELECT sequence,received,payload FROM hardware ORDER BY sequence DESC LIMIT 10000').fetchall()[::-1]
                    return self.send_json(200,dict(schema='rig-observations/1',rig=RIG,units=UNITS,
                        samples=[dict(sequence=s,received_at=t,**json.loads(p)) for s,t,p in rows],
                        limit=10000,total=db.execute('SELECT COUNT(*) FROM hardware').fetchone()[0],
                        note='Raw experimental observations, not PINN predictions or Gangwon field data.'))
                rows=db.execute('SELECT sequence,received,payload FROM (SELECT *,ROW_NUMBER() OVER (PARTITION BY device,channel ORDER BY json_extract(payload,\'$.observed_at\') DESC,sequence DESC) AS rank FROM hardware) WHERE rank=1 ORDER BY sequence DESC LIMIT 100').fetchall()
                now=time.time()
                from datetime import datetime
                samples=[]
                for s,t,p in rows:
                    sample=json.loads(p);age=max(0,now-datetime.fromisoformat(sample['observed_at']).timestamp())
                    samples.append(dict(**sample,sequence=s,received_at=t,age_seconds=age,stale=age>5))
                return self.send_json(200,dict(rig=RIG,units=UNITS,samples=samples,
                    latest_sequence=db.execute('SELECT COALESCE(MAX(sequence),0) FROM hardware').fetchone()[0]))
        if path in ('/api/reports','/api/runs','/api/plans'):
            if not self.admin():return
            with connect() as db:
                if path=='/api/plans':data=[dict(id=a,status=b,timestamp=c) for a,b,c in db.execute('SELECT id,status,timestamp FROM plans ORDER BY timestamp DESC')]
                else:data=[json.loads(r[0]) for r in db.execute('SELECT payload FROM '+('reports' if path.endswith('reports') else 'runs')+' ORDER BY timestamp DESC LIMIT 100')]
            return self.send_json(200,data)
        if path.startswith('/api/'):return self.send_json(404,dict(error='Unknown endpoint'))
        return super().do_GET()
    def do_POST(self):
        if not self.same_origin():return
        if self.path=='/api/hardware' and not self.sensor():return
        if self.path=='/api/rig-pinn/predict' and not self.admin():return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=2_000_000:return self.send_json(413,dict(error='요청 크기 초과'))
            if self.path in ('/api/hardware','/api/rig-pinn/predict') and size>16384:return self.send_json(413,dict(error='장치/모델 요청은 16 KB 이하이어야 합니다.'))
            data=json.loads(self.rfile.read(size));now=time.time()
            if self.path=='/api/rig-pinn/predict':
                required={'initial_volume_ml','outlet_height_m','open_mask','times_s'}
                if not isinstance(data,dict) or set(data)!=required:raise ValueError('실험 PINN 입력 필드를 확인하세요.')
                try:model=load_rig_model()
                except (OSError,ValueError):return self.send_json(503,dict(error='실험 PINN 모델이 없거나 유효하지 않습니다.'))
                return self.send_json(200,predict_rig(model,**data))
            if self.path=='/api/hardware':
                sample=validate_sample(data);encoded=json.dumps(sample,sort_keys=True,ensure_ascii=False,allow_nan=False)
                with LOCK,connect() as db:
                    existing=db.execute('SELECT sequence,payload FROM hardware WHERE device=? AND sample=?',(sample['device_id'],sample['sample_id'])).fetchone()
                    if existing:
                        if existing[1]!=encoded:return self.send_json(409,dict(error='동일 sample_id의 내용이 다릅니다.'))
                        response=dict(accepted=True,duplicate=True,sequence=existing[0])
                    else:
                        cursor=db.execute('INSERT INTO hardware (device,sample,channel,received,payload) VALUES (?,?,?,?,?)',
                            (sample['device_id'],sample['sample_id'],sample['channel_id'],now,encoded))
                        response=dict(accepted=True,duplicate=False,sequence=cursor.lastrowid)
                return self.send_json(200 if response['duplicate'] else 201,response)
            if self.path=='/api/reports':
                locate(data['region_id']);severity=int(data['severity']);text=str(data['text']).strip()
                if severity not in (1,2,3) or not 1<=len(text)<=1000:raise ValueError('신고 입력 오류')
                reporter=hashlib.sha256(str(data['reporter']).encode()).hexdigest()
                with LOCK,connect() as db:
                    existing=db.execute('SELECT id FROM reports WHERE reporter=? AND region=? AND timestamp>?',(reporter,data['region_id'],now-86400)).fetchone()
                    if existing:return self.send_json(409,dict(error='24시간 이내 중복 신고: 가중치가 중복되지 않습니다.'))
                    payload=dict(id=secrets.token_hex(12),region_id=data['region_id'],severity=severity,text=text,timestamp=now*1000,score=30+20*severity)
                    db.execute('INSERT INTO reports VALUES (?,?,?,?,?)',(payload['id'],reporter,data['region_id'],now,json.dumps(payload)))
                return self.send_json(201,dict(id=payload['id'],accepted=True))
            if self.path=='/api/runs':
                if not self.admin():return
                args=data.get('input',{});args={k:v for k,v in args.items() if k in ('network','scenario','users','model','weather')}
                with LOCK:result=simulate(**args)
                payload=dict(result=result,input=args,id=secrets.token_hex(12),timestamp=now*1000)
                with connect() as db:db.execute('INSERT INTO runs VALUES (?,?,?)',(payload['id'],now,json.dumps(payload)))
                return self.send_json(201,payload)
            if self.path=='/api/plans':
                if not self.admin():return
                if data['status'] not in ('AI 자동 제안','관리자 검토 중','보완 필요','승인','반려'):raise ValueError('계획 상태 오류')
                with connect() as db:db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)',(str(data['id'])[:160],data['status'],now))
                return self.send_json(200,dict(saved=True))
            return self.send_json(404,dict(error='Unknown endpoint'))
        except (ValueError,KeyError,TypeError) as e:self.send_json(400,dict(error=str(e)))
        except Exception:self.send_json(500,dict(error='서버 계산 실패. 입력 자료를 확인하세요.'))

def main():
    if not TOKEN:print('Admin endpoints disabled: set WATERAI_ADMIN_TOKEN (minimum 24 chars recommended).')
    host=os.environ.get('HOST','127.0.0.1');port=int(os.environ.get('PORT','8000'))
    print(f'AquaPlan: http://{host}:{port}',flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
if __name__=='__main__':main()
