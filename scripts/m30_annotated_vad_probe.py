"""RP5의 개발 집합만 VAD 구간 진단한다. 예비 주석을 확정 정답으로 승격하지 않는다."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from edge_asr.benchmark import digest
from edge_asr.dataset import load_audio
from edge_asr.evaluation import RssSampler
from edge_asr.runtime import runtime_info
from edge_asr.segmentation import SegmentPipeline
from edge_asr.vad_annotations import load_annotations,score_segments
from m30_segment_probe import MODEL_SHA
from m30_thermal_run import save

SETTINGS={'rate':16000,'num_threads':1,'window':512,'threshold':.5,'min_speech_s':.1,
          'min_silence_s':.3,'hard_limit_s':5,'native_soft_limit_s':5,'native_buffer_s':31,
          'native_active_budget_s':30,'prefix_frames':0,'suffix_frames':0,'paced':False,
          'repetitions':1,'warmups':1}


def select_development(rows, mode):
    if mode not in ('reviewed','provisional'):raise ValueError('검수/예비 모드가 필요합니다.')
    selected=[r for r in rows if r['split']=='development' and r['annotation_status']!='excluded']
    allowed={'human_reviewed'} if mode=='reviewed' else {'corpus_provisional','unreviewed'}
    if not selected or any(r['annotation_status'] not in allowed for r in selected):
        raise ValueError('검수 상태와 실행 모드가 다릅니다. 미검수 예비 점검은 --mode provisional로 명시하세요.')
    return selected


def measure(vad, audio, row, policy, convert):
    segments=[]
    began,cpu=time.perf_counter(),time.process_time()
    rss=RssSampler();pipeline=None
    result={'id':row['id'],'audio_sha256':audio.sha256,'frames':len(audio.samples),'category':row['category'],
            'annotation_status':row['annotation_status'],'status':'failed','error':None,'score':None}
    try:
        with rss:
            pipeline=SegmentPipeline(vad,policy,5,lambda event,pcm:segments.append(event),convert=convert)
            for offset in range(0,len(audio.samples),1600):
                pipeline.accept(audio.samples[offset:offset+1600],final=offset+1600>=len(audio.samples))
            meta=pipeline.finish()
        expected=row['expected_processed'][policy]
        if (not meta['complete'] or meta['frames']!=row['frames'] or
                meta['processed_pcm_sha256']!=expected['pcm_sha256'] or
                meta['newly_clipped_samples']!=expected['newly_clipped_samples']):
            raise ValueError('Host의 공통 전처리 PCM과 불일치')
        result['status']='completed'
    except Exception as error:
        result['error']=f'{type(error).__name__}: {error}'
    result.update(call_wall_s=time.perf_counter()-began,process_cpu_s=time.process_time()-cpu,
                  resources=rss.metadata(),pipeline=pipeline.metadata() if pipeline else None,segments=segments)
    # 채점 비용은 위 호출 시간/CPU에 포함하지 않는다.
    if result['status']=='completed':result['score']=score_segments(row,segments)
    return result


def probe(root, model, annotations, output, policy, mode):
    rows,sha=load_annotations(annotations,root)
    receipt=json.loads((annotations.parent/'provenance.json').read_text())
    if receipt['files'][annotations.name]!=sha:raise ValueError('고정된 주석 목록 해시 불일치')
    selected=select_development(rows,mode)
    if policy not in ('identity','rms26') or digest(model)!=MODEL_SHA:raise ValueError('검증한 정책과 Silero 파일이 필요합니다.')
    for row in selected:
        if row['frames']>180*16000 or policy not in row.get('expected_processed',{}):raise ValueError('입력 길이/공통 PCM 준비 오류')
    if (output/'worker.json').exists():raise FileExistsError('기존 실행을 덮어쓰지 않습니다.')
    output.mkdir(parents=True,exist_ok=True)
    info={'status':'running','process_id':os.getpid(),'mode':mode,'policy':policy,'settings':SETTINGS,
          'annotation_sha256':sha,'model_sha256':MODEL_SHA,'planned_ids':[r['id'] for r in selected],
          'holdout_ids':[r['id'] for r in rows if r['split']=='holdout'],
          'excluded_ids':[r['id'] for r in rows if r['annotation_status']=='excluded'],
          'scope':'unpaced WAV/공통 PCM/VAD 계산. 마이크·ASR·첫 자막·Refine·실제 시계 endpoint 아님.'}
    save(output/'worker.json',info)
    try:
        began,cpu=time.perf_counter(),time.process_time()
        with RssSampler() as loading:
            import numpy as np
            import sherpa_onnx as sherpa
            config=sherpa.VadModelConfig();config.sample_rate=16000;config.num_threads=1;config.provider='cpu'
            for k,v in dict(model=str(model.resolve()),threshold=.5,min_speech_duration=.1,min_silence_duration=.3,
                            max_speech_duration=5,window_size=512).items():setattr(config.silero_vad,k,v)
            vad=sherpa.VoiceActivityDetector(config,buffer_size_in_seconds=31)
        info['loading']={'wall_s':time.perf_counter()-began,'process_cpu_s':time.process_time()-cpu,'resources':loading.metadata()}
        info['runtime']=runtime_info();save(output/'worker.json',info)
        convert=lambda block:np.asarray(block,dtype=np.float32)
        def call(row):
            audio=load_audio(root/row['audio_path'],16000)
            if audio.sha256!=row['audio_sha256'] or len(audio.samples)!=row['frames']:raise ValueError('WAV 해시/길이 불일치')
            return measure(vad,audio,row,policy,convert)
        warm=next((r for r in selected if r['category']=='short'),selected[0])
        warm_result=call(warm);save(output/'warmup.json',warm_result)
        if warm_result['status']!='completed':raise RuntimeError('워밍업 실패: '+str(warm_result['error']))
        failed=0
        with (output/'samples.jsonl').open('x',encoding='utf-8') as log:
            for row in selected:
                result=call(row);failed+=result['status']!='completed'
                log.write(json.dumps(result,ensure_ascii=False)+'\n');log.flush()
        info.update(status='completed_with_failures' if failed else 'completed',failed_samples=failed)
    except BaseException as error:
        info.update(status='failed',error=repr(error));raise
    finally:save(output/'worker.json',info)
    return info


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,default=Path('models/silero-vad-k2-20261029/silero_vad.onnx'))
    p.add_argument('--annotations',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--policy',choices=['identity','rms26'],required=True);p.add_argument('--mode',choices=['reviewed','provisional'],default='reviewed')
    a=p.parse_args();probe(Path.cwd(),a.model,a.annotations,a.output,a.policy,a.mode)
