"""호스트에서 모델·WAV 없이 실행하는 설정 경계 검사."""

import tempfile
import unittest
from pathlib import Path

from edge_asr.config import load_config, load_evaluation_config

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "configs/legacy" / "zipformer_streaming.toml"


class ConfigTests(unittest.TestCase):
    def test_example_without_model_assets(self):
        config = load_config(EXAMPLE)
        self.assertEqual(config["model"]["num_threads"], 2)

    def test_invalid_values(self):
        source = EXAMPLE.read_text(encoding="utf-8")
        for old, new in (
            ('family = "zipformer"', 'family = "unknown"'),
            ("num_threads = 2", "num_threads = 0"),
            ("num_threads = 2", "num_threads = true"),
            ("sample_rate_hz = 16000", "sample_rate_hz = -1"),
            ("chunk_ms = 100", "chunk_ms = 0"),
            ("warmup_runs = 3", "warmup_runs = -1"),
            ("repetitions = 30", "repetitions = 0"),
            ('mode = "paced_stream"', 'mode = "live"'),
            ("[model]", "[missing_model]"),
        ):
            with self.subTest(new=new), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "invalid.toml"
                path.write_text(source.replace(old, new), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                load_config(Path(temp) / "missing.toml")

    def test_zipformer_decoding_requires_a_valid_active_paths_setting(self):
        model = (ROOT / "configs/zipformer/b0.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "zipformer.toml"
            for options in (
                'decoding_method = "beam_search"',
                'decoding_method = "modified_beam_search"',
                'decoding_method = "modified_beam_search"\nmax_active_paths = 0',
                'decoding_method = "modified_beam_search"\nmax_active_paths = true',
                'decoding_method = "greedy_search"\nmax_active_paths = 4',
            ):
                with self.subTest(options=options):
                    path.write_text(model.replace('decoding_method = "greedy_search"', options))
                    with self.assertRaises(ValueError):
                        load_evaluation_config(path)
        for arm, paths in (("b1-a", 2), ("b1-b", 4), ("b1-c", 8)):
            config = load_evaluation_config(ROOT / f"configs/zipformer/{arm}.toml")
            self.assertEqual(config["model"]["max_active_paths"], paths)
