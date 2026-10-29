"""원시 재채점·보류 집합 배제·실패 분모·계측 손상 거부를 검사한다."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import test_vad_annotations as fixtures
from edge_asr.benchmark import digest
from edge_asr.vad_annotations import score_segments

summary=fixtures.module('summarize_annotated_vad')


def write(path,value):path.write_text(json.dumps(value),encoding='utf-8')
def jsonl(path,rows):path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')


class AnnotationSummaryTests(unittest.TestCase):
    def setup_run(self,root):
        a=fixtures.annotation();b=deepcopy(a);b.update(id='noise',category='noise_candidate',annotation_status='unreviewed',target_interval_frames=None,
            regions=[{'start_frame':0,'end_frame':100,'label':'ignore','reason':'not reviewed'}])
        for row in (a,b):row['expected_processed']={p:{'pcm_sha256':p,'newly_clipped_samples':0} for p in ('identity','rms26')}
        h={**deepcopy(a),'id':'held','split':'holdout'}
        annotations=root/'annotations.jsonl';jsonl(annotations,[a,b,h]);runs=root/'runs';runs.mkdir()
        thermal={'temperature_mc':50000,'cpu0_freq_khz':1500000,'current_limits':0,'history_limits':14}
        for policy in ('identity','rms26'):
            folder=runs/policy;folder.mkdir();rows=[]
            for ref in (a,b):
                segments=[fixtures.segment(20,60,80)]
                rows.append({'id':ref['id'],'category':ref['category'],'annotation_status':ref['annotation_status'],'frames':100,
                    'audio_sha256':ref['audio_sha256'],'status':'completed','score':score_segments(ref,segments),'error':None,
                    'call_wall_s':.01,'process_cpu_s':.02,'resources':{'rss_observed_max_mib':10},'segments':segments,
                    'pipeline':{'complete':True,'frames':100,'processed_pcm_sha256':policy,'newly_clipped_samples':0,'native_budget_exhausted':False}})
            write(folder/'worker.json',{'mode':'provisional','policy':policy,'settings':summary.SETTINGS,'model_sha256':summary.MODEL_SHA,
                'annotation_sha256':digest(annotations),'planned_ids':['case','noise'],'status':'completed','failed_samples':0,'loading':{},'runtime':{}})
            write(folder/'execution.json',{'control_valid':True,'limit_verified':True,'status':'completed','source_commit':'fixture','before':thermal,'after':thermal})
            jsonl(folder/'thermal.jsonl',[thermal]);jsonl(folder/'samples.jsonl',rows);write(folder/'warmup.json',rows[0])
        return annotations,runs

    def test_unknown_noise_not_an_error_and_holdout_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);annotations,runs=self.setup_run(root)
            result=summary.summarize(root,annotations,runs)
            self.assertEqual(result['paired_successful'],2);self.assertEqual(result['holdout_not_executed'],1)
            self.assertFalse(result['gold_accuracy_available'])
            noise=result['table']['noise_candidate']['identity']
            self.assertEqual(noise['clips_with_emissions'],1)
            self.assertIsNone(noise['non_speech_false_alarm_fraction'])
            self.assertEqual(result['table']['short']['identity']['speech_miss_fraction'],0)

    def test_failure_is_reported_and_both_arms_use_same_successes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);annotations,runs=self.setup_run(root);folder=runs/'rms26'
            rows=summary.read_jsonl(folder/'samples.jsonl');rows[1].update(status='failed',score=None,error='native budget')
            jsonl(folder/'samples.jsonl',rows)
            info=json.loads((folder/'worker.json').read_text());info.update(status='completed_with_failures',failed_samples=1);write(folder/'worker.json',info)
            result=summary.summarize(root,annotations,runs)
            self.assertEqual(result['paired_successful'],1)
            self.assertEqual(result['table']['noise_candidate']['identity']['successful'],0)
            self.assertIsNone(result['table']['noise_candidate']['identity']['frame_counts'])
            self.assertEqual(result['policies']['rms26']['failures'][0]['id'],'noise')

    def test_tampered_score_missing_sample_and_bad_thermal_are_rejected(self):
        for change in ('score','missing','holdout','thermal','nan'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                root=Path(d);annotations,runs=self.setup_run(root);folder=runs/'identity'
                rows=summary.read_jsonl(folder/'samples.jsonl')
                if change=='score':rows[0]['score']['speech_miss_fraction']=.1
                elif change=='missing':rows.pop()
                elif change=='holdout':rows[1]['id']='held'
                elif change=='nan':rows[0]['call_wall_s']=float('nan')
                else:
                    thermal=summary.read_jsonl(folder/'thermal.jsonl');thermal[0]['temperature_mc']=76000;jsonl(folder/'thermal.jsonl',thermal)
                jsonl(folder/'samples.jsonl',rows)
                with self.assertRaises(ValueError):summary.summarize(root,annotations,runs)


if __name__=='__main__':unittest.main()
