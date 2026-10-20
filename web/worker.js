/* Execute the SAME Python source as the CPython service; no JS imitation. */
import {loadPyodide} from './pyodide/pyodide.mjs';
let ready;
async function initialize(){
 const py=await loadPyodide({indexURL:new URL('pyodide/',self.location.href).href});
 await py.loadPackage(['numpy','scipy']);
 const source=await fetch('core.py').then(r=>{if(!r.ok)throw Error('Python source unavailable');return r.text()});
 py.runPython(source);return py;
}
onmessage=async({data})=>{
 try{
  ready ||= initialize();const py=await ready;
  if(data.action==='init') {postMessage({id:data.id,ok:true,result:true});return;}
  py.globals.set('_input_json',JSON.stringify(data.payload||{}));
  const code=data.action==='demand'?'json.dumps(demand(json.loads(_input_json)))':
    data.action==='network'?'json.dumps(demo_network())':
    data.action==='validate'?'json.dumps(validate_network(json.loads(_input_json)))':
    data.action==='compare'?`json.dumps([simulate(**dict(json.loads(_input_json), scenario=s, make_plans=False)) for s in SCENARIOS])`:
    'json.dumps(simulate(**json.loads(_input_json)))';
  const result=JSON.parse(await py.runPythonAsync(code));postMessage({id:data.id,ok:true,result});
 }catch(e){postMessage({id:data.id,ok:false,error:String(e)});}
};
