"""같은 1배속 WAV의 Zipformer/고정 구간 Refine 동시 부하를 측정한다."""

import argparse
import json
import os
import subprocess
import time
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.adapters import create_adapter
from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import Audio, float_digest, load_audio, prepare_track
from edge_asr.legacy.realtime.refine import RefineChannel
from edge_asr.legacy.realtime.replay import RATE, WINDOW, ConcurrentReplay
from edge_asr.legacy.realtime.vad import POLICIES, OnlineVadReplay, TimedProbability
from edge_asr.resources import ProcessSampler, RssSampler
from edge_asr.runtime import environment, limits_verified
from edge_asr.streaming import read_events, run_paced, warmup_stream

COMBINATIONS = ((1, 0), (2, 0), (1, 1), (1, 2), (2, 1))


def probe(
    root,
    manifest,
    output,
    zip_threads,
    refine_threads,
    *,
    refine_policy="fixed5",
    vad_model=None,
    segment_merge=False,
    token_diagnostics=False,
    caption_display=False,
    refine_config=None,
    warmup_wav=None,
):
    if caption_display and (
        segment_merge or token_diagnostics or refine_policy != "vad0" or not refine_threads
    ):
        raise ValueError("자막 표시는 연속 Zip+Refine vad0 별도 모드입니다.")
    if token_diagnostics and (segment_merge or refine_policy != "vad0" or not refine_threads):
        raise ValueError("연속 토큰 진단은 구간 reset 없이 Refine을 쓰는 vad0 별도 모드입니다.")
    if segment_merge and (refine_policy != "vad0" or not refine_threads):
        raise ValueError("구간 병합은 Refine을 사용하는 vad0 별도 모드입니다.")
    if (zip_threads, refine_threads) not in COMBINATIONS:
        raise ValueError("단독 1T/2T 또는 동시 1+1/1+2/2+1 조건이 필요합니다.")
    if refine_policy not in POLICIES or (refine_policy != "fixed5" and not refine_threads):
        raise ValueError("지원하는 Refine 정책과 VAD의 Refine 모델이 필요합니다.")
    if refine_policy != "fixed5":
        from edge_asr.vad import MODEL_SHA

        vad_model = vad_model or root / "models/silero-vad-k2-20261029/silero_vad.onnx"
        if digest(vad_model) != MODEL_SHA:
            raise ValueError("기존 검증한 VAD 가중치가 필요합니다.")
    if (output / "worker.json").exists():
        raise FileExistsError("기존 실행을 덮어쓰지 않습니다.")
    zpath = root / "configs/legacy/zipformer_paced.toml"
    spath = refine_config or root / "configs/legacy/sensevoice_eval.toml"
    zconfig, sconfig = load_evaluation_config(zpath), load_evaluation_config(spath)
    family = sconfig["model"]["family"]
    if family not in ("sensevoice", "qwen3_asr") or sconfig["evaluation"]["mode"] != "batch":
        raise ValueError("Refine은 SenseVoice/Qwen batch 설정이 필요합니다.")
    if refine_config is not None and not refine_threads:
        raise ValueError("별도 Refine 설정에는 Refine 스레드가 필요합니다.")
    if family == "qwen3_asr" and (segment_merge or token_diagnostics):
        raise ValueError("Qwen은 별도 후보 표시/동시 측정만 지원하며 병합 실험은 미검증입니다.")
    zconfig["model"]["num_threads"] = zip_threads
    sconfig["model"]["num_threads"] = refine_threads or 1
    output.mkdir(parents=True, exist_ok=True)
    info = {
        "kind": "concurrent_wav_probe",
        "status": "preparing",
        "process_id": os.getpid(),
        "process_group_id": os.getpgrp(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "zip_threads": zip_threads,
        "refine_threads": refine_threads,
        "zip_config": zconfig,
        ("sensevoice_config" if refine_config is None else "refine_config"): sconfig
        if refine_threads
        else None,
        "config_files_sha256": {"zipformer": digest(zpath), family: digest(spath)},
        "settings": {
            "capture": zconfig["streaming"],
            "refine_window_frames": WINDOW,
            "refine_queue_capacity": 2,
            "context_frames": 0,
            "vad": False,
            "deduplication": False,
            "source_gap_frames": 0,
        },
        "unmeasured": {
            key: None
            for key in (
                "hybrid_merged_cer",
                "acoustic_endpoint_latency_s",
                "aligned_recognition_latency_s",
                "ui_display_latency_s",
                "microphone_overruns",
            )
        },
    }
    if refine_config is not None:
        info["refine_family"] = family
    if refine_policy != "fixed5":
        info["settings"].update(
            refine_policy=refine_policy,
            vad=True,
            context_frames=3200 if refine_policy == "vad-continuation200" else 0,
            context_selection="continuation" if refine_policy == "vad-continuation200" else "all",
        )
        info["main_pid_components"] = ["zipformer", "online_vad", "coordinator_and_sampling"]
    if segment_merge:
        info["kind"] = "segmented_merge_wav_probe"
        info["settings"].update(
            segment_merge=True,
            zip_state_policy="reset_per_vad_owned_segment",
            zip_holdback_frames=4800,
            zip_chunk_frames=1600,
            merge_policies=["zip_only", "refine_complete", "agreement_only"],
        )
        info["main_pid_components"].append("segment_merger_and_result_reader")
        info["unmeasured"].pop("hybrid_merged_cer")
        info["unmeasured"]["deployed_hybrid_service_cer"] = None
    if token_diagnostics:
        info["kind"] = "continuous_token_diagnostic_probe"
        info["settings"].update(
            token_diagnostics=True,
            zip_state_policy="continuous_per_source",
            replacements_enabled=False,
            token_time_semantics="decoder_emission_point_not_acoustic_interval",
        )
        info["main_pid_components"].append("token_alignment_audit_and_result_reader")
    if caption_display:
        info["kind"] = "separate_caption_wav_mvp"
        info["settings"].update(
            caption_display=True,
            replacements_enabled=False,
            zip_state_policy="continuous_per_source",
        )
        info["main_pid_components"].append("caption_events_and_result_reader")
    write_json(output / "worker.json", info)
    adapter = channel = replay = resources = None
    telemetry = {}
    probability = timed_probability = vad = None
    code = 1
    try:
        limits = Path(f"/proc/{os.getpid()}/limits").read_text()
        info.update(limits_text=limits, main_limits_verified=limits_verified(limits))
        if not info["main_limits_verified"]:
            raise RuntimeError("thermal-run의 4GiB 주소 공간 제한이 필요합니다.")
        audio, sources, manifest_sha = prepare_track(root, manifest)
        write_json(
            output / "track.json",
            {
                "manifest_sha256": manifest_sha,
                "sample_rate_hz": RATE,
                "frames": len(audio.samples),
                "track_float32_sha256": audio.sha256,
                "sources": sources,
            },
        )
        info.update(
            manifest_sha256=manifest_sha,
            planned_sources=len(sources),
            audio_s=audio.duration_s,
            planned_refine_windows=sum(
                (s["end"] - s["start"] + WINDOW - 1) // WINDOW for s in sources
            ),
        )
        if refine_policy != "fixed5":
            info["planned_refine_windows"] = None
            info["planned_refine_reason"] = "online_vad_not_precomputed"
        warm_frames = min(WINDOW, sources[0]["end"])
        warm = Audio(audio.samples[:warm_frames], RATE, float_digest(audio.samples[:warm_frames]))
        if warmup_wav is not None:
            warm = load_audio(warmup_wav, RATE)
            if len(warm.samples) > WINDOW:
                raise ValueError("별도 워밍업 WAV는 최대5초입니다. 자동으로 자르지 않습니다.")
            info["warmup_source"] = {"path": str(warmup_wav), "audio_sha256": warm.sha256}
            warm_frames = len(warm.samples)
            warm = Audio(warm.samples, RATE, float_digest(warm.samples))
        info["warmup_input"] = {"frames": warm_frames, "float32_sha256": warm.sha256}
        if refine_threads:
            refine = create_adapter(sconfig["model"], RATE, root)
            info["sensevoice_model_files" if refine_config is None else "refine_model_files"] = {
                k: {"path": str(p), "sha256": digest(p)} for k, p in refine.model_files.items()
            }
            channel = RefineChannel(
                root,
                sconfig["model"],
                warm,
                output,
                return_results=segment_merge or token_diagnostics or caption_display,
            )
            ready = channel.start()
            actual = Path(f"/proc/{ready['process_id']}/limits").read_text()
            info.update(refine_ready=ready, refine_limits_verified=limits_verified(actual))
            if not info["refine_limits_verified"]:
                raise RuntimeError("Refine 자식의 실제 주소 공간 제한 미확인")
        adapter = create_adapter(zconfig["model"], RATE, root)
        info["zipformer_model_files"] = {
            k: {"path": str(p), "sha256": digest(p)} for k, p in adapter.model_files.items()
        }
        info["zipformer_metadata"] = deepcopy(adapter.metadata)
        began, cpu = time.perf_counter(), time.process_time()
        with RssSampler() as loading:
            adapter.load()
        info["zip_loading"] = {
            "wall_s": time.perf_counter() - began,
            "cpu_s": time.process_time() - cpu,
            "resources": loading.metadata(),
        }
        began = time.perf_counter()
        for _ in range(3):
            warmup_stream(adapter, warm, zconfig["streaming"])
        info.update(
            zip_warmup_runs=3,
            zip_warmup_s=time.perf_counter() - began,
            environment=environment(),
            status="running",
        )
        if refine_policy != "fixed5":
            from edge_asr.segmentation import SegmentPipeline
            from edge_asr.vad_probability import BoundedProbabilityVad, K2SileroProbability

            began, cpu = time.perf_counter(), time.process_time()
            with RssSampler() as loading:
                probability = K2SileroProbability(vad_model)
                timed_probability = TimedProbability(probability)
                vad = BoundedProbabilityVad(timed_probability)
            convert = lambda x: probability.np.asarray(x, dtype=probability.np.float32)
            info["vad_loading"] = {
                "wall_s": time.perf_counter() - began,
                "cpu_s": time.process_time() - cpu,
                "resources": loading.metadata(),
            }
            began = time.perf_counter()
            for _ in range(3):
                pipeline = SegmentPipeline(
                    vad,
                    "identity",
                    5,
                    lambda *a: None,
                    convert=convert,
                    context_frames=info["settings"]["context_frames"],
                    context_selection=info["settings"]["context_selection"],
                    native_budget_frames=None,
                )
                for offset in range(0, len(warm.samples), 1600):
                    pipeline.accept(
                        warm.samples[offset : offset + 1600],
                        final=offset + 1600 >= len(warm.samples),
                    )
                pipeline.finish()
            info.update(
                vad_model={"path": str(vad_model), "sha256": digest(vad_model)},
                vad_warmup_runs=3,
                vad_warmup_s=time.perf_counter() - began,
                vad_measurement_before=probability.metadata(),
                vad_cost_before={
                    "wall_s": timed_probability.wall_s,
                    "cpu_s": timed_probability.cpu_s,
                    "calls": timed_probability.calls,
                },
            )
            info["vad_settings"] = {
                "window_frames": 512,
                "num_threads": 1,
                "threshold": 0.5,
                "min_speech_frames": 1600,
                "min_silence_frames": 4800,
                "long_threshold": 0.9,
                "long_silence_frames": 1600,
                "soft_limit_s": 5,
                "retained_audio_frames": 0,
                "queue_capacity": 1,
            }
        write_json(output / "worker.json", info)

        def roles():
            return {
                "zip": os.getpid(),
                "refine": channel.process.pid if channel else None,
                "producer": telemetry.get("producer_pid"),
            }

        with (
            (output / "zip-events.jsonl").open("x") as zlog,
            (output / "refine-jobs.jsonl").open("x") as jlog,
            ExitStack() as stack,
        ):
            arguments = (
                adapter,
                sources,
                telemetry,
                channel.submit if channel else None,
                zlog,
                jlog,
            )
            if caption_display:
                from edge_asr.legacy.realtime.captions import CaptionReplay

                replay = CaptionReplay(
                    *arguments,
                    vad=vad,
                    policy=refine_policy,
                    convert=convert,
                    caption_log=stack.enter_context((output / "caption-events.jsonl").open("x")),
                )
                channel.listen(replay.receive)
            elif token_diagnostics:
                from edge_asr.legacy.token_alignment import TokenTraceReplay

                replay = TokenTraceReplay(
                    *arguments,
                    vad=vad,
                    policy=refine_policy,
                    convert=convert,
                    token_log=stack.enter_context((output / "zip-token-snapshots.jsonl").open("x")),
                    audit_log=stack.enter_context(
                        (output / "token-alignment-events.jsonl").open("x")
                    ),
                    receipt_log=stack.enter_context((output / "refine-receipts.jsonl").open("x")),
                )
                channel.listen(replay.receive)
                import sherpa_onnx

                info["token_api"] = {
                    "api": "OnlineRecognizer.recognizer.get_result",
                    "sherpa_git_sha1": getattr(sherpa_onnx, "git_sha1", None),
                }
            elif segment_merge:
                from edge_asr.legacy.segment_merge import SegmentMergeReplay

                replay = SegmentMergeReplay(
                    *arguments,
                    vad=vad,
                    policy=refine_policy,
                    convert=convert,
                    merge_log=stack.enter_context((output / "merge-events.jsonl").open("x")),
                    segment_log=stack.enter_context((output / "zip-segments.jsonl").open("x")),
                    receipt_log=stack.enter_context((output / "refine-receipts.jsonl").open("x")),
                )
                channel.listen(replay.receive)
                info["merge_session_id"] = replay.session_id
            else:
                replay = (
                    ConcurrentReplay(*arguments)
                    if vad is None
                    else OnlineVadReplay(*arguments, vad=vad, policy=refine_policy, convert=convert)
                )
            with ProcessSampler(roles, output) as resources:
                run_paced(
                    replay,
                    audio,
                    zconfig["streaming"],
                    output / "capture",
                    telemetry,
                    chunk_handler=replay.accept_at,
                )
                info["zip_finished_ns"] = time.perf_counter_ns()
                if channel:
                    info["refine_completion"] = channel.finish()
                if caption_display and not replay.display_complete():
                    raise RuntimeError("표시하지 못한 Refine 반환이 있습니다.")
                if segment_merge:
                    replay.drain_finished()
                info["measurement_finished_ns"] = time.perf_counter_ns()
        if resources.error:
            raise RuntimeError("process_resource_sampling_failed: " + resources.error)
        losses = sum(r["type"] == "drop" for r in read_events(output / "refine-jobs.jsonl"))
        info["status"] = "measurement_with_losses" if losses else "completed"
        code = 1 if losses else 0
    except BaseException as error:
        info.update(status="measurement_failed", error=f"{type(error).__name__}: {error}")
    finally:
        info.setdefault("measurement_finished_ns", time.perf_counter_ns())
        info["replay"] = telemetry
        if replay:
            write_jsonl(output / "zip-sources.jsonl", replay.source_rows)
            if vad is not None:
                write_jsonl(output / "vad-sources.jsonl", replay.vad_sources)
        if probability is not None:
            info["vad_measurement_after"] = probability.metadata()
            info["vad_runtime_after"] = vad.metadata() if vad is not None else None
            info["vad_inference"] = (
                {
                    key: getattr(timed_probability, key) - info["vad_cost_before"][key]
                    for key in ("wall_s", "cpu_s", "calls")
                }
                if "vad_cost_before" in info
                else None
            )
        if channel:
            channel.close()
            info.update(
                refine_exit_code=channel.process.exitcode, refine_alive=channel.process.is_alive()
            )
        if segment_merge and replay:
            replay.write_decisions(output)
        if adapter:
            adapter.close()
        info["resource_sampling_error"] = resources.error if resources else None
        info["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "worker.json", info)
        if (output / "track.json").exists():
            if token_diagnostics:
                from edge_asr.legacy.tools.reports.token_alignment import summarize
            elif segment_merge:
                from edge_asr.legacy.tools.reports.segment_merge import summarize
            else:
                from edge_asr.legacy.tools.reports.concurrent import summarize

            try:
                summarize(output, output / "summary.json")
            except Exception as error:
                info["summary_error"] = repr(error)
                write_json(output / "worker.json", info)
                code = 1
    print(
        json.dumps(
            {
                "output": str(output),
                "status": info["status"],
                "error": info.get("error"),
                "summary_error": info.get("summary_error"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip-threads", type=int, choices=(1, 2), required=True)
    parser.add_argument("--refine-threads", type=int, choices=(0, 1, 2), required=True)
    parser.add_argument("--refine-policy", choices=POLICIES, default="fixed5")
    parser.add_argument("--vad-model", type=Path)
    parser.add_argument(
        "--warmup-wav", type=Path, help="평가 입력과 분리한 최대5초 16kHz PCM16 WAV"
    )
    parser.add_argument(
        "--refine-config", type=Path, help="별도 Offline Refine 설정; 기본 SenseVoice"
    )
    parser.add_argument(
        "--caption-display",
        action="store_true",
        help="연속 Zip과 구간별 보정 후보를 분리 표시; 자동 교체 없음",
    )
    parser.add_argument(
        "--token-diagnostics",
        action="store_true",
        help="연속 Zip의 토큰/시각/VAD 구조만 진단; 텍스트 교체 없음",
    )
    parser.add_argument(
        "--segment-merge",
        action="store_true",
        help="vad0 구간마다 Zip 상태를 새로 만드는 별도 병합 실험",
    )
    args = parser.parse_args()
    return probe(
        Path.cwd(),
        args.manifest,
        args.output,
        args.zip_threads,
        args.refine_threads,
        refine_policy=args.refine_policy,
        vad_model=args.vad_model,
        segment_merge=args.segment_merge,
        token_diagnostics=args.token_diagnostics,
        caption_display=args.caption_display,
        refine_config=args.refine_config,
        warmup_wav=args.warmup_wav,
    )


if __name__ == "__main__":
    raise SystemExit(main())
