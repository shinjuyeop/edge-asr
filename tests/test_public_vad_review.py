"""합성 TextGrid/WAV로 미검수 자료가 정답으로 승격되지 않는지 검사한다."""
from array import array
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import wave
import zipfile

from edge_asr.dataset import load_manifest
from edge_asr.textgrid import read_textgrid

SPEC = importlib.util.spec_from_file_location("public_vad_review", Path(__file__).resolve().parents[1] / "scripts/prepare_public_vad_review.py")
helper = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(helper)


def grid_blob(intervals=None, names=("phoneme", "utt.ortho.", "duplicate", "duplicate")):
    intervals = intervals or [(0, .2, "네"), (.2, .5, "<IVER>"), (.5, .7, "응"), (.7, .9, "네"), (.9, 10, "긴 문장")]
    lines = ['File type = "ooTextFile"', 'Object class = "TextGrid"', 'xmin = 0', 'xmax = 10',
             'tiers? <exists>', f'size = {len(names)}', 'item []:']
    for i, name in enumerate(names, 1):
        lines += [f'item [{i}]:', 'class = "IntervalTier"', f'name = "{name}"', 'xmin = 0', 'xmax = 10', f'intervals: size = {len(intervals)}']
        for j, (start, end, text) in enumerate(intervals, 1):
            lines += [f'intervals [{j}]:', f'xmin = {start}', f'xmax = {end}', 'text = "'+text.replace('"','""')+'"']
    return ('\n'.join(lines)+'\n').encode()


class TextGridTests(unittest.TestCase):
    def test_unicode_quotes_multiline_and_duplicate_names(self):
        text = '그는 "네"라고\n말했다'
        blob = grid_blob([(0, 10, text)])
        for raw in (blob, blob.decode().encode('utf-16'), b'\xef\xbb\xbf'+blob):
            grid = read_textgrid(raw)
            self.assertEqual(grid.tiers[0].intervals[0].text, text)
            self.assertEqual([t.name for t in grid.tiers][-2:], ['duplicate','duplicate'])

    def test_rejects_broken_or_unsupported_annotations(self):
        good = grid_blob()
        bad = [good[:-20], good+b'ignored', good.replace(b'IntervalTier',b'TextTier',1),
               good.replace(b'xmax = 0.2',b'xmax = 0.1',1),
               good.replace(b'xmax = 0.2',b'xmax = 0',1),
               good.replace(b'xmax = 10',b'xmax = 1e999',1),
               good.replace(b'intervals: size = 5',b'intervals: size = 6',1)]
        for blob in bad:
            with self.subTest(blob=blob[:60]), self.assertRaises(ValueError):read_textgrid(blob)


class PublicReviewTests(unittest.TestCase):
    def sources(self, root):
        seoul, noise = root/'data/seoul', root/'data/noise'
        seoul.mkdir(parents=True); noise.mkdir()
        with zipfile.ZipFile(seoul/'label-TextGrid.zip','w') as z:
            z.writestr('s12m21f1.TextGrid',grid_blob())
            z.writestr('s11m21f1.TextGrid',grid_blob())
            z.writestr('s13m21f1.TextGrid',b'broken')
        wav = noise/'noise.wav'
        with wave.open(str(wav),'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000)
            out.writeframes(array('h',[12]*1600).tobytes())
        for directory in (seoul,noise):
            record = {'files':[{'path':p.name,'sha256':helper.digest(p)} for p in directory.iterdir()],
                      'license':'CC BY-NC 2.0','resource_license_label':'Apache 2.0'}
            (directory/'acquisition.json').write_text(json.dumps(record))
        return seoul,noise

    def test_deterministic_selection_null_labels_and_source_preservation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); seoul,noise=self.sources(root); output=root/'data/review'
            before={p:helper.digest(p) for p in root.rglob('*') if p.is_file()}
            result=helper.prepare(root,seoul,noise,output)
            self.assertEqual(result['candidate_counts'],{'short':4,'long':2,'noise_candidate':1})
            self.assertEqual(result['candidate_speakers'],2)
            self.assertEqual(result['quarantined'],['s13m21f1.TextGrid'])
            rows=[json.loads(l) for l in (output/'review-candidates.jsonl').read_text().splitlines()]
            self.assertTrue(rows[0]['id'].startswith('seoul-s11'))
            self.assertEqual([r['suggested_transcript'] for r in rows[:3]],['네','응','긴 문장'])
            self.assertTrue(all(r['reference'] is None and r['expected_speech'] is None for r in rows))
            self.assertTrue(all(r['reference_speech_intervals'] is None for r in rows))
            with self.assertRaises(ValueError):load_manifest(output/'review-candidates.jsonl',root)
            self.assertEqual(before,{p:helper.digest(p) for p in before})
            for p,sha in result['files'].items():self.assertEqual(helper.digest(output/p),sha)
            with self.assertRaises(FileExistsError):helper.prepare(root,seoul,noise,output)

    def test_source_corruption_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);seoul,noise=self.sources(root);output=root/'data/review'
            with (noise/'noise.wav').open('ab') as f:f.write(b'changed')
            with self.assertRaisesRegex(ValueError,'해시'):helper.prepare(root,seoul,noise,output)
            self.assertFalse(output.exists())

    def test_invalid_zip_paths_and_duplicate_members(self):
        with tempfile.TemporaryDirectory() as d:
            archive=Path(d)/'label.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('../s11m21f1.TextGrid',grid_blob())
            with self.assertRaises(ValueError):helper.seoul_candidates(archive)
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('a/s11m21f1.TextGrid',grid_blob())
                z.writestr('b/s11m21f1.TextGrid',grid_blob())
            # 같은 basename은 음원 연결/후보 ID가 충돌하므로 거부해야 한다.
            with self.assertRaises(ValueError):helper.seoul_candidates(archive)


if __name__ == '__main__':unittest.main()
