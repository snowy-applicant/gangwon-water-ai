#!/usr/bin/env python3
"""Send observed JSONL or Uno status to the MAIN gangwon-water-ai server.

JSONL is an adapter contract, NOT a PASCO USB/Bluetooth driver. Uno's ASCII
status is not a force/flow reading. Control requires --allow-control and typed
commands; no model/network result can actuate a servo through this program.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from datetime import datetime,timezone
from urllib.error import HTTPError,URLError
from urllib.parse import urlparse
from urllib.request import Request,urlopen
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from waterai.hardware import NETWORK_ID,validate_sample

def make_sample(line, device_id):
    """Normalize v4/v5 Uno status OR an explicit measurement JSON record."""
    if line.startswith('ST '):
        match=re.fullmatch(r'ST (\d+) L=([01]{4}) B=([01]) A=([01]) HB=([01])(?: C=(\d+))?',line.strip())
        if not match:raise ValueError('Uno 상태 형식 오류')
        ms,active,latched,armed,hb,calibrated=match.groups()
        data=dict(kind='controller_status',channel_id='controller',values=dict(
            uptime_ms=int(ms),active=[v=='1' for v in active],latched=latched=='1',
            armed=armed=='1',heartbeat=hb=='1',calibrated=int(calibrated or 0)))
    else:
        data=json.loads(line)
        if not isinstance(data,dict):raise ValueError('측정 JSON 객체가 필요합니다.')
    data.setdefault('sample_id',uuid4().hex)
    data.setdefault('device_id',device_id)
    data.setdefault('network_id',NETWORK_ID)
    data.setdefault('domain','rig')
    data.setdefault('observed_at',datetime.now(timezone.utc).isoformat())
    return validate_sample(data)

def post_sample(url, token, sample):
    request=Request(url.rstrip('/')+'/api/hardware',data=json.dumps(sample,allow_nan=False).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+token},method='POST')
    try:
        with urlopen(request,timeout=5) as response:return json.load(response)
    except HTTPError as exc:raise RuntimeError(f'수신 서버 HTTP {exc.code}') from None
    except (URLError,TimeoutError):raise RuntimeError('수신 서버에 연결하지 못했습니다.') from None

def validate_command(text):
    """Exact firmware grammar, never evaluate shell text or auto-arm."""
    text=text.strip()
    if re.fullmatch(r'(PING|HB|STAT|STOP|RESET|CONFIG)',text):return text
    if re.fullmatch(r'ARM [0-9]{1,5}',text):return text
    if re.fullmatch(r'OPENM [0-9]{1,6} [0-9]{1,6} [0-9]{1,6} [0-9]{1,6}',text):return text
    if re.fullmatch(r'CLOSE (R1|R3A|R3B|R2)',text):return text
    if re.fullmatch(r'(POS|ANG) (R1|R3A|R3B|R2) [0-9]{1,3}( [0-9]{1,3})?',text):
        parts=text.split()
        if len(parts)!=(3 if parts[0]=='POS' else 4) or any(int(v)>180 for v in parts[2:]):raise ValueError('서보 각도 오류')
        return text
    if re.fullmatch(r'LIMIT [0-9]{1,6}|LOOP [01]',text):return text
    raise ValueError('지원하지 않는 장치 명령입니다.')

def serial_bridge(args,token):
    try:import serial
    except ImportError:raise RuntimeError('USB 연결에는 pip install pyserial이 필요합니다.') from None
    # Background upload cannot block firmware heartbeats or local STOP.
    uploads=queue.Queue(maxsize=64);commands=queue.Queue();done=threading.Event()
    def upload():
        while not done.is_set() or not uploads.empty():
            try:sample=uploads.get(timeout=.2)
            except queue.Empty:continue
            try:post_sample(args.url,token,sample)
            except RuntimeError as exc:print(str(exc)+' · 해당 표본 업로드 실패',file=sys.stderr,flush=True)
    threading.Thread(target=upload,daemon=True).start()
    def keyboard():
        try:
            for line in sys.stdin:
                if line.strip().upper()=='QUIT':break
                try:commands.put(validate_command(line))
                except ValueError as exc:print(exc,file=sys.stderr,flush=True)
        finally:commands.put(None)
    if args.allow_control:threading.Thread(target=keyboard,daemon=True).start()
    last_heartbeat=0.;last_upload=0.
    with serial.Serial(args.port,args.baud,timeout=.1,write_timeout=.5) as port:
        try:
            while True:
                now=time.monotonic()
                if args.allow_control:
                    if now-last_heartbeat>=1:
                        port.write(b'HB\n');last_heartbeat=now
                    try:
                        command=commands.get_nowait()
                        if command is None:break
                        port.write((command+'\n').encode('ascii'))
                    except queue.Empty:pass
                raw=port.readline()
                if not raw:continue
                line=raw.decode('ascii',errors='replace').strip()
                print(line,flush=True)
                if not line.startswith('ST ') or now-last_upload<1:continue
                try:
                    sample=make_sample(line,args.device_id)
                    uploads.put_nowait(sample);last_upload=now
                except (ValueError,queue.Full) as exc:print(f'상태 업로드 제외: {exc}',file=sys.stderr,flush=True)
        finally:
            if args.allow_control:
                # A best-effort software close; no claim of physical power-loss shutoff.
                try:port.write(b'STOP\n');port.flush()
                except Exception:pass
            done.set()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True,help='Python 서버 주소. 정적 Sites 주소는 수신 서버가 아닙니다.')
    parser.add_argument('--device-id',required=True)
    parser.add_argument('--port',help='Uno USB 시리얼 포트. 생략하면 측정 JSONL을 stdin에서 읽음')
    parser.add_argument('--baud',type=int,default=38400)
    parser.add_argument('--allow-control',action='store_true',help='직접 입력한 장치 명령과 1초 heartbeat 허용')
    args=parser.parse_args();parsed=urlparse(args.url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        parser.error('인증정보·경로·쿼리가 없는 서버 기본 주소를 사용하세요.')
    if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost')):
        parser.error('HTTPS 또는 로컬 서버 주소가 필요합니다.')
    token=os.environ.get('WATERAI_SENSOR_TOKEN','')
    if not token:parser.error('WATERAI_SENSOR_TOKEN 환경변수가 필요합니다.')
    if args.allow_control and not args.port:parser.error('--allow-control에는 --port가 필요합니다.')
    try:
        if args.port:serial_bridge(args,token)
        else:
            failures=0
            for line in sys.stdin:
                if not line.strip():continue
                try:
                    sample=make_sample(line,args.device_id);result=post_sample(args.url,token,sample)
                    print(json.dumps(dict(accepted=True,sequence=result['sequence'])),flush=True)
                except (ValueError,RuntimeError) as exc:
                    failures+=1;print(exc,file=sys.stderr,flush=True)
            return 1 if failures else 0
    except RuntimeError as exc:parser.exit(1,str(exc)+'\n')
    except KeyboardInterrupt:return 0
    return 0

if __name__=='__main__':raise SystemExit(main())
