const $ = id => document.getElementById(id);
const sec = value => value == null ? '—' : `${value.toFixed(2)}초`;
const clock = value => `${Math.floor(Math.max(0,value)/60).toString().padStart(2,'0')}:${Math.floor(Math.max(0,value)%60).toString().padStart(2,'0')}`;
let catalog, mode='replay', run=null, events=[], cursor=0, epoch=0, loadingAudio=false;
let context, gain, buffer, node, playing=false, baseContext=0, baseOffset=0, position=0;
let calibration=null, clockSamples=[], observations=[], lastEventKey='', tableKey='';
let lastArrival=null, follow=false, currentSource=-1, busy=false, selectionDirty=false;
let audioEnded=false, completionNotified=false, resultsShown=false;
const isMic = () => run?.input?.kind==='microphone';
const isLive = () => mode!=='replay';

async function api(path, body) {
  const response=await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {cache:'no-store'});
  const value=await response.json();
  if(!response.ok) throw Error(value.error || `HTTP ${response.status}`);
  return value;
}
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
function connection(connected){$('connection').textContent=connected?'연결됨':'연결 확인 필요';$('connection').dataset.state=connected?'connected':'error';}
function option(value,label){const o=document.createElement('option');o.value=value;o.textContent=label;o.title=label;return o;}
function selectedRun(){const select=$('runSelect');$('selectedRun').textContent=select.value?select.selectedOptions[0]?.textContent||'':'';}
function audioContext(){if(!context){context=new AudioContext();gain=context.createGain();gain.gain.value=Number($('volume').value);gain.connect(context.destination);}return context;}
function outputTime(){const stamp=context?.getOutputTimestamp?.();return stamp?.contextTime>0 ? stamp.contextTime : context?.currentTime || 0;}
function nowPosition(){if(mode==='mic'&&run?.terminal)return Math.max(run.audio?.duration_s||0,run.end_s||0);if(mode==='mic'&&follow&&calibration){const t0=run?.clock?.t0_monotonic_s||run?.scheduled?.start_monotonic_s;return t0?Math.max(0,performance.now()/1000+calibration.offset-t0):0;}return playing ? Math.max(0,baseOffset+outputTime()-baseContext) : position;}
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
  for(const id of ['replayTab','liveTab','micTab','runSelect','profileSelect','sourceSelect','joinSelect','joinRun','deviceSelect','micDuration','micReference','refreshDevices'])$(id).disabled=busy;
  $('loadRun').disabled=busy||!catalog?.runs.length;
  $('runSelect').disabled=busy||!catalog?.runs.length;
  $('joinSelect').disabled=busy||!catalog?.runs.length;$('joinRun').disabled=$('joinSelect').disabled;
  $('profileSelect').disabled=busy||!catalog?.profiles.length;
  $('sourceSelect').disabled=busy||!catalog?.profiles.length;
  $('sort').disabled=busy||!run?.sources?.length;
  $('micReference').disabled=busy||(mode==='mic'&&run&&!run.terminal);
  $('prepare').disabled=busy||!catalog?.profiles.length||Boolean(catalog?.active)||(mode==='mic'&&!$('deviceSelect').value);
  $('play').disabled=busy||selectionDirty||(mode==='mic'&&follow&&run?.status==='replaying')||(mode==='mic'?(!run||(!run.terminal&&!['ready','replaying'].includes(run.status))||(run.terminal&&!buffer)):(!buffer||(mode==='live'&&(run?.terminal||!['ready','replaying'].includes(run?.status)))));
  $('stopRun').disabled=busy||!isLive()||!run?.can_stop;
  $('finishMic').disabled=busy||mode!=='mic'||!run?.can_stop||run?.status!=='replaying';
  $('seek').disabled=busy||isLive()||!buffer;$('rewind').disabled=$('seek').disabled;
  for(const button of $('sourceRows').querySelectorAll('button')){button.disabled=$('seek').disabled;button.closest('tr').classList.toggle('listenable',!button.disabled);}
}
function clearRun(){
  stopAudio();epoch++;run=null;events=[];buffer=null;cursor=0;position=0;lastEventKey='';tableKey='';
  currentSource=-2;loadingAudio=false;follow=false;selectionDirty=false;observations=[];lastArrival=null;calibration=null;clockSamples=[];
  audioEnded=false;completionNotified=false;resultsShown=false;$('finalResult').open=false;$('finalTranscript').textContent='확정 결과 대기';
  $('clockText').textContent='00:00 / 00:00';$('seek').value=0;$('seek').max=1;$('sync').textContent='음원 준비 전';
  $('play').textContent=mode==='mic'?'마이크 시작':mode==='live'?'시작':'재생';$('runStatus').textContent=isLive()?'실행을 준비하세요':'기록을 불러오세요';$('runStatus').dataset.state='';
  const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent='실행';dd.textContent='선택 전';
  $('conditions').replaceChildren(dt,dd);$('history').replaceChildren();$('network').textContent='관측 기록 없음';
  renderMetrics();renderTable();renderCaption(0);drawWave();updateControls();
}
function changeMode(value){
  for(const name of ['replay','live','mic']){const tab=$(`${name}Tab`);tab.setAttribute('aria-selected',String(name===value));tab.tabIndex=name===value?0:-1;}
  if(mode===value)return;
  mode=value;clearRun();
  $('replaySetup').hidden=value!=='replay';$('liveSetup').hidden=value==='replay';
  $('micFields').hidden=value!=='mic';$('sourceSelect').hidden=value==='mic';$('sourceLabel').hidden=value==='mic';$('finishMic').hidden=value!=='mic';
  $('inputHelp').textContent=value==='mic'?'RP5에 연결된 마이크를 사용합니다. 녹음은 입력 종료 후 재생할 수 있습니다.':'모델 준비가 끝나면 시작을 누르세요.';
  $('liveSetup').setAttribute('aria-labelledby',value==='mic'?'micTab':'liveTab');
  $('modeBadge').textContent=value==='mic'?'RP5 마이크':value==='live'?'RP5 라이브':'기록 재생';
  notice(value==='mic'?'입력 장치와 구성을 선택해 준비하세요.':value==='live'?'구성과 음원을 선택해 준비하세요.':'저장된 실행을 선택한 뒤 기록 불러오기를 누르세요.');
}
async function refreshDevices(){const data=await api('/api/devices');$('deviceSelect').replaceChildren(...(data.devices.length?data.devices.map(d=>option(d.id,d.name)):[option('','입력 장치 없음')]));if(!data.devices.length)notice('RP5에서 녹음 장치를 찾지 못했습니다. 연결 후 장치 새로 확인을 누르세요.',true);updateControls();}
async function resetSelection(){
  // 종료가 실패하면 이전 실행을 유지해 재시도할 수 있게 한다.
  stopAudio();follow=false;epoch++;loadingAudio=false;selectionDirty=true;
  if(run?.can_stop){
    notice('이전 평가 종료 중입니다. 종료 확인 후 새 음원을 준비할 수 있습니다.');
    await api('/api/stop',{id:run.id});
  }
  clearRun();await refreshCatalog();
  notice('선택이 변경되었습니다. RP5 실행 준비를 누르세요.');
}
async function refreshCatalog(){
  catalog=await api('/api/catalog');const selected=$('runSelect').value;
  $('runSelect').replaceChildren(...(catalog.runs.length?catalog.runs.map(r=>option(r.id,r.name.replace('results/optimization/opt3/',''))):[option('','저장된 실행 없음')]));
  $('joinSelect').replaceChildren(...(catalog.runs.length?catalog.runs.slice().reverse().map(r=>option(r.id,r.name.replace('results/optimization/opt3/',''))):[option('','참여할 실행 없음')]));
  if(catalog.runs.some(r=>r.id===selected))$('runSelect').value=selected;
  selectedRun();
  if(!$('profileSelect').value){$('profileSelect').replaceChildren(...(catalog.profiles.length?catalog.profiles.map(p=>option(p.id,p.title)):[option('','등록된 구성 없음')]));profileChanged();}
  updateControls();
  connection(true);
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
    notice(mode==='live'?'준비 완료. 시작을 누르면 3초 뒤 입력과 재생이 시작됩니다.':'음원 준비 완료. 재생하거나 원음 번호를 선택하세요.');
  }catch(e){if(epoch===myEpoch)notice(e.message,true);}finally{if(epoch===myEpoch)loadingAudio=false;}
}
function renderConditions(){
  if(!run)return;const m=run.config?.model||{},s=run.settings||{};
  const fields=[['모델',`${m.family||'준비 중'} · ${m.quantization||'—'}`],['스레드',`${m.num_threads??'—'} / ${m.num_threads_batch??m.num_threads??'—'}`],['고정 KV',m.cache_text_prefix?'사용':'미사용'],['부분 요청',s.partial_mode==='adaptive'?`가변${s.adaptive_policy==='budgeted'?`·첫 요청 보호 / ${s.partial_max_calls}회·${s.partial_budget_s}초 예산`:''} / 첫 ${s.partial_after_s}초 / 새 음성 ${s.partial_min_new_s}초 / 끝 여유 ${s.partial_tail_margin_s}초`:`첫 ${s.partial_after_s??'—'}초 / 다음 ${s.partial_followup_s??s.partial_interval_s??'—'}초 뒤 / 이후 ${s.partial_interval_s??'—'}초`],['구간',`${s.max_segment_s??'—'}초`],['VAD',`${s.vad_preprocess||'—'} / ${s.vad_threshold??'—'}`],['표시',s.token_stream?'생성 중 토큰':'완성 문자열'],['소스',(run.source_commit||'—').slice(0,8)]];
  if(m.family==='qwen3_gguf'){fields[0][0]='텍스트 모델';fields.splice(1,0,['오디오 양자화',m.audio_quantization??'q8_0']);}
  $('conditions').replaceChildren(...fields.flatMap(([k,v])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=v;return[dt,dd];}));
}
function renderMetrics(){
  const summary=run?.summary;const pair=s=>s&&s.n?`${sec(s.p50)} / ${sec(s.p95)}`:'—';
  const micMetrics=isMic()||(!run&&mode==='mic');
  $('firstScope').textContent=micMetrics?'완료 VAD 구간 시작 → 텍스트 발행':'원음 시작 → 텍스트 발행';
  $('finalScope').textContent=micMetrics?'VAD/ASR 구간 끝 → 최종 결과':'파일 끝 → 최종 결과';
  $('sourceColumn').textContent=micMetrics?'구간':'원음';$('tableHelp').textContent=isLive()?run?.terminal?'기록 재생에서 원음·구간을 선택해 들을 수 있습니다.':'입력 중에는 위치를 이동할 수 없습니다. 종료 후 기록 재생에서 확인하세요.':isMic()?'완료된 발화 구간입니다. 구간 번호를 선택하면 녹음을 재생합니다.':'원음 번호를 선택하면 해당 위치부터 재생합니다.';
  $('measurementScope').textContent=micMetrics?'첫 표시·4자 표시는 완료된 VAD/ASR 구간 시작 기준입니다. 최종 반환은 해당 구간 끝부터 최종 결과까지입니다. 녹음 시작부터 첫 발화까지의 무음은 포함하지 않습니다.':'첫 표시·4자 표시는 원음 파일 시작 기준입니다. 성공 호출 중 한 원음에만 귀속된 표시를 집계합니다. 최종 반환은 파일 입력 끝부터 마지막으로 겹치는 최종 결과까지입니다.';
  $('tableTitle').textContent=micMetrics?'완료된 발화 구간별 지연':'원음별 지연';$('referenceColumn').textContent=micMetrics?'확정 전사':'정답';
  $('firstMetric').textContent=pair(summary?.first_s);$('fourMetric').textContent=pair(summary?.first4_s);$('finalMetric').textContent=sec(summary?.file_end_s?.p95);
  $('cerMetric').textContent=run?.score?.cer!=null?`${(run.score.cer*100).toFixed(2)}%`:run?.terminal||(isMic()&&!run.input.reference)?'미측정':run?'평가 대기':'—';
  $('cerDetail').textContent=run?.score?`치환 ${run.score.substitutions} · 삭제 ${run.score.deletions} · 삽입 ${run.score.insertions} / 정답 ${run.score.reference_characters}자`:isMic()?'입력한 정답이 있을 때 녹음 전체를 종료 후 채점':'전체 입력 배출 후 확정 전사만 채점';
  $('coverage').textContent=summary?`관측 수 / 전체 ${isMic()?'구간':'원음'} · 첫 표시 ${summary.first_s.n}/${run.sources.length} · 4자 ${summary.first4_s.n}/${run.sources.length}`:run?'입력 처리가 끝나면 전체 측정값을 표시합니다.':'실행을 불러오면 측정값을 확인할 수 있습니다.';
  $('failures').textContent=`실패 ${run?.failed_calls??'—'} · 입력 드롭 ${run?.dropped_chunks??'—'}`;
  for(const id of ['firstMetric','fourMetric','finalMetric','cerMetric'])$(id).dataset.empty=String(['—','미측정','평가 대기'].includes($(id).textContent));
  $('micTelemetry').hidden=!isMic();
  if(isMic()){const t=run.capture||{},d=run.device_status||{};$('micTelemetry').textContent=`입력 ${sec(t.captured_frames==null?null:t.captured_frames/16000)} · 레벨 ${t.rms_dbfs==null?'—':t.rms_dbfs.toFixed(1)+' dBFS'} · 클리핑 ${t.clipped_samples??'—'}샘플 · 하드웨어 넘침 ${t.hardware_overruns??'—'} · 온도 ${d.temperature_mc==null?'—':(d.temperature_mc/1000).toFixed(1)+'°C'} · 제한 플래그 ${d.current_limits??'—'}`;}
}
function highlightSource(){
  for(const tr of $('sourceRows').querySelectorAll('tr[data-index]')){
    const current=Number(tr.dataset.index)===currentSource;tr.classList.toggle('current',current);
    tr.querySelector('button')?.setAttribute('aria-current',String(current));
  }
}
function renderTable(){
  const rows=run?.summary?.rows || (run?.sources||[]);const sorted=[...rows];const key=$('sort').value;
  if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.className='table-empty';td.textContent=run?.terminal?'표시할 원음·발화 구간이 없습니다.':run?'완료된 구간을 기다리고 있습니다.':'실행을 불러오면 원음별 측정값이 표시됩니다.';tr.append(td);$('sourceRows').replaceChildren(tr);return;}
  if(key!=='start')sorted.sort((a,b)=>(b[key]??-1)-(a[key]??-1));
  $('sourceRows').replaceChildren(...sorted.map(row=>{
    const tr=document.createElement('tr');const index=run.sources.findIndex(s=>s.id===row.id);
    tr.dataset.index=index;
    const values=[String(index+1).padStart(2,'0'),isMic()?row.final_hypothesis||'표시 없음':row.reference,sec(row.first_s),sec(row.first4_s),sec(row.file_end_s),sec(row.max_growth_gap_s)];
    for(const text of values){const td=document.createElement('td');td.textContent=text;tr.append(td);}
    if(mode==='replay'){const button=document.createElement('button');button.className='source-play';button.textContent=values[0];button.setAttribute('aria-label',`${isMic()?'구간':'원음'} ${index+1} 재생`);tr.firstElementChild.replaceChildren(button);}
    tr.title=row.ambiguous_final?'최종 구간이 원음 경계를 넘습니다. 원음별 CER는 계산하지 않습니다.':row.id;
    tr.onclick=()=>{if(mode==='replay'&&buffer&&!busy){position=Math.max(0,row.start/16000-.3);playAudio(position);audioContext().resume();lastEventKey='';renderCaption(position);}};
    return tr;
  }));
  highlightSource();updateControls();
}
function displayEvents(at){return events.filter(e=>e.displayed&&e.published_s<=at&&(e.type==='token'||e.type==='result'));}
function renderCaption(at){
  const sources=run?.sources||[];
  let sourceIndex=sources.findIndex(s=>s.start/16000<=at&&at<s.end/16000);
  if(sourceIndex<0&&sources.length&&at>=sources.at(-1).end/16000)sourceIndex=sources.length-1;
  const source=sources[sourceIndex];
  if(sourceIndex!==currentSource||!run){currentSource=sourceIndex;$('referenceText').textContent=source?.reference||(sources.length?'원음 사이의 무음 구간입니다.':mode==='mic'?'정답을 입력하면 녹음 종료 후 CER를 계산합니다.':'음원을 불러오면 정답이 표시됩니다.');$('referenceText').classList.toggle('muted',!source);$('sourceTag').textContent=source?`${sourceIndex+1} / ${sources.length}`:'—';$('sourceName').textContent=source?.id||'';
    highlightSource();
  }
  if(isMic()){$('referenceText').textContent=run.input.reference||'자유 발화 · 정답 미입력';$('sourceName').textContent=`RP5 · ${run.input.device}`;$('sourceTag').textContent='마이크';}
  const shown=displayEvents(isLive()&&follow?Infinity:at),e=shown.at(-1);
  const eventKey=e?`${e.job_id}:${e.type}:${e.published_s}`:'';
  if(eventKey!==lastEventKey||!e){
    lastEventKey=eventKey;$('hypothesis').textContent=e?.status==='failed'?'이 구간의 전사에 실패했습니다.':e?.hypothesis||(isLive()?'입력이 시작되면 자막이 표시됩니다.':'재생하면 자막이 표시됩니다.');$('hypothesis').classList.toggle('muted',!e?.hypothesis);
    $('captionState').textContent=e?e.status==='failed'?'실패':e.type==='token'?'생성 중':e.kind==='final'?'확정':'부분 결과':'대기';
    $('captionState').dataset.state=e?e.status==='failed'?'failed':e.type==='result'&&e.kind==='final'?'final':'partial':'waiting';
    const matches=e?sources.filter(s=>e.start_frame<s.end&&e.end_frame>s.start):[];
    $('captionTarget').textContent=e?`${isMic()?'발화 구간 '+(e.segment_id+1):'원음 '+(matches.map(s=>sources.indexOf(s)+1).join('·')||'—')} · 처리 ${e.start_frame/16000>=0?(e.start_frame/16000).toFixed(2):'—'}–${(e.end_frame/16000).toFixed(2)}초 · RP5 발행 +${e.published_s.toFixed(2)}초`:'';
    const finals=shown.filter(e=>e.type==='result'&&e.kind==='final').sort((a,b)=>a.start_frame-b.start_frame);
    $('finalTranscript').textContent=finals.map(e=>e.status==='failed'?'[전사 실패]':e.hypothesis||'[빈 전사]').join(' ')||'확정 결과 대기';
    $('history').replaceChildren(...finals.slice(-8).reverse().map(e=>{const div=document.createElement('div');div.className='history-row';const span=document.createElement('span'),p=document.createElement('p');span.textContent=`${clock(e.published_s)} · 구간 ${e.segment_id+1} · ${e.status==='failed'?'실패':'확정'}`;p.textContent=e.hypothesis||'빈 전사 · 실패 기록';div.append(span,p);return div;}));
  }
  if(!resultsShown&&((buffer&&at>=buffer.duration)||(isLive()&&follow&&run?.terminal))){resultsShown=true;$('finalResult').open=true;}
  const heard=source?shown.filter(r=>r.hypothesis&&r.status!=='failed'&&r.start_frame<source.end&&r.end_frame>source.start&&sources.filter(s=>r.start_frame<s.end&&r.end_frame>s.start).length===1):[];
  const first=heard.find(r=>r.hypothesis.trim()),four=heard.find(r=>Array.from(r.hypothesis).filter(c=>/[\p{L}\p{N}]/u.test(c)).length>=4);
  $('sourceTiming').textContent=source?`이 ${isMic()?'발화 구간':'원음'} · 첫 표시 ${sec(first?first.published_s-source.start/16000:null)} · 4자 ${sec(four?four.published_s-source.start/16000:null)}${mode==='live'?' · 현재 관측':''}`:'';
  const targets=e?sources.filter(s=>e.start_frame<s.end&&e.end_frame>s.start):[];
  const late=targets.length>0&&!(targets.length===1&&targets[0]===source);
  $('lateReference').hidden=!late||isMic();$('targetReference').textContent=late?targets.map(s=>s.reference).join(' / '):'';
}
async function poll(){
  if(!run)return;const myEpoch=epoch,id=run.id,requestedCursor=cursor;
  try{
    const state=await api(`/api/state?id=${encodeURIComponent(id)}&cursor=${cursor}`);
    if(epoch!==myEpoch||requestedCursor!==cursor)return;
    const received=performance.now()/1000;for(const e of state.events){
      if(e.displayed){const t0=state.clock?.t0_monotonic_s;
        const extra=isLive()&&t0&&calibration?received+calibration.offset-t0-e.published_s:null;
        if(extra!=null)lastArrival=extra;
        observations.push({type:'received',job_id:e.job_id,event:e.type,published_s:e.published_s,browser_received_s:received,forwarding_estimate_s:extra});
      }
    }
    events.push(...state.events);cursor=state.cursor;run=state;
    const names={preparing:'모델 준비·워밍업 중',ready:'준비 완료',replaying:isMic()?'마이크 입력·추론 중':audioEnded?'음원 종료 · 추론 결과 대기':'1배속 입력·추론 중',draining:'입력 종료 · 남은 추론 처리 중',completed:'추론 완료 · 결과 보존',failed:'실패 기록 있음',interrupted:'중단됨'};
    $('runStatus').textContent=names[run.status]||run.status;$('runStatus').dataset.state=run.status;connection(true);
    if(catalog.active?.id===run.id&&!run.can_stop)catalog.active=null;
    updateControls();
    if(!run.terminal&&['blocked','failed'].includes(run.guard))notice('평가가 중단되었습니다. 실행 기록과 제한 상태를 확인하세요.',true);
    renderConditions();renderMetrics();if(run.error)notice(run.error.message||String(run.error),true);
    const key=`${run.sources.length}:${Boolean(run.summary)}:${$('sort').value}`;if(key!==tableKey){tableKey=key;renderTable();}
    if(run.audio&&!buffer&&!selectionDirty&&(!isMic()||run.terminal))await loadAudio(myEpoch);
    if(isLive()&&run.terminal&&follow&&!completionNotified){completionNotified=true;notice(isMic()?'입력이 종료되었습니다. 결과를 확인하거나 녹음 재생을 누르세요.':'추론이 종료되었습니다. 확정 전사와 측정값을 확인하세요.');await refreshCatalog();}
    if(mode==='mic')$('play').textContent=run.terminal?'녹음 재생':run.status==='ready'?'마이크 시작':'마이크 입력 중';
  }catch(e){if(epoch===myEpoch){connection(false);notice(e.message,true);}}
}
async function play(){
  if(mode==='mic'){
    if(run.terminal){const id=run.id;changeMode('replay');await selectRun(id);return play();}
    await calibrate();if(!run.clock){await api('/api/start',{id:run.id});}follow=true;notice('마이크 입력이 시작됩니다. RP5에 연결한 마이크에 말하세요.');return;
  }
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
  if(isMic()&&!buffer){const peak=run.capture?.peak||0;c.fillStyle='#555';c.fillRect(0,22,width*peak,14);return;}
  const data=buffer?.getChannelData(0),at=nowPosition(),ratio=buffer?Math.min(1,at/buffer.duration):0;
  for(let x=0;x<width;x+=4){let peak=.035;if(data){const a=Math.floor(x/width*data.length),b=Math.floor((x+4)/width*data.length);for(let i=a;i<b;i+=Math.max(1,Math.floor((b-a)/30)))peak=Math.max(peak,Math.abs(data[i]));}const h=Math.max(2,Math.min(50,peak*90));c.fillStyle=x<width*ratio?'#555':'#d5d5d5';c.fillRect(x,height/2-h/2,2,h);}
}
let lastDraw=0;
function tick(ms){
  const at=nowPosition(),end=buffer?Math.max(buffer.duration,run?.end_s+1||0):0;
  if(playing&&buffer&&at>=buffer.duration&&!audioEnded){
    audioEnded=true;
    if(mode==='live'&&!run?.terminal){$('runStatus').textContent='음원 종료 · 추론 결과 대기';$('play').textContent='결과 대기';notice('음원 재생이 끝났습니다. 남은 추론 결과를 기다립니다.');}
  }
  if(playing&&mode==='replay'&&at>=end){stopAudio();position=end;$('play').textContent='다시 듣기';}
  if(playing&&mode==='live'&&run?.terminal&&buffer&&at>=buffer.duration){stopAudio();$('play').textContent='추론 종료';}
  if(ms-lastDraw>80){lastDraw=ms;$('clockText').textContent=`${clock(Math.min(at,buffer?.duration||0))} / ${clock(buffer?.duration||0)}${mode==='live'&&audioEnded&&!run?.terminal?` · 결과 대기 ${sec(at-buffer.duration)}`:''}`;$('seek').value=at;renderCaption(at);drawWave();
    if(mode==='mic')$('clockText').textContent=run?.terminal?`녹음 ${clock(run.audio?.duration_s||0)} · 결과 보존`:`${clock(at)} / 최대 ${clock(run?.input?.duration_limit_s||0)}`;
    if(isLive()&&calibration){const serverNow=performance.now()/1000+calibration.offset;const origin=run?.clock?.t0_monotonic_s||run?.scheduled?.start_monotonic_s;
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
$('micTab').onclick=action(async()=>{
  changeMode('mic');await refreshCatalog();await refreshDevices();
  if(catalog.active){await selectRun(catalog.active.id);if(isMic()){$('profileSelect').value=catalog.active.profile;follow=Boolean(run.clock);await calibrate();}else notice('다른 WAV 평가가 실행 중입니다. 종료 후 마이크를 준비하세요.');}
});
const tabs=['replayTab','liveTab','micTab'];
for(const [index,id] of tabs.entries())$(id).onkeydown=event=>{
  if(busy)return;
  const target=event.key==='ArrowRight'?(index+1)%tabs.length:event.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:event.key==='Home'?0:event.key==='End'?tabs.length-1:null;
  if(target===null)return;event.preventDefault();for(const tab of tabs)$(tab).tabIndex=tab===tabs[target]?0:-1;$(tabs[target]).focus();
};
$('runSelect').onchange=selectedRun;
$('profileSelect').onchange=action(async()=>{profileChanged();await resetSelection();});
$('sourceSelect').onchange=action(resetSelection);
$('loadRun').onclick=action(()=>selectRun($('runSelect').value));$('play').onclick=action(play);
$('joinRun').onclick=action(async()=>{await selectRun($('joinSelect').value);if(run.terminal){const id=run.id;changeMode('replay');await selectRun(id);}});
$('prepare').onclick=action(async()=>{const request={profile:$('profileSelect').value,selection:$('sourceSelect').value};if(mode==='mic')request.microphone={device:$('deviceSelect').value,seconds:Number($('micDuration').value),reference:$('micReference').value};const r=await api('/api/launch',request);await refreshCatalog();await selectRun(r.id);});
$('refreshDevices').onclick=action(refreshDevices);
for(const id of ['deviceSelect','micDuration','micReference'])$(id).onchange=action(resetSelection);
$('finishMic').onclick=action(async()=>{await api('/api/finish',{id:run.id});$('finishMic').disabled=true;notice('마이크 입력을 종료하고 남은 추론을 기다립니다. 결과와 녹음은 보존됩니다.');});
$('stopRun').onclick=action(async()=>{
  stopAudio();follow=false;epoch++;loadingAudio=false;selectionDirty=true;notice('평가 종료 중입니다.');
  await api('/api/stop',{id:run.id});await refreshCatalog();await poll();notice('평가가 종료되었습니다. 기록은 보존되며 다시 준비할 수 있습니다.');
});
$('rewind').onclick=()=>{stopAudio();position=0;lastEventKey='';renderCaption(0);$('play').textContent='재생';};
$('seek').oninput=()=>{if(mode!=='replay')return;const was=playing;stopAudio();position=Number($('seek').value);lastEventKey='';if(was)playAudio(position);renderCaption(position);};
$('volume').oninput=()=>{if(gain)gain.gain.value=Number($('volume').value);};$('sort').onchange=renderTable;
$('download').onclick=()=>{const blob=new Blob([JSON.stringify({run:run?.id,mode,userAgent:navigator.userAgent,audioOutputLatency:context?.outputLatency??null,scope:'브라우저 수신·재생 추정. 물리 화면/음향 실측 아님.',observations},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`listener-${run?.id||'session'}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
window.addEventListener('resize',drawWave);
await refreshCatalog().catch(e=>{connection(false);notice(e.message,true);});
setInterval(()=>{if(isLive()&&!busy)poll();},300);
setInterval(()=>{if(isLive()&&(playing||follow))synchronize().catch(()=>{});},5000);
requestAnimationFrame(tick);
