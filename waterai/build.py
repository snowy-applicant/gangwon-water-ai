"""Synchronize portable Python/model assets; fail if trained models absent."""
from pathlib import Path
import shutil
def main():
    root=Path(__file__).resolve().parents[1]
    for src,dest in [('waterai/core.py','core.py'),('models/risk.json','risk.json'),('models/metrics.json','metrics.json'),('models/pinn.json','pinn.json'),('models/rig-pinn.json','rig-pinn.json'),('data/weather_snapshot.json','weather_snapshot.json')]:
        shutil.copyfile(root/src,root/'web'/dest)
    shutil.copytree(root/'web',root/'dist',dirs_exist_ok=True)
    print('web/ assets synchronized; dist/ prepared')
if __name__=='__main__':main()
