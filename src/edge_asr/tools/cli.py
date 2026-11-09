"""고정된 연구 도구의 단일 진입점. 선택한 도구만 지연 import한다."""

import argparse
import sys
from importlib import import_module

# 명시적인 명령 목록이며 외부 플러그인 검색/자동 등록은 하지 않는다.
COMMANDS = {
    "prepare-official": "data.official_sample",
    "prepare-kss": "data.kss",
    "prepare-external": "data.external",
    "prepare-validation": "data.validation",
    "prepare-volume": "edge_asr.legacy.tools.data.volume",
    "prepare-vad": "edge_asr.legacy.tools.data.vad_cases",
    "prepare-vad-overlap": "edge_asr.legacy.tools.data.vad_overlap",
    "prepare-segments": "edge_asr.legacy.tools.data.segmentation_cases",
    "prepare-public-review": "edge_asr.legacy.tools.data.public_review",
    "materialize-review": "edge_asr.legacy.tools.data.review_audio",
    "prepare-annotations": "edge_asr.legacy.tools.data.vad_annotations",
    "review-annotations": "edge_asr.legacy.tools.data.review_annotations",
    "compare": "edge_asr.legacy.tools.probes.comparison",
    "thermal-run": "probes.thermal",
    "probe-load": "probes.load",
    "probe-qwen3": "edge_asr.tools.optimization.qwen3",
    "compare-qwen3": "edge_asr.legacy.tools.probes.qwen3_comparison",
    "compare-qwen3-realtime": "edge_asr.legacy.tools.probes.qwen3_realtime",
    "validate-files": "edge_asr.legacy.tools.probes.file_validation",
    "probe-preprocessing": "edge_asr.legacy.tools.probes.preprocessing",
    "probe-vad": "edge_asr.legacy.tools.probes.vad",
    "probe-segments": "edge_asr.legacy.tools.probes.segments",
    "probe-segmented-accuracy": "edge_asr.legacy.tools.probes.segmented_accuracy",
    "probe-concurrent": "edge_asr.legacy.tools.probes.concurrent",
    "probe-annotated-vad": "edge_asr.legacy.tools.probes.annotated_vad",
    "probe-vad-duration": "edge_asr.legacy.tools.probes.vad_duration",
    "summarize-comparison": "edge_asr.legacy.tools.reports.comparison",
    "summarize-preflight": "edge_asr.legacy.tools.reports.preflight",
    "summarize-vad": "edge_asr.legacy.tools.reports.vad",
    "summarize-segments": "edge_asr.legacy.tools.reports.segments",
    "summarize-segmented-accuracy": "edge_asr.legacy.tools.reports.segmented_accuracy",
    "summarize-concurrent": "edge_asr.legacy.tools.reports.concurrent",
    "replay-merge": "edge_asr.legacy.tools.reports.merge_replay",
    "replay-splice": "edge_asr.legacy.tools.reports.splice_replay",
    "summarize-segment-merge": "edge_asr.legacy.tools.reports.segment_merge",
    "summarize-token-alignment": "edge_asr.legacy.tools.reports.token_alignment",
    "summarize-annotated-vad": "edge_asr.legacy.tools.reports.annotated_vad",
    "inspect-cooling": "reports.cooling",
    "consolidate": "maintenance.consolidate",
    "repeat-zipformer": "edge_asr.benchmark",
}


LEGACY_COMMANDS = {
    "prepare_korean_test0": "prepare-official",
    "prepare_kss_subset": "prepare-kss",
    "prepare_m23_data": "prepare-external",
    "prepare_m30_audio": "prepare-volume",
    "prepare_vad_cases": "prepare-vad",
    "prepare_segmentation_cases": "prepare-segments",
    "prepare_public_vad_review": "prepare-public-review",
    "materialize_public_vad_review": "materialize-review",
    "prepare_vad_annotations": "prepare-annotations",
    "review_vad_annotations": "review-annotations",
    "compare_models": "compare",
    "m30_thermal_run": "thermal-run",
    "m30_load_probe": "probe-load",
    "m30_preprocessing_probe": "probe-preprocessing",
    "m30_vad_probe": "probe-vad",
    "m30_segment_probe": "probe-segments",
    "m30_annotated_vad_probe": "probe-annotated-vad",
    "summarize_comparison": "summarize-comparison",
    "summarize_m30": "summarize-preflight",
    "summarize_vad_probe": "summarize-vad",
    "summarize_segment_probe": "summarize-segments",
    "summarize_annotated_vad": "summarize-annotated-vad",
    "consolidate_rp5": "consolidate",
}


def module_name(command):
    command = LEGACY_COMMANDS.get(command, command)
    module = COMMANDS[command]
    return module if module.startswith("edge_asr.") else "edge_asr.tools." + module


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        argv[0] = LEGACY_COMMANDS.get(argv[0], argv[0])
    parser = argparse.ArgumentParser(
        prog="edge-asr tools", description="데이터 준비·측정·집계·유지보수 도구"
    )
    parser.add_argument("tool", choices=["list", *COMMANDS], nargs="?", default="list")
    parser.add_argument(
        "arguments", nargs=argparse.REMAINDER, help="선택한 도구의 인자. <도구> --help로 확인"
    )
    args = parser.parse_args(argv)
    if args.tool == "list":
        for command, module in COMMANDS.items():
            print(f"{command:25} {module}")
        return 0
    previous = sys.argv
    try:
        sys.argv = [f"edge-asr tools {args.tool}", *args.arguments]
        return import_module(module_name(args.tool)).main() or 0
    finally:
        sys.argv = previous
