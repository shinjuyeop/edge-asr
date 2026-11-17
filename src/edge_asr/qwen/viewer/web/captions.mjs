// 저장 기록과 라이브 모두 같은 revision 규칙으로 미확정 자막을 교체한다.
export function continuousCaption(events) {
  const segments=new Map();
  for(const e of events){
    if(!e.displayed||e.stale_revision)continue;
    const old=segments.get(e.segment_id);
    if(old?.type==='result'&&old.kind==='final')continue;
    if(old&&(e.job_id<old.job_id||(e.job_id===old.job_id&&e.type==='token'&&(old.type==='result'||e.token_index<=old.token_index))))continue;
    if(e.kind==='partial'&&!['success','provisional'].includes(e.status))continue;
    segments.set(e.segment_id,e);
  }
  const rows=[...segments.values()].sort((a,b)=>a.segment_id-b.segment_id);
  const done=rows.filter(e=>e.type==='result'&&e.kind==='final');
  const pending=rows.filter(e=>!(e.type==='result'&&e.kind==='final'));
  const visible=[...done.slice(-2),...pending].sort((a,b)=>a.segment_id-b.segment_id);
  return {text:visible.map(e=>e.status==='failed'?'[전사 실패]':e.hypothesis||'').filter(Boolean).join(' '),last:visible.at(-1)};
}
