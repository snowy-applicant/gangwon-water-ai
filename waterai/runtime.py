"""Download a pinned official Pyodide runtime for self-hosted browser execution.
Verify package hashes against the official lock. No credentials required.
"""
import hashlib,json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
BASE='https://cdn.jsdelivr.net/pyodide/v314.0.7/full/'
ROOT=Path(__file__).resolve().parents[1]/'web/pyodide'
def main():
    ROOT.mkdir(exist_ok=True)
    session=requests.Session()
    def get(name,expected=None):
        target=ROOT/name
        if target.exists() and (not expected or hashlib.sha256(target.read_bytes()).hexdigest()==expected):return
        r=session.get(BASE+name,timeout=180);r.raise_for_status()
        if expected and hashlib.sha256(r.content).hexdigest()!=expected:raise ValueError('Hash mismatch: '+name)
        target.write_bytes(r.content);print(name,len(r.content),flush=True)
    get('pyodide-lock.json');lock=json.loads((ROOT/'pyodide-lock.json').read_text())
    tasks=[('pyodide.js',None),('pyodide.mjs',None),('pyodide.asm.mjs',None),('pyodide.asm.wasm',None),('python_stdlib.zip',None)]
    for name in ('numpy','scipy'):
        item=lock['packages'][name];tasks.append((item['file_name'],item['sha256']))
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda t:get(*t),tasks))
    print('Pinned browser Python runtime ready.')
if __name__=='__main__':main()
