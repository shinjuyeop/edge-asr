"""선택 후보의 잘못된 자산·누락 preload를 로딩 전에 거부한다."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from edge_asr.artifacts import digest
from edge_asr.qwen.convolution import ConvolutionTile


class ConvolutionTests(unittest.TestCase):
    def test_asset_and_preload_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library, cpu, manifest = [root / name for name in ("tile.so", "cpu.so", "build.json")]
            library.write_bytes(b"tile")
            cpu.write_bytes(b"cpu")
            build = {
                "feature": "conv_tile4_1to5_v1",
                "upstream_commit": "pinned",
                "library": "tile.so",
                "cpu_library": "cpu.so",
                "library_sha256": digest(library),
                "original_cpu_sha256": digest(cpu),
            }
            manifest.write_text(json.dumps(build))
            env = {"LD_PRELOAD": str(library), "EDGE_ASR_CONV_CPU": str(cpu)}
            with patch.dict(os.environ, env):
                tile = ConvolutionTile("build.json", root, "pinned")
                self.assertEqual(tile.files["conv_library"], library)
                with self.assertRaisesRegex(ValueError, "커밋"):
                    ConvolutionTile("build.json", root, "wrong")
                library.write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "해시"):
                    ConvolutionTile("build.json", root, "pinned")
                library.write_bytes(b"tile")
            for preload, original in (
                ("", str(cpu)),
                (str(library), "wrong"),
                (f"{library}:extra", str(cpu)),
            ):
                with patch.dict(os.environ, {"LD_PRELOAD": preload, "EDGE_ASR_CONV_CPU": original}):
                    with self.assertRaisesRegex(ValueError, "LD_PRELOAD"):
                        ConvolutionTile("build.json", root, "pinned")

    def test_missing_loaded_library_and_invalid_trace_are_failures(self):
        tile = ConvolutionTile.__new__(ConvolutionTile)
        tile.path = Path("/absent/conv.so")
        tile.library = None
        with self.assertRaises(OSError):
            tile.bind()
        with self.assertRaises(RuntimeError):
            tile.begin()
        tile.library = Mock()
        tile.library.edge_conv_begin.return_value = -1
        with self.assertRaises(RuntimeError):
            tile.begin()
        for value in (
            None,
            b'{"mode":0,"groups":[]}',
            b'{"mode":2,"groups":[{"calls":1,"accepted":0}]}',
        ):
            tile.library.edge_conv_result.return_value = value
            with self.assertRaises(RuntimeError):
                tile.result()
