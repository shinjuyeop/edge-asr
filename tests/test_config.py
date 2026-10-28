"""호스트에서 모델·WAV 없이 실행하는 설정 경계 검사."""

from pathlib import Path
import tempfile
import unittest

from edge_asr.config import load_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "configs" / "zipformer_streaming.toml"


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
            ('[model]', '[missing_model]'),
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
