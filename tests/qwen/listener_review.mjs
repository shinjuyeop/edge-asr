import test from 'node:test';
import assert from 'node:assert/strict';
import {createListener, runConditions} from '../../src/edge_asr/qwen/viewer/web/listener.mjs';
import {sourceReview, reviewTranscript} from '../../src/edge_asr/qwen/viewer/web/review.mjs';
import {continuousCaption} from '../../src/edge_asr/qwen/viewer/web/captions.mjs';
const sources=[{id:'a',start:0,end:32000,reference:'첫 문장'},{id:'b',start:32000,end:64000,reference:'다음 문장'}];
const event=(job,start,end,at,text,extra={})=>({job_id:job,segment_id:job,start_frame:start,end_frame:end,published_s:at,hypothesis:text,type:'result',kind:'final',status:'success',displayed:true,...extra});
const events=[event(0,0,16000,1.5,'첫'),event(1,16000,32000,3.2,'문장',{displayed:false}),event(2,32000,64000,4.5,'다음 문장')];

test('next audio is excluded while delayed final and suppressed final remain reviewable',()=>{
 const copy=JSON.stringify({sources,events});const r=sourceReview(sources,events,0);
 assert.equal(r.audioEnd,2);assert.equal(r.end,3.2);assert.equal(r.events.length,2);
 assert.equal(reviewTranscript(r),'첫 문장');assert.equal(r.crossesBoundary,false);
 assert.equal(JSON.stringify({sources,events}),copy);
 const stale=event(99,0,32000,9,'오래된 확정',{stale_revision:true});
 const clean=sourceReview(sources,[...events,stale],0);
 assert.equal(reviewTranscript(clean),reviewTranscript(r));assert.equal(clean.end,r.end);
});
test('cross-source results are preserved whole and failed/absent finals are explicit',()=>{
 const r=sourceReview(sources,[event(4,16000,40000,4,'경계를 넘은 전사'),event(5,0,1000,1,null,{status:'failed'})],0);
 assert.equal(r.crossesBoundary,true);assert.equal(reviewTranscript(r),'[전사 실패] 경계를 넘은 전사');
 assert.equal(sourceReview(sources,[],1).end,4);assert.match(reviewTranscript(sourceReview(sources,[],1)),/없습니다/);
 assert.equal(sourceReview(sources,[],2),null);
});
test('transport stops audio at boundary, waits for final, then advances on Next only',async()=>{
 const elements=new Map();const classes=()=>({add(){},remove(){},toggle(){}});
 class Element {
  constructor(){this.children=[];this.classList=classes();this.dataset={};this.value='';this.textContent='';this.clientWidth=100;this.selectedOptions=[];}
  replaceChildren(...v){this.children=v;} append(...v){this.children.push(...v);} setAttribute(){} querySelectorAll(){return [];} querySelector(){return null;}
  get firstElementChild(){return this.children[0];} getContext(){return {scale(){},clearRect(){},fillRect(){}};}
 }
 const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};get('replayStyle').value='source';get('volume').value='0.8';get('sort').value='start';
 let frame,pollRun;const starts=[];const ctx={currentTime:1,getOutputTimestamp(){return {contextTime:this.currentTime};},createGain(){return {gain:{},connect(){}};},createBufferSource(){return {connect(){},disconnect(){},stop(){},start(...args){starts.push(args);}};},resume:async()=>{},decodeAudioData:async()=>({duration:4,getChannelData(){return new Float32Array(64000);}})};
 const state={id:'fixture',sources,events,cursor:events.length,terminal:true,status:'completed',end_s:4.5,audio:{duration_s:4}};
 const fetch=async path=>({ok:true,json:async()=>path==='/api/catalog'?{runs:[],profiles:[]}:path==='/api/clock'?{received_s:10,sent_s:10}:state,arrayBuffer:async()=>new ArrayBuffer(0)});
 const listener=createListener({fetch,console,document:{getElementById:get,createElement:()=>new Element()},window:{addEventListener(){}},AudioContext:class{constructor(){return ctx;}},performance:{now:()=>0},devicePixelRatio:1,requestAnimationFrame(fn){frame=fn;},setTimeout(){},setInterval(fn,ms){if(ms===300)pollRun=fn;}});
 await listener.start();await listener.selectRun('fixture');await get('play').onclick();
 assert.equal(starts.at(-1)[1],0);assert.equal(starts.at(-1)[2],2);
 ctx.currentTime=3.5;frame(100);assert.equal(get('play').textContent,'일시정지');assert.equal(get('referenceText').textContent,'첫 문장');
 ctx.currentTime=4.3;frame(200);assert.equal(get('play').textContent,'이 음원 다시 듣기');assert.equal(get('seek').value,3.2);assert.equal(get('hypothesis').textContent,'첫 문장');assert.equal(get('nextSource').disabled,false);
 await get('nextSource').onclick();assert.equal(get('seek').min,2);assert.equal(starts.at(-1)[1],2);assert.equal(starts.at(-1)[2],2);
 ctx.currentTime=7;frame(300);assert.equal(get('play').textContent,'이 음원 다시 듣기');assert.equal(get('nextSource').disabled,true);assert.equal(get('hypothesis').textContent,'다음 문장');
 get('rewind').onclick();assert.equal(get('seek').min,2);assert.equal(get('play').textContent,'재생');
 get('replayStyle').value='continuous';get('replayStyle').onchange();assert.equal(get('play').textContent,'재생');assert.equal(get('nextSource').hidden,true);assert.equal(get('seek').min,0);await get('liveTab').onclick();assert.equal(get('nextSource').hidden,true);
 // 원음이 끝난 라이브 입력은 늦은 final을 기다린 뒤 화면을 유지한다.
 state.terminal=false;state.status='replaying';state.events=[];state.cursor=0;state.clock={t0_monotonic_s:10};
 await listener.selectRun('fixture');await get('play').onclick();
 ctx.currentTime=11.5;frame(400);
 assert.equal(get('play').textContent,'결과 대기');assert.equal(get('runStatus').textContent,'음원 종료 · 추론 결과 대기');assert.equal(get('referenceText').textContent,'다음 문장');
 state.terminal=true;state.status='completed';state.events=[events.at(-1)];state.cursor=1;
 pollRun();await new Promise(resolve=>setImmediate(resolve));frame(500);
 assert.equal(get('play').textContent,'추론 종료');assert.equal(get('hypothesis').textContent,'다음 문장');assert.equal(get('referenceText').textContent,'다음 문장');

});

test('continuous captions keep completed context, accept corrections and reject stale/empty updates',()=>{
 const token=(job,index,text,extra={})=>event(job,0,32000,index/10,text,{segment_id:0,type:'token',kind:'partial',status:'provisional',token_index:index,...extra});
 const rows=[token(1,1,'한국'),token(1,2,'한글'),token(1,3,'한')];
 assert.equal(continuousCaption(rows).text,'한');
 rows.push(token(2,1,'한국어'),token(1,4,'낡은 문자'),event(3,0,32000,3,null,{segment_id:0,kind:'partial',status:'failed'}),event(3,0,32000,3.1,null,{segment_id:0,kind:'partial',status:'cancelled'}));
 assert.equal(continuousCaption(rows).text,'한국어');
 rows.push(event(4,0,32000,4,'첫 확정',{segment_id:0}),token(5,1,'지나간 토큰'));
 assert.equal(continuousCaption(rows).text,'첫 확정');
 rows.push(token(6,1,'다음 발화',{segment_id:1,start_frame:32000,end_frame:48000}));
 assert.equal(continuousCaption(rows).text,'첫 확정 다음 발화');
 assert.equal(continuousCaption(rows).last.kind,'partial');
 rows.push(event(7,32000,48000,5,'다음 확정',{segment_id:1}));
 assert.equal(continuousCaption(rows).text,'첫 확정 다음 확정');
});

test('punctuation finals preserve readable context and audible repeats remain distinct',()=>{
 const a=event(0,0,1000,1,'안 돼'),b=event(1,1000,2000,2,'안 돼');
 const empty=event(2,2000,3000,3,'.');
 const pending=event(3,3000,4000,4,'다음',{kind:'partial',status:'success'});
 const punctToken={...empty,type:'token',status:'provisional',token_index:1};
 assert.equal(continuousCaption([a,b,punctToken]).text,'안 돼 안 돼');
 assert.equal(continuousCaption([a,b,empty,pending]).text,'안 돼 안 돼 다음');
 assert.equal(reviewTranscript(sourceReview([{id:'x',start:0,end:5000}],[a,b,empty,pending],0)),'안 돼 안 돼 .');
});

test('raw final history includes suppressed finals once and ignores stale revisions',()=>{
 const final=event(4,0,1600,2,'확정',{segment_id:0,revision:6,displayed:false});
 const stale=event(9,0,1600,3,'옛날',{segment_id:0,revision:4});
 const r=sourceReview(sources,[final,final,stale],0);
 assert.equal(reviewTranscript(r),'확정');
});

test('conditions identify the recorded scheduler and optional PCM boundaries',()=>{
 for(const policy of ['latest','paced','headroom','headroom_followup','first_text']){
  const rows=Object.fromEntries(runConditions({settings:{partial_mode:'continuous',continuous_policy:policy}}));
  assert.match(rows['부분 요청'],new RegExp(policy+' · '));
  assert.equal(rows['첫 부분 PCM 상한'],undefined);assert.equal(rows['발화 재개 앞 문맥'],undefined);
 }
 const rows=Object.fromEntries(runConditions({settings:{partial_mode:'continuous',first_snapshot_cap_s:1.2,
  split_on_pause:true,onset_context_s:0.2,onset_context_max_gap_s:0.6,vad_min_silence_s:0.5}}));
 assert.match(rows['부분 요청'],/연속 정책 기록 없음/);assert.doesNotMatch(rows['부분 요청'],/최신 입력 우선/);
 assert.equal(rows['표시'],'기록 없음');assert.equal(rows['첫 부분 PCM 상한'],'1.2초');
 assert.equal(rows['쉼 경계 분할'],'사용');assert.equal(rows['발화 재개 앞 문맥'],'0.2초');
 assert.equal(rows['재개 문맥 적용 최대 공백'],'0.6초');assert.equal(rows['VAD 최소 무음'],'0.5초');
 assert.equal(rows['오디오 가속'],undefined);
});
