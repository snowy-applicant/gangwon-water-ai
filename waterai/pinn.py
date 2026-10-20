"""Steady pressure-pipe PINN research benchmark, dimensionless head loss.
Autograd residual: dh/dx + q**1.852 = 0, h(0,q)=0.
Not applicable to open channels/reservoirs. Analytic solver remains primary.
"""
import json,time
from pathlib import Path
def train(epochs=1800):
    import torch
    torch.manual_seed(42);torch.set_num_threads(2)
    net=torch.nn.Sequential(torch.nn.Linear(2,24),torch.nn.Tanh(),torch.nn.Linear(24,24),torch.nn.Tanh(),torch.nn.Linear(24,1))
    optimizer=torch.optim.Adam(net.parameters(),lr=.003);start=time.perf_counter()
    for _ in range(epochs):
        x=torch.rand(256,1,requires_grad=True);q=torch.rand(256,1)*.95+.05
        h=net(torch.cat([x,q],1));dx=torch.autograd.grad(h.sum(),x,create_graph=True)[0]
        boundary=net(torch.cat([torch.zeros_like(x),q],1))
        loss=((dx+q**1.852)**2).mean()+5*(boundary**2).mean()
        optimizer.zero_grad();loss.backward();optimizer.step()
    x=torch.linspace(0,1,51).reshape(-1,1).repeat(10,1)
    q=torch.linspace(.05,1,10).repeat_interleave(51).reshape(-1,1)
    with torch.no_grad():error=net(torch.cat([x,q],1))+x*q**1.852
    rmse=float((error.square().mean()).sqrt());mae=float(error.abs().mean())
    layers=[dict(weight=l.weight.detach().tolist(),bias=l.bias.detach().tolist()) for l in net if isinstance(l,torch.nn.Linear)]
    payload=dict(version='steady-head-pinn-v1',layers=layers,rmse=rmse,mae=mae,
      training_seconds=time.perf_counter()-start,physics_loss=float(loss.detach()),
      benchmark='synthetic dimensionless Hazen-Williams analytic reference',
      primary_solver='analytic',reason='해석식이 정확하고 저렴하므로 PINN을 운영 계산에 우선 사용하지 않음',
      domain=dict(x=[0,1],q=[.05,1]),validated_on_observations=False)
    path=Path(__file__).resolve().parents[1]/'models/pinn.json';path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in payload.items() if k!='layers'},ensure_ascii=False,indent=2))
if __name__=='__main__':train()
