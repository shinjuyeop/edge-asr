"""시간 구간 채점·불확실 라벨·검수 승인 경계·보류 화자·실패 행을 검사한다."""
from array import array
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from edge_asr.audio_policy import preprocess
from edge_asr.dataset import Audio
from edge_asr.segmentation import pcm_bytes
from edge_asr.textgrid import Interval
from edge_asr.vad_annotations import load_annotations,score_segments,validate_annotation

ROOT=Path(__file__).resolve().parents[1]
def module(name):
    with patch.object(sys,'path',[str(ROOT/'scripts'),*sys.path]):
        spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
prepare=module('prepare_vad_annotations');review=module('review_vad_annotations');probe=module('m30_annotated_vad_probe')


def annotation(status='corpus_provisional'):
    row={'schema':'vad-annotation-v1','id':'case','audio_path':'data/test.wav','audio_sha256':'a'*64,
         'frames':100,'sample_rate_hz':16000,'category':'short','split':'development','annotation_status':status,
         'reviewer':None,'reviewed_at':None,'review_method':None,'review_note':None,'endpoint_reference_frame':None,
         'target_interval_frames':[20,60],
         'regions':[{'start_frame':a,'end_frame':b,'label':k,'reason':'fixture'} for a,b,k in ((0,20,'non_speech'),(20,60,'speech'),(60,100,'non_speech'))]}
    if status=='human_reviewed':row.update(reviewer='test fixture',reviewed_at='2026-10-29T12:00:00+09:00',review_method='listening_and_waveform',review_note='synthetic test only')
    return row


def segment(a,b,emitted=None,**extra):
    return {'owned_start_frame':a,'owned_end_frame':b,'emitted_at_audio_frame':emitted if emitted is not None else b,
            'endpoint_eligible':True,'after_flush':False,**extra}


class AnnotationTests(unittest.TestCase):
    def test_union_weighted_scores_and_ignored_regions(self):
        row=annotation();row['regions']=[{'start_frame':a,'end_frame':b,'label':k,'reason':'fixture'} for a,b,k in ((0,20,'speech'),(20,50,'ignore'),(50,70,'speech'),(70,100,'non_speech'))]
        row['target_interval_frames']=[0,20]
        result=score_segments(row,[segment(10,30),segment(20,70),segment(20,70)])
        self.assertEqual(result['counts']['true_positive_frames'],30)
        self.assertEqual(result['counts']['false_negative_frames'],10)
        self.assertEqual(result['counts']['predicted_in_ignored_frames'],30)
        self.assertEqual(result['counts']['false_positive_frames'],0)
        self.assertEqual(result['speech_miss_fraction'],.25)
        self.assertEqual(result['target_detected_frames'],10)
        self.assertFalse(result['gold']);self.assertIsNone(result['endpoint']['audio_timeline_delay_s'])

    def test_false_alarm_and_unknown_noise_are_distinct(self):
        row=annotation();result=score_segments(row,[segment(10,80)])
        self.assertEqual(result['non_speech_false_alarm_fraction'],.5)
        row.update(annotation_status='unreviewed',target_interval_frames=None,
                   regions=[{'start_frame':0,'end_frame':100,'label':'ignore','reason':'unreviewed noise'}])
        result=score_segments(row,[segment(10,80)])
        self.assertIsNone(result['speech_miss_fraction']);self.assertIsNone(result['non_speech_false_alarm_fraction'])
        self.assertIsNone(result['target_fully_missed']);self.assertEqual(result['emitted_segments'],1)

    def test_reviewed_endpoint_negative_eof_and_hard_limit(self):
        row=annotation('human_reviewed');row['endpoint_reference_frame']=60
        result=score_segments(row,[segment(20,50,55)])
        self.assertEqual(result['endpoint']['audio_timeline_delay_s'],-5/16000)
        self.assertEqual(result['endpoint']['boundary_error_s'],-10/16000)
        self.assertIsNone(result['endpoint']['wall_delay_s'])
        for extra in ({'after_flush':True},{'endpoint_eligible':False}):
            result=score_segments(row,[segment(20,60,80,**extra)])
            self.assertIsNone(result['endpoint']['audio_timeline_delay_s'])
        row['regions'][-1]['label']='ignore'
        with self.assertRaises(ValueError):validate_annotation(row)

    def test_status_and_time_integrity(self):
        mutations=[lambda r:r.update(annotation_status='human_reviewed'),lambda r:r.update(frames=True),
                   lambda r:r['regions'][1].update(start_frame=19),lambda r:r['regions'][-1].update(end_frame=99),
                   lambda r:r.update(endpoint_reference_frame=60),lambda r:r.update(audio_sha256='bad'),
                   lambda r:r.update(annotation_status='unreviewed'),lambda r:r.update(target_interval_frames=[80,20])]
        for mutate in mutations:
            row=annotation();mutate(row)
            with self.assertRaises(ValueError):validate_annotation(row)
        row=annotation('human_reviewed');row['reviewed_at']='2026-10-29'
        with self.assertRaises(ValueError):validate_annotation(row)
        for event in (segment(-1,50),segment(20,101),segment(20,60,50)):
            with self.assertRaises(ValueError):score_segments(annotation(),[event])

    def test_loader_duplicate_path_escape_and_reviewed_default(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'annotations.jsonl';row=annotation()
            path.write_text(json.dumps(row)+'\n');loaded,_=load_annotations(path,root)
            with self.assertRaises(ValueError):probe.select_development(loaded,'reviewed')
            hold={**row,'id':'hold','split':'holdout'}
            self.assertEqual([r['id'] for r in probe.select_development([row,hold],'provisional')],['case'])
            path.write_text((json.dumps(row)+'\n')*2)
            with self.assertRaises(ValueError):load_annotations(path,root)
            row['audio_path']='../outside.wav';path.write_text(json.dumps(row))
            with self.assertRaises(ValueError):load_annotations(path,root)

    def test_review_decision_binding_and_no_implicit_mutation(self):
        row=annotation();before=deepcopy(row);decision=annotation('human_reviewed')
        decision={k:v for k,v in decision.items() if k in ('id','audio_sha256','annotation_status','reviewer','reviewed_at','review_method','review_note','regions','endpoint_reference_frame')}
        result=review.apply_reviews([row],[decision])
        self.assertEqual(row,before);self.assertEqual(result[0]['annotation_status'],'human_reviewed')
        for bad in ({**decision,'audio_sha256':'b'*64},{**decision,'split':'development'},{**decision,'reviewer':None}):
            with self.assertRaises(ValueError):review.apply_reviews([row],[bad])
        with self.assertRaises(ValueError):review.apply_reviews([row],[decision,decision])

    def test_phoneme_projection_is_conservative_and_uses_clip_origin(self):
        regions=prepare.project_regions([Interval(1,1.1,'nn'),Interval(1.1,1.2,'<SIL>'),
            Interval(1.2,1.3,'<IVER>'),Interval(1.3,1.4,'<LAUGH-네>'),Interval(1.4,1.5,'unregistered')],16000,24000)
        self.assertEqual([r['label'] for r in regions],['speech','non_speech','ignore','ignore','ignore'])
        self.assertEqual(regions[0]['start_frame'],0);self.assertEqual(regions[-1]['end_frame'],8000)
        self.assertEqual(prepare.HOLDOUT,['s05','s10','s15','s20','s25','s30','s35','s40'])

    def test_probe_keeps_failures_unscored_and_verifies_pcm(self):
        class SilentVad:
            def reset(self):pass
            def accept_waveform(self,block):pass
            def empty(self):return True
            def is_speech_detected(self):return False
            def flush(self):pass
        row=annotation();row.update(frames=1600,target_interval_frames=[0,1600],regions=[{'start_frame':0,'end_frame':1600,'label':'speech','reason':'fixture'}])
        samples=array('f',[.01]*1600);audio=Audio(samples,16000,'a'*64)
        pcm,meta=preprocess(samples,'identity')
        row['expected_processed']={'identity':{'pcm_sha256':hashlib.sha256(pcm_bytes(pcm)).hexdigest(),'newly_clipped_samples':meta['newly_clipped_samples']}}
        result=probe.measure(SilentVad(),audio,row,'identity',list)
        self.assertEqual(result['status'],'completed');self.assertTrue(result['score']['target_fully_missed'])
        row['expected_processed']['identity']['pcm_sha256']='b'*64
        result=probe.measure(SilentVad(),audio,row,'identity',list)
        self.assertEqual(result['status'],'failed');self.assertIsNone(result['score'])


if __name__=='__main__':unittest.main()
