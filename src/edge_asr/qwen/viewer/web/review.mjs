// 원래 발행 시각을 유지하면서 선택한 원음의 결과만 검토한다.
export function sourceReview(sources, events, index) {
  const source = sources[index];
  if (!source) return null;
  const relevant = events.filter(e =>
    e.start_frame < source.end && e.end_frame > source.start &&
    (e.type === 'token' || e.type === 'result'));
  const finals = relevant.filter(e => e.type === 'result' && e.kind === 'final')
    .sort((a, b) => a.start_frame - b.start_frame);
  return {
    index, source, events: relevant, finals,
    start: source.start / 16000,
    audioEnd: source.end / 16000,
    end: Math.max(source.end / 16000, ...relevant.map(e => e.published_s)),
    crossesBoundary: finals.some(e => e.start_frame < source.start || e.end_frame > source.end),
  };
}

export function reviewTranscript(review) {
  return review.finals.map(e => e.status === 'failed' ? '[전사 실패]' :
    e.hypothesis || '[빈 전사]').join(' ') || '이 원음에 해당하는 확정 전사가 없습니다.';
}
