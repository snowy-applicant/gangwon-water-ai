'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const fmt=(n,d=0)=>Number(n).toLocaleString('ko-KR',{maximumFractionDigits:d});
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const read=(k,def)=>{try{return JSON.parse(localStorage.getItem('aq-'+k))??def}catch{return def}};
const save=(k,v)=>localStorage.setItem('aq-'+k,JSON.stringify(v));
let worker,seq=0,pending=new Map(),model,metrics,pinn,network,weather,result,busy=false,server=false,adminToken='';
let users=read('users',[]),reports=read('reports',[]),history=read('history',[]),planStates=read('planStates',{});
const titles={overview:['공급망 대시보드','강원도의 물 수요와 공급을 한눈에, 다음 의사결정을 더 명확하게.'],user:['내 사업장 분석','사업장의 수요가 전체 공급 계획에 미치는 영향을 확인하세요.'],scenarios:['기후 시나리오 비교','같은 공급망, 서로 다른 기후. 부족한 곳을 미리 확인합니다.'],plans:['수로 설치 및 보강','공급망 재계산으로 개선 효과를 검토하는 장기 확충 후보.'],reports:['긴급 신고 · 대응','재해 위험과 대응 우선순위를 분리해 관리합니다.'],models:['AI 모델 · 데이터','출처와 검증 수준을 확인하고 계산을 재현합니다.'],history:['시뮬레이션 이력','데이터와 모델이 바뀐 이유를 추적할 수 있습니다.']};
function tab(name){$$('.view').forEach(v=>v.classList.toggle('active',v.id===name));$$('nav button').forEach(v=>v.classList.toggle('active',v.dataset.tab===name));$('#page-title').textContent=titles[name][0];$('#page-desc').textContent=titles[name][1];if(name==='history')renderHistory();if(name==='reports')renderReports();}
$$('nav button').forEach(b=>b.onclick=()=>tab(b.dataset.tab));$('#data-detail').onclick=()=>tab('models');$('#go-plans').onclick=()=>tab('plans');
function status(s,error=false){$('#status').textContent=s;$('#status').classList.toggle('error',error);}
function call(action,payload){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});worker.postMessage({id,action,payload});});}
function setBusy(value){busy=value;$$('.run').forEach(b=>b.disabled=value);$('#export').disabled=value||!result;}
const scenarios={normal:'정상',dry:'장기 강수 감소',prolonged:'30일 이상 강수 부족',heat:'극심한 고온',inflow:'저수지 유입 급감',outage:'공급 시설 제한',compound:'동시 극한 가뭄'};
function weatherInput(){
 const by={};const values=Object.values(weather);network.nodes.filter(n=>n.node_type==='demand').forEach(n=>{
   const found=weather[n.id]||values[0];by[n.id]=found;
 });return by;
}
function payload(){return {network,scenario:$('#scenario').value,users,model,weather:weatherInput()};}
async function run(){
 if(busy)return;setBusy(true);status('Python · 시간별 전체 공급망 최적화와 신규 수로 후보 비교 중…');
 try{
  result=await call('simulate',payload());
  const item={...result,users:JSON.parse(JSON.stringify(users)),network,weather:weatherInput()};
  history.unshift(item);history=history.slice(0,10);save('history',history);
  if(server&&adminToken)await api('/api/runs','POST',{input:payload()});
  render();status(`계산 완료 · ${new Date().toLocaleTimeString('ko-KR')} · 질량 보존 잔차 ${result.mass_balance_residual.toExponential(1)} m³ · 가상 공급망 / 과거 기상 입력`);
 }catch(e){status('계산 실패: '+e.message,true)}finally{setBusy(false)}
}
$('#run').onclick=run;
function render(){
 const r=result;const essential=r.regions.reduce((a,x)=>a+x.essential,0),del=r.regions.reduce((a,x)=>a+x.essential_supply,0);
 const short=r.regions.filter(x=>x.coverage<.999);
 $('#kpis').innerHTML=[['48시간 예상 물 수요',fmt(r.demand_m3),'m³','등록 사업장 + 가상 지역 기본 수요'],['48시간 공급 가능량',fmt(r.supply_m3),'m³','저수량·유입량·관로 용량 반영'],['예상 물 부족량',fmt(r.shortage_m3),'m³',`필수 수요 미충족 ${short.length}개 지역`],['필수 수요 충족률',fmt(del/essential*100,1),'%',`물량 기준 · 시간별 부족 별도 확인`]].map((k,i)=>`<div class="kpi ${i===2?'warning':''}"><div class="label">${k[0]}<span>↗</span></div><strong>${k[1]}<small>${k[2]}</small></strong><p>${k[3]}</p></div>`).join('');
 $('#priorities').innerHTML=r.regions.slice(0,5).map((x,i)=>`<div class="priority-row" data-detail="${esc(x.id)}" tabindex="0" role="button"><span class="rank">0${i+1}</span><strong>${esc(x.name)}<small>부족 ${fmt(x.shortage)} m³ · 위험 ${fmt((x.risk?.['30']||0)*100)}%</small></strong><span class="badge">${fmt(x.coverage*100)}% 충족</span></div>`).join('');
 $$('[data-detail]').forEach(b=>{b.onclick=()=>regionDetail(b.dataset.detail);b.onkeydown=e=>{if(e.key==='Enter')regionDetail(b.dataset.detail)}});
 $('#insight').textContent=short.length?`${short.length}개 지역에서 필수 수요 부족이 예상됩니다. 기존 연결망을 최적화한 뒤 개선 효과가 있는 수로 후보 ${r.plans.length}건을 선별했습니다.`:'설정한 가정 아래에서 48시간 필수 수요 물량을 충족합니다. 필요한 시점의 공급과 실제 시설 운영은 별도 검증이 필요합니다.';
 drawMap();drawChart();renderPlans();renderUsers();$('#provenance').innerHTML=`<p class="hint">공급망 출처: ${esc(r.data_provenance)} / 버전: ${esc(network.version||'미확인')} / 기준 시각: ${esc(network.timestamp||'없음')}<br>기상 기준: ${esc(Object.values(weather)[0]?.date)} · 과거 자료입니다. 현재 예보가 아닙니다.<br>실행 입력 해시: <code>${r.input_hash}</code><br>${r.assumptions.map(esc).join(' · ')}</p>`;
}
function regionDetail(id){const r=result.regions.find(x=>x.id===id);if(!r)return;
 const edges=result.edges.filter(e=>e.to_node===id&&e.volume_m3>.001);
 $('#region-detail').innerHTML=`<strong>${esc(r.name)} · 계산 근거</strong><div class="statline"><span>48시간 수요 / 공급</span><b>${fmt(r.demand)} / ${fmt(r.supply)} m³</b></div><div class="statline"><span>필수 수요 충족률</span><b>${fmt(r.coverage*100,1)}%</b></div><div class="statline"><span>최초 필수 수요 부족</span><b>${r.first_shortage_hour===null?'없음':r.first_shortage_hour+'시간 후'}</b></div><p>선택 경로: ${edges.map(e=>`${esc(e.from_node)} → ${esc(e.to_node)} (${fmt(e.volume_m3)} m³, 손실 전)`).join(', ')||'공급 가능한 경로 없음'}<br>필수 수요 → 총 부족량 → 비용 순으로 전역 최적화했습니다.</p>`;
}
function drawMap(){
 const W=700,H=430,xy=n=>[90+(n.longitude-127.1)/2.35*500,390-(n.latitude-37.0)/1.6*350];
 const by=Object.fromEntries(result.nodes.map(n=>[n.id,n]));
 let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="강원 시군 중심점과 가상 공급망 개략 지도"><defs><pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#dbe6d9" stroke-width=".6"/></pattern><linearGradient id="land"><stop stop-color="#d7e4ce"/><stop offset="1" stop-color="#e6edde"/></linearGradient></defs><rect width="700" height="430" fill="#e8f0eb"/><path d="M220 15L395 25L442 73L490 120L530 190L585 270L574 355L490 410L392 386L330 419L275 367L195 360L165 302L114 268L130 195L95 140L153 110L170 55Z" fill="url(#land)" stroke="#bfd0ba"/><path d="M310 30Q460 180 390 390M280 80Q210 220 300 350M390 70Q520 240 460 365" stroke="#c9d8bf" stroke-width="18" fill="none" opacity=".35"/><rect width="700" height="430" fill="url(#grid)"/><text x="583" y="130" fill="#9fb8ae" font-size="16" letter-spacing="5">동해</text><text x="25" y="30" fill="#8ca18d" font-size="9">N ↑ / 개략 배치 · 법정 경계 아님</text><text x="25" y="410" fill="#8ca18d" font-size="9">가상 공급원 및 가상 관로 · 시군 좌표 기반</text>`;
 result.edges.forEach(e=>{const a=xy(by[e.from_node]),b=xy(by[e.to_node]);s+=`<path d="M${a} Q${(a[0]+b[0])/2+15},${(a[1]+b[1])/2-12} ${b}" fill="none" stroke="${e.volume_m3>.001?'#548e7e':'#bccbbf'}" stroke-width="${e.volume_m3>.001?1.6:1}" opacity=".65" ${e.volume_m3>.001?'':'stroke-dasharray="3 4"'}/>`});
 result.plans.forEach(p=>{s+=`<polyline points="${p.geometry.map(xy).join(' ')}" fill="none" stroke="#d49a62" stroke-width="1.5" stroke-dasharray="5 4" opacity=".8"/>`});
 result.nodes.forEach(n=>{const [x,y]=xy(n),r=result.regions.find(r=>r.id===n.id),isSource=n.node_type==='source';s+=`<g ${!isSource?`data-region="${esc(n.id)}" tabindex="0" role="button" aria-label="${esc(n.name)} 상세"`:''}><circle cx="${x}" cy="${y}" r="${isSource?8:5.5}" fill="${isSource?'#1e796b':r.coverage<.999?'#d79a65':'#91b8a1'}" stroke="white" stroke-width="2"/><text x="${x+10}" y="${y+3}" fill="#446654" font-size="9">${esc(n.name)}</text></g>`});
 $('#map').innerHTML=s+'</svg>';$$('[data-region]').forEach(g=>{g.onclick=()=>regionDetail(g.dataset.region);g.onkeydown=e=>{if(e.key==='Enter')regionDetail(g.dataset.region)}});
}
function drawChart(){const a=result.timeline,max=Math.max(...a.map(x=>x.storage_m3),1),pts=a.map((v,i)=>`${35+i/(a.length-1)*560},${140-v.storage_m3/max*110}`),path=pts.join(' ');
 $('#chart').innerHTML=`<svg viewBox="0 0 630 175" role="img" aria-label="48시간 저장량 추이"><defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#cde0ca" stop-opacity=".8"/><stop offset="1" stop-color="#f8faf5"/></linearGradient></defs>${[30,65,100,140].map(y=>`<path d="M35 ${y}H600" stroke="#edf1e8"/>`).join('')}<polygon points="35,140 ${path} 595,140" fill="url(#fade)"/><polyline points="${path}" stroke="#729d7c" fill="none" stroke-width="2"/><text x="35" y="163" fill="#9baa95" font-size="10">1시간</text><text x="560" y="163" fill="#9baa95" font-size="10">48시간</text><text x="40" y="20" fill="#82987b" font-size="10">${fmt(max)} m³</text></svg>`;
}
function renderPlans(){
 $('#plans-list').innerHTML=result.plans.length?result.plans.map((p,i)=>`<div class="plan-card"><span class="tag">PROPOSAL 0${i+1}</span><h3>${esc(p.region)} · ${esc(p.source)} → ${esc(p.target)}</h3><p>${esc(p.explanation)}</p><div class="mini-kpis"><div><strong>${fmt(p.improvement_m3)}</strong><span>전체 부족 감소 m³ / 48h</span></div><div><strong>${fmt(p.length_m/1000,1)} km</strong><span>가상 GIS 경로 · ${p.diameter_mm} mm</span></div><div><strong>${fmt(p.cost_krw/1e8,1)} 억원</strong><span>시험용 공사 원단위 추정</span></div></div><p>예상 용량 ${fmt(p.capacity_m3_day)} m³/일 · 대상 지역 개선 ${fmt(p.target_improvement_m3)} m³ · 보강 우선순위 ${fmt(p.priority,2)}</p><label>내부 검토 상태 · 실제 시공 승인 아님<select data-plan="${i}">${['AI 자동 제안','관리자 검토 중','보완 필요','승인','반려'].map(s=>`<option ${s===(planStates[result.input_hash+'-'+i]||p.status)?'selected':''}>${s}</option>`).join('')}</select></label><details><summary>다른 공급원 후보와 비교</summary><table><thead><tr><th>공급원</th><th>전체 부족 감소</th><th>추정 비용</th></tr></thead><tbody>${p.alternatives.map(a=>`<tr><td>${esc(a.source)}</td><td>${fmt(a.improvement_m3)} m³</td><td>${fmt(a.cost_krw/1e8,1)} 억원</td></tr>`).join('')}</tbody></table></details></div>`).join(''):'<div class="empty">현 조건에서 개선 효과가 검증된 후보가 없습니다.<br>필수 수요가 충족되었거나 공급원·관로 제약으로 추가 수로의 효과가 부족합니다.</div>';
 $$('[data-plan]').forEach(s=>s.onchange=async()=>{const key=result.input_hash+'-'+s.dataset.plan;try{if(server){if(!adminToken)throw Error('서버 계획 저장에는 관리자 인증이 필요합니다.');await api('/api/plans','POST',{id:key,status:s.value});}planStates[key]=s.value;save('planStates',planStates);status(server?'서버에 내부 검토 상태를 저장했습니다.':'이 기기의 데모 검토 상태를 저장했습니다.');}catch(e){status(e.message,true);s.value=planStates[key]||'AI 자동 제안'}});
}
const rates={'농업':['벼','채소','과일'],'축산업':['소','돼지'],'공업':['식품','제조'],'일반':['주거','상업','의료']};
function subtypes(){$('#subtypes').innerHTML=rates[$('#category').value].map((s,i)=>`<label><input type="checkbox" value="${s}" ${i===0?'checked':''}>${s}</label>`).join('')};$('#category').onchange=subtypes;subtypes();
function fillRegions(query=''){const nodes=network.nodes.filter(n=>n.node_type==='demand'&&n.name.includes(query));$('#region').innerHTML=nodes.map(n=>`<option value="${esc(n.id)}">강원특별자치도 ${esc(n.name)} (중심점)</option>`).join('');}
$('#address-search').oninput=e=>fillRegions(e.target.value.trim());
$('#user-form').onsubmit=async e=>{e.preventDefault();if(busy)return;try{const u={id:crypto.randomUUID(),category:$('#category').value,subtypes:$$('#subtypes input:checked').map(x=>x.value),region_id:$('#region').value,area:Number($('#area').value)};
 if($('#heads').value)u.heads=Number($('#heads').value);if($('#actual').value)u.actual_usage=Number($('#actual').value);
 const estimate=await call('demand',u);users.push({...u,estimate});save('users',users);await run();
 $('#user-result').innerHTML=`<div class="result-card"><h3>예상 수요 ${fmt(estimate.value,2)} m³/일</h3><p>추정 범위 ${fmt(estimate.lower,2)} ~ ${fmt(estimate.upper,2)} m³/일<br>${esc(estimate.basis)}<br>${esc(estimate.warning)}</p></div>`;
 const r=result.regions.find(r=>r.id===u.region_id);$('#user-result').innerHTML+=`<div class="risk-bars">${['7','30','90'].map(h=>`<div>${h}일 강수 부족 지표<strong>${fmt(r.risk[h]*100)}%</strong>공식 가뭄 확률 아님</div>`).join('')}</div><p class="muted">${esc(r.name)} 48시간 필수 수요 충족률 ${fmt(r.coverage*100,1)}%. 이 값은 지역 전체 결과이며 개별 사업장 공급 보장량이 아닙니다.</p>`;
 }catch(err){status(err.message,true)}};
function renderUsers(){$('#user-list').innerHTML=users.map(u=>`<div class="result-card"><strong>${esc(u.category)} · ${esc(u.subtypes.join('/'))}</strong><p>${fmt(u.area)} m² · ${esc(network.nodes.find(n=>n.id===u.region_id)?.name||u.region_id)} · ${fmt(u.estimate?.value||0,2)} m³/일</p><button class="button secondary" data-remove="${esc(u.id)}">사업장 제외</button></div>`).join('');$$('[data-remove]').forEach(b=>b.onclick=async()=>{users=users.filter(u=>u.id!==b.dataset.remove);save('users',users);await run()})}
$('#compare').onclick=async()=>{if(busy)return;setBusy(true);status('Python · 7가지 시나리오 비교 중…');try{const rows=await call('compare',payload());$('#comparison').innerHTML=`<table><thead><tr><th>시나리오</th><th>48h 수요 m³</th><th>공급 m³</th><th>부족 m³</th><th>필수 수요 미충족</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${scenarios[r.scenario]}</td><td>${fmt(r.demand_m3)}</td><td>${fmt(r.supply_m3)}</td><td>${fmt(r.shortage_m3)}</td><td>${r.regions.filter(x=>x.coverage<.999).length}개 지역</td></tr>`).join('')}</tbody></table>`;status('시나리오 비교 완료 · 강수 부족 AI 확률은 기상 입력을 고정하여 비교하고, 공급 시나리오만 변경했습니다.')}catch(e){status(e.message,true)}finally{setBusy(false)}};
function download(name,obj){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(obj,null,2)],{type:'application/json'}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
$('#export').onclick=()=>download('aquaplan-result.json',{...result,users,network,weather:weatherInput(),model});$('#sample').onclick=()=>download('network.json',network);
$('#network-file').onchange=async e=>{if(!e.target.files[0])return;try{const file=e.target.files[0];if(file.size>2e6)throw Error('2 MB 이하 파일을 사용하세요.');const n=JSON.parse(await file.text());network=await call('validate',n);fillRegions();users=[];save('users',users);await run()}catch(e){status('공급망 불러오기 실패: '+e.message,true)}};
$('#reset-network').onclick=async()=>{network=await call('network');fillRegions();await run()};
function renderModels(){
 $('#model-info').innerHTML=`<p class="muted">${esc(model.label)}<br>학습 자료: ${model.provenance.map(esc).join(', ')} · 학습 종료 ${model.training_end} · 검증 2023년 · 시험 ${model.test_period} · 경계 90일 제거</p><div class="table-wrap"><table><thead><tr><th>기간</th><th>Brier ↓</th><th>기준선 Brier ↓</th><th>정밀도</th><th>재현율</th><th>F1</th><th>시험 표본</th></tr></thead><tbody>${Object.entries(metrics.risk).map(([h,m])=>`<tr><td>${h}일</td><td>${fmt(m.brier,3)}</td><td>${fmt(m.baseline_brier,3)}</td><td>${fmt(m.precision,3)}</td><td>${fmt(m.recall,3)}</td><td>${fmt(m.f1,3)}</td><td>${m.test_samples}</td></tr>`).join('')}</tbody></table></div><p class="hint">Brier는 낮을수록 좋습니다. 30일 모델은 시험 자료에서 기준선보다 나빠 의사결정용으로 사용하기 어렵습니다. 농업·수문학적 가뭄 모델과 실제 공급 부족 확률 모델은 아직 검증되지 않았습니다.</p><h3>기존 단일 압력 관로 PINN · 비교용 벤치마크</h3><p class="muted">정상 압력 관로의 무차원 수두 손실을 PyTorch autograd 물리 잔차로 학습했습니다. 해석식 대비 RMSE ${fmt(pinn.rmse,5)}, MAE ${fmt(pinn.mae,5)}. 가상 물리 데이터 검증이며 실측 검증은 없습니다.<br>실제 배분 계산은 정확한 Hazen–Williams 해석식과 개방형 수로 Manning 식을 우선 사용합니다. 고정 수두 모델로 관망 전체의 비선형 압력 해석을 대신할 수 없습니다.</p><h3>데이터 출처 및 범위</h3><p class="muted">NASA POWER 2015–2025 춘천·강릉·태백 기상 격자 추정 자료를 학습에 사용합니다. 나머지 지역 화면에는 춘천 과거 입력을 대체 적용하므로 해당 지역 예보로 해석하면 안 됩니다. 공급 시설·관로·지형·공사비는 가상 데이터입니다.<br><a href="https://power.larc.nasa.gov/docs/services/api/temporal/daily/" target="_blank" rel="noopener">NASA POWER Daily API</a> · <a href="https://data.kma.go.kr/" target="_blank" rel="noopener">기상청 자료 포털 (미연결)</a> · <a href="https://www.drought.go.kr/" target="_blank" rel="noopener">국가가뭄정보포털 (미연결)</a></p>`;
}
function renderHistory(){$('#history-list').innerHTML=history.length?history.map((r,i)=>`<div class="result-card"><h3>${esc(scenarios[r.scenario])} · ${new Date(r.created_at).toLocaleString('ko-KR')}</h3><p>수요 ${fmt(r.demand_m3)} / 공급 ${fmt(r.supply_m3)} / 부족 ${fmt(r.shortage_m3)} m³${history[i+1]?` · 이전 실행 대비 부족 ${fmt(r.shortage_m3-history[i+1].shortage_m3,1)} m³ 변화`:''}<br>모델 ${esc(r.model_version)} · <code>${r.input_hash.slice(0,24)}…</code></p><button class="button secondary" data-history="${i}">재현 입력 포함 JSON 내려받기</button></div>`).join(''):'<p class="empty">실행 이력이 없습니다.</p>';$$('[data-history]').forEach(b=>b.onclick=()=>download('aquaplan-run.json',history[Number(b.dataset.history)]))}
let reporter=read('reporter',null);if(!reporter){reporter=crypto.randomUUID();save('reporter',reporter)}
$('#report-form').onsubmit=async e=>{e.preventDefault();const region_id=$('#report-region').value,severity=Number($('#severity').value),text=$('#report-text').value.trim();if(!text)return;
 try{if(server){await api('/api/reports','POST',{region_id,severity,text,reporter});status('서버에 긴급 신고를 접수했습니다. 관리자 화면에서 확인할 수 있습니다.');if(adminToken)reports=await api('/api/reports');}
 else{if(reports.some(r=>r.region_id===region_id&&r.reporter===reporter&&Date.now()-r.timestamp<86400000))throw Error('같은 지역 신고가 24시간 이내 이미 접수되었습니다. 가중치는 중복되지 않습니다.');reports.unshift({region_id,severity,text,reporter,timestamp:Date.now(),score:Math.min(100,30+severity*20)});save('reports',reports);status('이 기기 데모에 저장했습니다. 실제 관리자에게 전송되지 않았습니다.');}renderReports();$('#report-text').value='';}catch(e){status(e.message,true)}};
function renderReports(){$('#report-list').innerHTML=reports.length?[...reports].sort((a,b)=>b.score-a.score).map(r=>{const row=result?.regions.find(x=>x.id===r.region_id);return `<div class="report-row"><strong>${esc(network?.nodes.find(n=>n.id===r.region_id)?.name||r.region_id)} · 대응 점수 ${r.score}</strong><small> / ${new Date(r.timestamp).toLocaleString('ko-KR')}</small><p>${esc(r.text)}</p><span class="hint">재해 지표 ${fmt((row?.risk?.['30']||0)*100)}% · 예상 부족 ${fmt(row?.shortage||0)} m³ · 신고로 확률 변경 없음</span></div>`}).join(''):'<p class="empty">접수된 신고가 없습니다.</p>'}
async function api(path,method='GET',body){const r=await fetch(path,{method,headers:{'Content-Type':'application/json',...(adminToken?{'Authorization':'Bearer '+adminToken}:{})},...(body?{body:JSON.stringify(body)}:{})});const data=await r.json();if(!r.ok)throw Error(data.error||'서버 요청 실패');return data}
$('#admin-connect').onclick=async()=>{adminToken=$('#admin-token').value;try{reports=await api('/api/reports');$('#server-state').textContent='관리자 인증 완료. 서버 신고를 불러왔습니다.';renderReports()}catch(e){adminToken='';$('#server-state').textContent=e.message}};
async function init(){setBusy(true);try{
  [model,metrics,pinn,weather]=await Promise.all(['risk.json','metrics.json','pinn.json','weather_snapshot.json'].map(p=>fetch(p).then(r=>{if(!r.ok)throw Error(p+' 불러오기 실패');return r.json()})));
  worker=new Worker('worker.js',{type:'module'});worker.onmessage=({data})=>{const p=pending.get(data.id);if(!p)return;pending.delete(data.id);data.ok?p.resolve(data.result):p.reject(Error(data.error));};worker.onerror=e=>{for(const p of pending.values())p.reject(Error(e.message||'Python worker failure'));pending.clear();status('Python 실행 환경을 불러오지 못했습니다. 네트워크를 확인한 뒤 새로고침하세요.',true)};
  await call('init');network=await call('network');fillRegions();$('#report-region').innerHTML=$('#region').innerHTML;renderModels();renderReports();
  $('#runtime').textContent='Python · SciPy 실행 중';
  try{const health=await api('/api/health');server=health.mode==='server'}catch{}
  $('#server-state').textContent=server?'Python 서버 모드 · 관리자 토큰으로 접근하세요.':'정적 사이트 · 브라우저 내 Python 실행 / 신고와 이력은 이 기기만 저장';
  if(server)$('#report-mode').textContent='Python 서버 모드: 신고가 서버에 저장되어 인증된 관리자가 확인할 수 있습니다.';
  setBusy(false);await run();
 }catch(e){status('초기화 실패: '+e.message+' · 인터넷 연결 후 새로고침하세요.',true);$('#runtime').textContent='엔진 오류';}}
init();
