// 저장 기록과 라이브 모두 같은 revision 규칙으로 미확정 자막을 교체한다.
const readable = text => /[\p{L}\p{N}]/u.test(text || '');
const isFinal = e => e.type === 'result' && e.kind === 'final';

export function finalRecords(events, at=Infinity) {
  const done=new Map();
  for(const e of events){
    if(!isFinal(e)||e.stale_revision||e.status==='cancelled'||e.published_s>at)continue;
    const old=done.get(e.segment_id);
    if(!old||(e.revision??e.job_id)>(old.revision??old.job_id))done.set(e.segment_id,e);
  }
  return [...done.values()].sort((a,b)=>a.start_frame-b.start_frame||a.segment_id-b.segment_id);
}

export function continuousCaption(events, deferProvisionalPunctuation=false) {
  const segments=new Map();
  for(const e of events){
    if(!e.displayed||e.stale_revision||e.status==='cancelled')continue;
    const old=segments.get(e.segment_id);
    if(old&&isFinal(old))continue;
    if(old&&((e.revision??e.job_id)<(old.revision??old.job_id)||(e.job_id===old.job_id&&e.type==='token'&&(old.type==='result'||e.token_index<=old.token_index))))continue;
    if(!isFinal(e)&&(!['success','provisional'].includes(e.status)||!readable(e.hypothesis)))continue;
    segments.set(e.segment_id,e);
  }
  const rows=[...segments.values()].sort((a,b)=>a.segment_id-b.segment_id);
  // 구두점뿐인 확정이 앞서 읽을 수 있던 두 구간 중 하나를 밀어내지 않게 한다.
  const done=rows.filter(e=>isFinal(e)&&(readable(e.hypothesis)||e.status==='failed'));
  const pending=rows.filter(e=>!isFinal(e));
  const visible=[...done.slice(-2),...pending].sort((a,b)=>a.segment_id-b.segment_id);
  const text=visible.map(e=>{
    if(e.status==='failed')return '[전사 실패]';
    const raw=e.hypothesis||'';
    // 이미 뒤 본문이 나온 부호와 확정 원문은 그대로 표시한다.
    return deferProvisionalPunctuation&&!isFinal(e)?raw.replace(/[.!?。？！…]+(?=[”’"'»）)\]}]*\s*$)/u,''):raw;
  }).filter(Boolean).join(' ');
  return {text,last:rows.at(-1)};
}
