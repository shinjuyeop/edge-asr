import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
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
 const starts=[];const ctx={currentTime:1,getOutputTimestamp(){return {contextTime:this.currentTime};},createGain(){return {gain:{},connect(){}};},createBufferSource(){return {connect(){},disconnect(){},stop(){},start(...args){starts.push(args);}};},resume:async()=>{}};
 const sandbox={sourceReview,reviewTranscript,continuousCaption,console,document:{getElementById:get,createElement:()=>new Element()},window:{addEventListener(){}},AudioContext:class{constructor(){return ctx;}},performance:{now:()=>0},devicePixelRatio:1,requestAnimationFrame(){},setTimeout(){},setInterval(){}};
 vm.createContext(sandbox);
 let code=fs.readFileSync(new URL('../../src/edge_asr/qwen/viewer/web/app.js',import.meta.url),'utf8');
 code=code.replace(/^import .*\n/gm,'').split('await refreshCatalog().catch')[0];
 code+=`\nglobalThis.control={setup(s,e){catalog={runs:[],profiles:[]};run={sources:s,terminal:true,status:'completed',end_s:4.5};events=e;buffer={duration:4,getChannelData(){return new Float32Array(64000);}};selectReview(0);},play,tick,get(){return {playing,position,done:reviewDone,index:review?.index};},continuous(){mode='replay';$('replayStyle').value='continuous';$('replayStyle').onchange();},live(){changeMode('live');return reviewing();}};`;
 vm.runInContext(code,sandbox);const c=sandbox.control;c.setup(sources,events);await c.play();
 assert.equal(starts.at(-1)[1],0);assert.equal(starts.at(-1)[2],2);
 ctx.currentTime=3.5;c.tick(100);assert.equal(c.get().playing,true);assert.equal(get('referenceText').textContent,'첫 문장');
 ctx.currentTime=4.3;c.tick(200);assert.equal(c.get().playing,false);assert.equal(c.get().position,3.2);assert.equal(get('hypothesis').textContent,'첫 문장');assert.equal(get('nextSource').disabled,false);
 await get('nextSource').onclick();assert.equal(c.get().index,1);assert.equal(starts.at(-1)[1],2);assert.equal(starts.at(-1)[2],2);
 ctx.currentTime=7;c.tick(300);assert.equal(c.get().done,true);assert.equal(get('nextSource').disabled,true);assert.equal(get('hypothesis').textContent,'다음 문장');
 get('rewind').onclick();assert.equal(c.get().index,1);assert.equal(c.get().position,2);assert.equal(c.get().done,false);
 c.continuous();assert.equal(get('play').textContent,'재생');assert.equal(get('nextSource').hidden,true);assert.equal(get('seek').min,0);assert.equal(c.live(),false);
});

test('continuous captions keep completed context, accept corrections and reject stale/empty updates',()=>{
 const token=(job,index,text,extra={})=>event(job,0,32000,index/10,text,{segment_id:0,type:'token',kind:'partial',status:'provisional',token_index:index,...extra});
 const rows=[token(1,1,'한국'),token(1,2,'한글'),token(1,3,'한')];
 assert.equal(continuousCaption(rows).text,'한');
 rows.push(token(2,1,'한국어'),token(1,4,'낡은 문자'),event(3,0,32000,3,null,{segment_id:0,kind:'partial',status:'failed'}));
 assert.equal(continuousCaption(rows).text,'한국어');
 rows.push(event(4,0,32000,4,'첫 확정',{segment_id:0}),token(5,1,'지나간 토큰'));
 assert.equal(continuousCaption(rows).text,'첫 확정');
 rows.push(token(6,1,'다음 발화',{segment_id:1,start_frame:32000,end_frame:48000}));
 assert.equal(continuousCaption(rows).text,'첫 확정 다음 발화');
 assert.equal(continuousCaption(rows).last.kind,'partial');
 rows.push(event(7,32000,48000,5,'다음 확정',{segment_id:1}));
 assert.equal(continuousCaption(rows).text,'첫 확정 다음 확정');
});
