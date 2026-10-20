/* Admin-only monitor for measured rig data. No servo or model actuation here. */
titles.hardware=['하드웨어 · 실측 데이터','수조 2개 · 호스 4개 · 받는 용기 3개 / 실제 관망과 분리된 실험'];
let hardwareBusy=false;
async function refreshHardware(){
 if(hardwareBusy)return;
 if(!adminToken){$('#hardware-state').textContent='AI 모델 · 데이터 탭에서 Python 서버 관리자 토큰으로 연결하세요.';return;}
 hardwareBusy=true;
 try{
  const data=await api('/api/hardware');
  $('#hardware-state').textContent=`수신 서버 연결 · 저장 순번 ${data.latest_sequence} · 3초마다 확인`;
  const measured=data.samples.filter(s=>s.kind==='measurement');
  $('#hardware-samples').innerHTML=['F1','F3','F2'].map(channel=>{
   const region={F1:'지역 1 · H1',F3:'지역 3 · H2 + H3 합계',F2:'지역 2 · H4'}[channel];
   const samples=measured.filter(s=>s.channel_id===channel);
   return `<article class="result-card"><h3>${region}</h3>${samples.length?samples.map(s=>`<p><strong>${esc(s.device_id)} · ${s.stale?'오래된 값':'최근 관측'}</strong><br>${Object.entries(s.values).map(([k,v])=>`${esc(k)}: ${fmt(v,6)} ${esc(data.units[k])}`).join('<br>')}<br><small>${esc(s.observed_at)}</small></p>`).join(''):'<p>미측정 · 힘 센서의 실제 관측값을 기다립니다.</p>'}</article>`;
  }).join('');
  const controllers=data.samples.filter(s=>s.kind==='controller_status');
  $('#hardware-controller').innerHTML=controllers.length?controllers.map(s=>`<div class="result-card"><strong>${esc(s.device_id)} · ${s.stale?'통신 상태 오래됨':'최근 상태 수신'}</strong><p>ARM ${s.values.armed?'활성':'잠금'} / STOP ${s.values.latched?'래치':'해제'} / HB ${s.values.heartbeat?'수신':'없음'}<br>보정 플래그 ${s.values.calibrated}/15<br>${s.values.active.map((v,i)=>`H${i+1} ${v?'공급 명령 중':'공급 명령 없음'}`).join(' · ')}</p><small>제어기 보고값입니다. 실제 출구 위치·유량을 확인하는 센서값은 아닙니다.</small></div>`).join(''):'<p class="empty">Uno 상태 미수신 · 38400 baud / 실측 유량과 별도입니다.</p>';
 }catch(e){$('#hardware-state').textContent='장치 수신 서버 연결 실패: '+e.message;}
 finally{hardwareBusy=false;}
}
$('#hardware-refresh').onclick=refreshHardware;
$('#hardware-export').onclick=async()=>{
 if(!adminToken){$('#hardware-state').textContent='관리자 인증 후 실측 자료를 내려받을 수 있습니다.';return;}
 try{const data=await api('/api/hardware/export');download('rig-observations.json',data);$('#hardware-state').textContent=`실측 ${data.samples.length}/${data.total}개 내보내기 완료. 가공·보정 전 원시 자료입니다.`;}
 catch(e){$('#hardware-state').textContent=e.message;}
};
setInterval(()=>{if(!document.hidden&&$('#hardware').classList.contains('active')&&adminToken)refreshHardware();},3000);

let rigPredictionDuration=60;
$('#rig-pinn-go').onclick=()=>tab('hardware');
async function loadRigPINN(){
 try{
  // Static model metadata is available even when Sites has no Python API.
  const response=await fetch('rig-pinn.json');if(!response.ok)throw Error('모델 파일을 먼저 빌드하세요.');
  const m=await response.json(),test=m.test;
  const description=`${m.version} · ${m.training.epochs}회 학습 · 합성 시험 ${test.scenarios}조건 / ${test.points}시점 · 용기 물량 RMSE ${fmt(test.receiver_volume_rmse_ml,2)} mL · 호스 유량 RMSE ${fmt(test.hose_flow_rmse_ml_s,3)} mL/s. 실물 검증 ${m.validated_on_observations?'완료':'미완료'}.`;
  $('#rig-pinn-info').textContent=description;$('#rig-pinn-model-summary').textContent=description;
  const domain=m.physics.domain;rigPredictionDuration=domain.duration_s;
  for(const id of ['rig-va','rig-vb']){const input=$('#'+id);input.min=domain.initial_volume_ml[0];input.max=domain.initial_volume_ml[1];input.value=Math.max(Number(input.min),Math.min(Number(input.max),Number(input.value)));}
  for(let i=1;i<=4;i++){const input=$('#rig-z'+i);input.min=domain.outlet_height_m[0];input.max=domain.outlet_height_m[1];input.value=Math.max(Number(input.min),Math.min(Number(input.max),Number(input.value)));}
  $('#rig-pinn-title').textContent=`실험 장치용 PINN · ${rigPredictionDuration}초 예측`;
  $('#rig-pinn-domain').textContent=`높이는 모델 설정의 공통 기준면을 사용합니다. 실제 서보 각도 입력이 아닙니다. 초기 물량 ${domain.initial_volume_ml.join('~')} mL, 출구 높이 ${domain.outlet_height_m.join('~')} m 범위만 예측합니다.`;
  if(m.physics.calibrated_on_observations)$('#rig-pinn-scope').textContent='실측 보정 설정을 사용합니다. 학습·보정에 쓰지 않은 별도 실험의 검증 여부를 확인하세요. 예측 중에는 각 호스 출구 높이가 일정하다고 가정합니다.';
 }catch(e){$('#rig-pinn-info').textContent='실험 PINN: '+e.message;}
}
$('#rig-pinn-form').onsubmit=async e=>{
 e.preventDefault();if(!adminToken){$('#rig-pinn-result').textContent='Python 서버 관리자 인증 후 예측할 수 있습니다.';return;}
 const button=$('#rig-pinn-predict');button.disabled=true;
 try{
  const data=await api('/api/rig-pinn/predict','POST',{
   initial_volume_ml:[Number($('#rig-va').value),Number($('#rig-vb').value)],
   outlet_height_m:[1,2,3,4].map(i=>Number($('#rig-z'+i).value)),
   open_mask:[1,2,3,4].map(i=>$('#rig-g'+i).checked?1:0),
   times_s:Array.from({length:13},(_,i)=>i*rigPredictionDuration/12)
  });
  const last=data.times_s.length-1,source=data.source_volume_ml[last],receiver=data.receiver_volume_ml[last],flow=data.hose_flow_ml_s[last];
  $('#rig-pinn-result').innerHTML=`<div class="result-card"><strong>${data.valid?'모델 범위 안의 연구 예측':'물리 일관성 검사 미통과'} · ${data.calibrated_on_observations?'실측 보정 설정':'합성 치수 · 실제 측정값 아님'}</strong><p>${data.times_s[last]}초 후 수조 A ${fmt(source[0],1)} / B ${fmt(source[1],1)} mL<br>지역 1 ${fmt(receiver[0],1)} / 지역 3 ${fmt(receiver[1],1)} / 지역 2 ${fmt(receiver[2],1)} mL<br>${flow.map((v,i)=>`H${i+1} ${fmt(v,3)} mL/s`).join(' · ')}</p><small>물수지 잔차 ${Number(data.diagnostics.mass_balance_max_ml).toExponential(1)} mL · 시간 미분/유량 잔차 ${fmt(data.diagnostics.continuity_rmse_ml_s,3)} mL/s. 예측에서 서보 명령을 생성하지 않습니다.</small></div><div class="table-wrap"><table><thead><tr><th>초</th><th>수조 A</th><th>수조 B</th><th>지역 1</th><th>지역 3</th><th>지역 2</th></tr></thead><tbody>${data.times_s.map((t,i)=>`<tr><td>${fmt(t,1)}</td>${[...data.source_volume_ml[i],...data.receiver_volume_ml[i]].map(v=>`<td>${fmt(v,1)} mL</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
 }catch(error){$('#rig-pinn-result').textContent=error.message;}
 finally{button.disabled=false;}
};
loadRigPINN();
