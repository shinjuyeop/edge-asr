from array import array
import copy
import hashlib
import sys
from pathlib import Path
import unittest

from edge_asr.audio_policy import preprocess
from edge_asr.segmentation import pcm_bytes
from edge_asr.vad_probe import source_metrics
from test_segmentation import run

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from summarize_segment_probe import verify_row


class SegmentSummaryTests(unittest.TestCase):
    def fixture(self):
        samples=array('f',[.02]*50000);pipeline,records=run(samples)
        pcm,_=preprocess(samples,'identity');segments=[r for r,_ in records]
        expected={'audio_sha256':'a'*64,'condition':'fixture','reference':'말','source_frames':26000,
                  'padded_frames':50000,'expected_processed':{'identity':{'pcm_sha256':hashlib.sha256(pcm_bytes(pcm)).hexdigest(),'newly_clipped_samples':0}}}
        row={'audio_sha256':'a'*64,'condition':'fixture','hard_limit_s':3,
             'pipeline':pipeline.metadata(),'segments':segments,'replay':None,
             **source_metrics(segments,8000,34000,expected_speech=True)}
        return row,expected,pcm

    def test_payload_and_endpoint_contract(self):
        row,expected,pcm=self.fixture()
        verify_row(row,expected,pcm,'identity',False)
        self.assertIsNone(row['endpoint_from_source_wav_end_audio_s'])

    def test_corrupt_payload_bounds_overlap_budget_and_drops_are_rejected(self):
        row,expected,pcm=self.fixture()
        for error in ('payload','bounds','overlap','budget','drop'):
            with self.subTest(error=error):
                r=copy.deepcopy(row)
                if error=='payload':r['segments'][0]['payload_sha256']='bad'
                elif error=='bounds':r['segments'][0]['owned_end_frame']=50000
                elif error=='overlap':r['segments'][1]['owned_start_frame']=47000
                elif error=='budget':r['pipeline']['native_budget_exhausted']=True
                else:r['replay']={'complete':True,'dropped_chunks':1}
                with self.assertRaises((ValueError,RuntimeError)):
                    verify_row(r,expected,pcm,'identity',error=='drop')


if __name__=='__main__':unittest.main()
