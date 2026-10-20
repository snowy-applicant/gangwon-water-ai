import unittest,json,math
from copy import deepcopy
from pathlib import Path
from waterai.core import *
ROOT=Path(__file__).resolve().parents[1]
class EngineTests(unittest.TestCase):
    def test_demand(self):
        self.assertEqual(demand(dict(category='농업',subtypes=['벼','채소'],area=1000))['value'],5)
        self.assertAlmostEqual(demand(dict(category='축산업',subtypes=['소'],area=1000,heads=10))['value'],.7)
        with self.assertRaises(ValueError):demand(dict(area=-1))
        with self.assertRaises(ValueError):demand(dict(area=float('nan')))
    def test_invalid_network(self):
        n=demo_network();n['edges'][0]['to_node']='missing'
        with self.assertRaises(ValueError):validate_network(n)
        n=demo_network();n['edges'][0]['loss_fraction']=1
        with self.assertRaises(ValueError):validate_network(n)
    def test_global_conservation_and_storage(self):
        n=demo_network();r=simulate(n,scenario='compound',hours=48,make_plans=False)
        self.assertLess(r['mass_balance_residual'],1e-6)
        self.assertTrue(all(s>=-1e-7 for s in r['final_storage'].values()))
        self.assertLessEqual(r['supply_m3'],r['demand_m3']+1e-5)
        initial=sum(x['reservoir_storage_m3'] for x in n['nodes'] if x['node_type']=='source')
        inflow=sum(x['inflow_m3_day'] for x in n['nodes'] if x['node_type']=='source')*.15*2
        self.assertLessEqual(r['supply_m3'],initial+inflow+1e-5)
        for e in r['edges']:
            self.assertLessEqual(e['volume_m3'],hydraulic_capacity(e,{x['id']:x for x in n['nodes']})*.55*2+1e-5)
        self.assertTrue(any(x['first_shortage_hour'] is not None for x in r['regions']))
    def test_shared_source_essential_priority(self):
        n=demo_network();n['nodes']=n['nodes'][:2]+[n['nodes'][-3]]
        s=n['nodes'][-1];s.update(reservoir_storage_m3=1e4,inflow_m3_day=0,max_supply_m3_day=100)
        n['edges']=n['edges'][:2]
        for d in n['nodes'][:2]:d.update(demand_m3_day=100,essential_demand_m3_day=50)
        step=allocate(n,{s['id']:1e4},'normal')
        self.assertAlmostEqual(sum(step['essential_delivered'].values()),100/24*.96,places=5)
        self.assertAlmostEqual(sum(step['delivered'].values()),sum(step['essential_delivered'].values()),places=5)
    def test_risk_independent_from_users(self):
        m=json.loads((ROOT/'models/risk.json').read_text(encoding='utf-8'))
        a=simulate(model=m,hours=1,make_plans=False)
        b=simulate(model=m,users=[dict(region_id='51110',category='농업',subtypes=['벼'],area=1e5)],hours=1,make_plans=False)
        self.assertEqual({r['id']:r['risk'] for r in a['regions']},{r['id']:r['risk'] for r in b['regions']})
        self.assertNotEqual(a['input_hash'],b['input_hash'])
    def test_reproducibility(self):
        a=simulate(hours=2,make_plans=False);b=simulate(hours=2,make_plans=False)
        self.assertEqual(a['input_hash'],b['input_hash']);self.assertEqual(a['supply_m3'],b['supply_m3'])
    def test_terrain_avoids_blocked(self):
        blocked={(10,y) for y in range(15)};path,c=terrain_path((2,2),(20,18),blocked)
        self.assertTrue(path);self.assertTrue(all(p not in blocked for p in path))
    def test_pinn_artifact(self):
        p=json.loads((ROOT/'models/pinn.json').read_text(encoding='utf-8'))
        self.assertLess(p['rmse'],.02);self.assertFalse(p['validated_on_observations'])
    def test_browser_python_is_same(self):
        self.assertEqual((ROOT/'web/core.py').read_bytes(),(ROOT/'waterai/core.py').read_bytes())
    def test_full_plan_export(self):
        result=simulate(scenario='normal',hours=48)
        self.assertTrue(result['plans'])
        self.assertTrue(json.dumps(result))
        for plan in result['plans']:
            self.assertGreater(plan['improvement_m3'],0)
            self.assertGreater(len(plan['geometry']),1)
if __name__=='__main__':unittest.main()
