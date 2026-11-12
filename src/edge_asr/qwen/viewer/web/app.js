const $ = id => document.getElementById(id);
const sec = value => value == null ? '—' : `${value.toFixed(2)}초`;
const clock = value => `${Math.floor(Math.max(0,value)/60).toString().padStart(2,'0')}:${Math.floor(Math.max(0,value)%60).toString().padStart(2,'0')}`;
let catalog, mode='replay', run=null, events=[], cursor=0, epoch=0, loadingAudio=false;
let context, gain, buffer, node, playing=false, baseContext=0, baseOffset=0, position=0;
let calibration=null, clockSamples=[], observations=[], lastEventKey='', tableKey='';
let lastArrival=null, follow=false, currentSource=-1, busy=false, selectionDirty=false;

async function api(path, body) {
  const response=await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {cache:'no-store'});
  const value=await response.json();
  if(!response.ok) throw Error(value.error || `HTTP ${response.status}`);
  return value;
}
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
function option(value,label){const o=document.createElement('option');o.value=value;o.textContent=label;return o;}
function audioContext(){if(!context){context=new AudioContext();gain=context.createGain();gain.gain.value=Number($('volume').value);gain.connect(context.destination);}return context;}
function outputTime(){const stamp=context?.getOutputTimestamp?.();return stamp?.contextTime>0 ? stamp.contextTime : context?.currentTime || 0;}
function nowPosition(){return playing ? Math.max(0,baseOffset+outputTime()-baseContext) : position;}
function stopAudio(){position=nowPosition();if(node){try{node.stop();}catch{}node.disconnect();node=null;}playing=false;}
function playAudio(offset=0,delay=0){
  stopAudio();const ctx=audioContext();baseContext=ctx.currentTime+Math.max(0,delay);baseOffset=offset;position=offset;
  if(offset<buffer.duration){node=ctx.createBufferSource();node.buffer=buffer;node.connect(gain);node.start(baseContext,offset);}
  playing=true;$('play').textContent=mode==='live'?'라이브 청취 중':'일시정지';
}
async function synchronize(){
  const before=performance.now()/1000;const r=await api('/api/clock');const after=performance.now()/1000;
  const rtt=Math.max(0,after-before-(r.sent_s-r.received_s));
  const sample={offset:((r.received_s-before)+(r.sent_s-after))/2,rtt,at:after};
  clockSamples.push(sample);clockSamples=clockSamples.filter(s=>after-s.at<30).slice(-20);
  calibration=clockSamples.reduce((a,b)=>a.rtt<b.rtt?a:b);
  observations.push({type:'clock',...sample});
}
async function calibrate(){for(let i=0;i<4;i++)await synchronize();}
function updateControls(){
  for(const id of ['replayTab','liveTab','runSelect','profileSelect','sourceSelect','joinSelect','joinRun'])$(id).disabled=busy;
  $('loadRun').disabled=busy||!catalog?.runs.length;
  $('prepare').disabled=busy||!catalog?.profiles.length||Boolean(catalog?.active);
  $('play').disabled=busy||selectionDirty||!buffer||(mode==='live'&&(run?.terminal||!['ready','replaying'].includes(run?.status)));
  $('stopRun').disabled=busy||mode!=='live'||!run?.can_stop;
  $('seek').disabled=busy||mode==='live'||!buffer;$('rewind').disabled=$('seek').disabled;
}
function clearRun(){
  stopAudio();epoch++;run=null;events=[];buffer=null;cursor=0;position=0;lastEventKey='';tableKey='';
  currentSource=-2;loadingAudio=false;follow=false;selectionDirty=false;observations=[];lastArrival=null;calibration=null;clockSamples=[];
  $('clockText').textContent='00:00 / 00:00';$('seek').value=0;$('seek').max=1;$('sync').textContent='음원 준비 전';
  $('play').textContent=mode==='live'?'시작':'재생';$('runStatus').textContent='실행을 선택하세요';
  $('conditions').replaceChildren();$('history').replaceChildren();$('network').textContent='관측 기록 없음';
  renderMetrics();renderTable();renderCaption(0);drawWave();updateControls();
}
function changeMode(value){
  mode=value;clearRun();
  $('replaySetup').hidden=value!=='replay';$('liveSetup').hidden=value!=='live';
  for(const name of ['replay','live']){$(`${name}Tab`).classList.toggle('selected',name===value);$(`${name}Tab`).setAttribute('aria-selected',String(name===value));}
  $('modeBadge').textContent=value==='live'?'라이브 · RP5 실제 추론':'기록 재생';
  notice(value==='live'?'구성과 음원을 선택해 실행을 준비하세요.':'기록을 선택해 원래 발행 시각으로 재생하세요.');
}
async function resetSelection(){
  // 종료가 실패하면 이전 실행을 유지해 재시도할 수 있게 한다.
  stopAudio();follow=false;epoch++;loadingAudio=false;selectionDirty=true;
  if(run?.can_stop){
    notice('이전 평가 종료 중입니다. 종료 확인 후 새 음원을 준비할 수 있습니다.');
    await api('/api/stop',{id:run.id});
  }
  clearRun();await refreshCatalog();
  notice('선택이 변경되었습니다. RP5 실행 준비를 누르세요. 이전 기록은 보존됩니다.');
}
async function refreshCatalog(){
  catalog=await api('/api/catalog');const selected=$('runSelect').value;
  $('runSelect').replaceChildren(...catalog.runs.map(r=>option(r.id,r.name.replace('results/optimization/opt3/',''))));
  $('joinSelect').replaceChildren(...catalog.runs.slice().reverse().map(r=>option(r.id,r.name.replace('results/optimization/opt3/',''))));
  if(catalog.runs.some(r=>r.id===selected))$('runSelect').value=selected;
  if(!$('profileSelect').options.length){$('profileSelect').replaceChildren(...catalog.profiles.map(p=>option(p.id,p.title)));profileChanged();}
  updateControls();
  $('connection').textContent='연결됨';
}
function profileChanged(){
  const profile=catalog?.profiles.find(p=>p.id===$('profileSelect').value);
  $('sourceSelect').replaceChildren(option('six','앞 6개 · 빠른 확인'),option('all','전체 목록 · 연속 입력'),...(profile?.sources||[]).map((s,i)=>option(s.id,`${i+1}. ${s.reference.slice(0,36)}`)));
}
async function selectRun(id){
  clearRun();run={id};
  notice('실행 정보와 원음을 불러오는 중입니다.');await poll();
}
async function loadAudio(myEpoch){
  if(loadingAudio||buffer||!run?.audio)return;
  loadingAudio=true;
  try{
    const response=await fetch(`/api/audio?id=${encodeURIComponent(run.id)}`);
    if(!response.ok)throw Error((await response.json()).error);
    const decoded=await audioContext().decodeAudioData(await response.arrayBuffer());
    if(epoch!==myEpoch)return;
    buffer=decoded;updateControls();
    $('seek').max=Math.max(buffer.duration,run.end_s+1);drawWave();
    notice(mode==='live'?'음원 준비 완료. 시작을 누르면 3초 뒤 같은 시간축으로 재생합니다.':'음원·실측 PCM 검증 완료. 재생하거나 아래 원음 행을 선택하세요.');
  }catch(e){if(epoch===myEpoch)notice(e.message,true);}finally{if(epoch===myEpoch)loadingAudio=false;}
}
function renderConditions(){
  if(!run)return;const m=run.config?.model||{},s=run.settings||{};
  const fields=[['모델',`${m.family||'준비 중'} · ${m.quantization||'—'}`],['스레드',`${m.num_threads??'—'} / ${m.num_threads_batch??m.num_threads??'—'}`],['고정 KV',m.cache_text_prefix?'사용':'미사용'],['부분 요청',`${s.partial_after_s??'—'}초 / 이후 ${s.partial_interval_s??'—'}초`],['구간',`${s.max_segment_s??'—'}초`],['VAD',`${s.vad_preprocess||'—'} / ${s.vad_threshold??'—'}`],['표시',s.token_stream?'생성 중 토큰':'완성 문자열'],['소스',(run.source_commit||'—').slice(0,8)]];
  $('conditions').replaceChildren(...fields.flatMap(([k,v])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=v;return[dt,dd];}));
}
function renderMetrics(){
  const summary=run?.summary;const pair=s=>s?`${sec(s.p50)} / ${sec(s.p95)}`:'집계 대기';
  $('firstMetric').textContent=pair(summary?.first_s);$('fourMetric').textContent=pair(summary?.first4_s);$('finalMetric').textContent=sec(summary?.file_end_s?.p95);
  $('cerMetric').textContent=run?.score?.cer!=null?`${(run.score.cer*100).toFixed(2)}%`:'평가 대기';
  $('cerDetail').textContent=run?.score?`치환 ${run.score.substitutions} · 삭제 ${run.score.deletions} · 삽입 ${run.score.insertions} / 정답 ${run.score.reference_characters}자`:'전체 입력 배출 후 확정 전사만 채점';
  $('coverage').textContent=summary?`전체 실행 통계 · 첫 표시 ${summary.first_s.n}/${run.sources.length}개 · 4자 ${summary.first4_s.n}/${run.sources.length}개 · 미출력은 별도 보존`:'전체 실행 통계는 종료 후 표시합니다. 현재 자막은 임시 가설일 수 있습니다.';
  $('failures').textContent=`실패 ${run?.failed_calls??'—'} / 입력 드롭 ${run?.dropped_chunks??'종료 후 집계'}`;
}
function renderTable(){
  const rows=run?.summary?.rows || (run?.sources||[]);const sorted=[...rows];const key=$('sort').value;
  if(key!=='start')sorted.sort((a,b)=>(b[key]??-1)-(a[key]??-1));
  $('sourceRows').replaceChildren(...sorted.map(row=>{
    const tr=document.createElement('tr');const index=run.sources.findIndex(s=>s.id===row.id);
    if(mode==='replay')tr.classList.add('listenable');tr.dataset.index=index;
    const values=[String(index+1).padStart(2,'0'),row.reference,sec(row.first_s),sec(row.first4_s),sec(row.file_end_s)];
    for(const text of values){const td=document.createElement('td');td.textContent=text;tr.append(td);}
    tr.title=row.ambiguous_final?'최종 구간이 원음 경계를 넘습니다. 원음별 CER는 계산하지 않습니다.':row.id;
    tr.onclick=()=>{if(mode==='replay'&&buffer){position=Math.max(0,row.start/16000-.3);playAudio(position);audioContext().resume();lastEventKey='';renderCaption(position);}};
    return tr;
  }));
}
function displayEvents(at){return events.filter(e=>e.displayed&&e.published_s<=at&&(e.type==='token'||e.type==='result'));}
function renderCaption(at){
  const sources=run?.sources||[];
  const sourceIndex=sources.findIndex(s=>s.start/16000<=at&&at<s.end/16000);
  const source=sources[sourceIndex];
  if(sourceIndex!==currentSource||!run){currentSource=sourceIndex;$('referenceText').textContent=source?.reference||(sources.length?'원음 사이 또는 입력 종료 구간입니다.':'음성을 재생하면 해당 원음의 정답이 표시됩니다.');$('referenceText').classList.toggle('muted',!source);$('sourceTag').textContent=source?`${sourceIndex+1} / ${sources.length}`:'—';$('sourceName').textContent=source?.id||'현재 듣고 있는 원음';
    for(const tr of $('sourceRows').children)tr.classList.toggle('current',Number(tr.dataset.index)===sourceIndex);
  }
  const shown=displayEvents(mode==='live'&&follow?Infinity:at),e=shown.at(-1);
  const eventKey=e?`${e.job_id}:${e.type}:${e.published_s}`:'';
  if(eventKey!==lastEventKey||!e){
    lastEventKey=eventKey;$('hypothesis').textContent=e?.status==='failed'?'이 구간의 전사에 실패했습니다.':e?.hypothesis||'RP5의 결과를 기다립니다.';$('hypothesis').classList.toggle('muted',!e?.hypothesis);
    $('captionState').textContent=e?e.status==='failed'?'실패':e.type==='token'?'생성 중':e.kind==='final'?'확정':'부분 결과':'대기';
    const matches=e?sources.filter(s=>e.start_frame<s.end&&e.end_frame>s.start):[];
    $('captionTarget').textContent=e?`원음 ${matches.map(s=>sources.indexOf(s)+1).join('·')||'—'} · 처리 ${e.start_frame/16000>=0?(e.start_frame/16000).toFixed(2):'—'}–${(e.end_frame/16000).toFixed(2)}초 · RP5 발행 +${e.published_s.toFixed(2)}초`:'자막의 처리 구간을 함께 표시합니다.';
    $('history').replaceChildren(...shown.filter(e=>e.type==='result'&&e.kind==='final').slice(-8).reverse().map(e=>{const div=document.createElement('div');div.className='history-row';const span=document.createElement('span'),p=document.createElement('p');span.textContent=`${clock(e.published_s)} · 구간 ${e.segment_id+1} · ${e.status==='failed'?'실패':'확정'}`;p.textContent=e.hypothesis||'빈 전사 · 실패 기록';div.append(span,p);return div;}));
  }
  const heard=source?shown.filter(r=>r.hypothesis&&r.status!=='failed'&&r.start_frame<source.end&&r.end_frame>source.start&&sources.filter(s=>r.start_frame<s.end&&r.end_frame>s.start).length===1):[];
  const first=heard.find(r=>r.hypothesis.trim()),four=heard.find(r=>Array.from(r.hypothesis).filter(c=>/[\p{L}\p{N}]/u.test(c)).length>=4);
  $('sourceTiming').textContent=source?`이 원음 · 첫 표시 ${sec(first?first.published_s-source.start/16000:null)} · 4자 ${sec(four?four.published_s-source.start/16000:null)}${mode==='live'?' · 현재 관측':''}`:'';
  const targets=e?sources.filter(s=>e.start_frame<s.end&&e.end_frame>s.start):[];
  const late=targets.length>0&&!(targets.length===1&&targets[0]===source);
  $('lateReference').hidden=!late;$('targetReference').textContent=late?targets.map(s=>s.reference).join(' / '):'';
}
async function poll(){
  if(!run)return;const myEpoch=epoch,id=run.id,requestedCursor=cursor;
  try{
    const state=await api(`/api/state?id=${encodeURIComponent(id)}&cursor=${cursor}`);
    if(epoch!==myEpoch||requestedCursor!==cursor)return;
    const received=performance.now()/1000;for(const e of state.events){
      if(e.displayed){const t0=state.clock?.t0_monotonic_s;
        const extra=mode==='live'&&t0&&calibration?received+calibration.offset-t0-e.published_s:null;
        if(extra!=null)lastArrival=extra;
        observations.push({type:'received',job_id:e.job_id,event:e.type,published_s:e.published_s,browser_received_s:received,forwarding_estimate_s:extra});
      }
    }
    events.push(...state.events);cursor=state.cursor;run=state;
    const names={preparing:'모델 준비·워밍업 중',ready:'준비 완료',replaying:'1배속 입력·추론 중',completed:'실행 완료',failed:'실패 기록 있음',interrupted:'중단됨'};
    $('runStatus').textContent=names[run.status]||run.status;$('connection').textContent='연결됨';
    if(catalog.active?.id===run.id&&!run.can_stop)catalog.active=null;
    updateControls();
    if(!run.terminal&&['blocked','failed'].includes(run.guard))notice('평가가 중단되었습니다. 실행 기록과 제한 상태를 확인하세요.',true);
    renderConditions();renderMetrics();
    const key=`${run.sources.length}:${Boolean(run.summary)}:${$('sort').value}`;if(key!==tableKey){tableKey=key;renderTable();}
    if(run.audio&&!buffer&&!selectionDirty)await loadAudio(myEpoch);
    if(mode==='live'&&run.terminal&&follow){follow=false;notice('추론이 종료되었습니다. 원음 종료 뒤 도착한 결과까지 확인하세요. 기록 모드에서 다시 재생할 수 있습니다.');await refreshCatalog();}
  }catch(e){if(epoch===myEpoch){$('connection').textContent='연결 확인 필요';notice(e.message,true);}}
}
async function play(){
  if(!buffer)return;const myEpoch=epoch;await audioContext().resume();if(epoch!==myEpoch)return;
  if(mode==='replay'){
    if(playing){stopAudio();$('play').textContent='이어 듣기';}
    else{const end=Math.max(buffer.duration,run.end_s+1);playAudio(position>=end?0:position);}
    return;
  }
  if(playing){notice('라이브 입력은 계속 진행됩니다. 소리만 끄려면 음량을 0으로 조절하세요.');return;}
  await calibrate();if(epoch!==myEpoch)return;let start=run.clock?.t0_monotonic_s||run.scheduled?.start_monotonic_s;
  if(!start){const planned=await api('/api/start',{id:run.id});if(epoch!==myEpoch)return;start=planned.start_monotonic_s;}
  const until=start-(performance.now()/1000+calibration.offset);
  follow=true;playAudio(Math.max(0,-until),Math.max(0,until));
  observations.push({type:'play',server_origin_s:start,offset_s:baseOffset,context_start_s:baseContext,calibration});
  $('play').textContent='라이브 청취 중';notice(until>0?'시작 예약 완료. 음성과 RP5 입력을 같은 시간축으로 시작합니다.':'진행 중인 평가에 합류했습니다. 앞부분은 기록 재생에서 확인하세요.');
}
function drawWave(){
  const canvas=$('wave'),width=Math.max(1,canvas.clientWidth),height=58;canvas.width=width*devicePixelRatio;canvas.height=height*devicePixelRatio;
  const c=canvas.getContext('2d');c.scale(devicePixelRatio,devicePixelRatio);c.clearRect(0,0,width,height);
  const data=buffer?.getChannelData(0),at=nowPosition(),ratio=buffer?Math.min(1,at/buffer.duration):0;
  for(let x=0;x<width;x+=4){let peak=.035;if(data){const a=Math.floor(x/width*data.length),b=Math.floor((x+4)/width*data.length);for(let i=a;i<b;i+=Math.max(1,Math.floor((b-a)/30)))peak=Math.max(peak,Math.abs(data[i]));}const h=Math.max(2,Math.min(50,peak*90));c.fillStyle=x<width*ratio?'#555':'#d5d5d5';c.fillRect(x,height/2-h/2,2,h);}
}
let lastDraw=0;
function tick(ms){
  const at=nowPosition(),end=buffer?Math.max(buffer.duration,run?.end_s+1||0):0;
  if(playing&&mode==='replay'&&at>=end){stopAudio();position=end;$('play').textContent='다시 듣기';}
  if(playing&&mode==='live'&&run?.terminal&&at>=end){stopAudio();$('play').textContent='청취 종료';}
  if(ms-lastDraw>80){lastDraw=ms;$('clockText').textContent=`${clock(at)} / ${clock(buffer?.duration||0)}`;$('seek').value=at;renderCaption(at);drawWave();
    if(mode==='live'&&calibration){const serverNow=performance.now()/1000+calibration.offset;const origin=run?.clock?.t0_monotonic_s||run?.scheduled?.start_monotonic_s;
      const drift=origin&&playing?at-(serverNow-origin):null;
      $('sync').textContent=`왕복 ${(calibration.rtt*1000).toFixed(0)}ms · 재생 차이 ${drift==null?'—':`${(drift*1000).toFixed(0)}ms`}`;
      $('network').textContent=`서버 발행→브라우저 수신 추정 ${lastArrival==null?'—':sec(lastArrival)} · 왕복/2 ${(calibration.rtt*500).toFixed(0)}ms (시계 추정 참고, 편도 실측 아님)`;
    }else $('sync').textContent=buffer?'실측 시간축 재생 · 네트워크 지연 제외':'음원 준비 전';
  }
  requestAnimationFrame(tick);
}
function action(fn){return async()=>{
  if(busy)return;busy=true;updateControls();
  try{await fn();}catch(e){notice(e.message,true);}finally{busy=false;updateControls();}
};}
$('replayTab').onclick=action(()=>changeMode('replay'));
$('liveTab').onclick=action(async()=>{
  changeMode('live');await refreshCatalog();
  if(catalog.active){
    $('profileSelect').value=catalog.active.profile;profileChanged();$('sourceSelect').value=catalog.active.selection;
    await selectRun(catalog.active.id);
  }
});
$('profileSelect').onchange=action(async()=>{profileChanged();await resetSelection();});
$('sourceSelect').onchange=action(resetSelection);
$('loadRun').onclick=action(()=>selectRun($('runSelect').value));$('play').onclick=action(play);
$('joinRun').onclick=action(async()=>{await selectRun($('joinSelect').value);if(run.terminal){const id=run.id;changeMode('replay');await selectRun(id);}});
$('prepare').onclick=action(async()=>{const r=await api('/api/launch',{profile:$('profileSelect').value,selection:$('sourceSelect').value});await refreshCatalog();await selectRun(r.id);});
$('stopRun').onclick=action(async()=>{
  stopAudio();follow=false;epoch++;loadingAudio=false;selectionDirty=true;notice('평가 종료 중입니다.');
  await api('/api/stop',{id:run.id});await refreshCatalog();await poll();notice('평가가 종료되었습니다. 기록은 보존되며 다시 준비할 수 있습니다.');
});
$('rewind').onclick=()=>{stopAudio();position=0;lastEventKey='';renderCaption(0);$('play').textContent='재생';};
$('seek').oninput=()=>{if(mode!=='replay')return;const was=playing;stopAudio();position=Number($('seek').value);lastEventKey='';if(was)playAudio(position);renderCaption(position);};
$('volume').oninput=()=>{if(gain)gain.gain.value=Number($('volume').value);};$('sort').onchange=renderTable;
$('download').onclick=()=>{const blob=new Blob([JSON.stringify({run:run?.id,mode,userAgent:navigator.userAgent,audioOutputLatency:context?.outputLatency??null,scope:'브라우저 수신·재생 추정. 물리 화면/음향 실측 아님.',observations},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`listener-${run?.id||'session'}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
window.addEventListener('resize',drawWave);
await refreshCatalog().catch(e=>notice(e.message,true));
setInterval(()=>{if(mode==='live'&&!busy)poll();},300);
setInterval(()=>{if(mode==='live'&&playing)synchronize().catch(()=>{});},5000);
requestAnimationFrame(tick);
