"""같은 1배속 WAV의 Zipformer/고정 구간 Refine 동시 부하를 측정한다."""

import argparse
import json
import os
import subprocess
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.adapters import create_adapter
from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.concurrent_probe import (
    RATE,
    WINDOW,
    ConcurrentReplay,
    ProcessSampler,
    RefineChannel,
    float_digest,
    limits_verified,
    prepare_track,
)
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import Audio
from edge_asr.online_vad import POLICIES, OnlineVadReplay, TimedProbability
from edge_asr.resources import RssSampler
from edge_asr.runtime import environment
from edge_asr.streaming import read_events, run_paced, warmup_stream

COMBINATIONS = ((1, 0), (2, 0), (1, 1), (1, 2), (2, 1))


def probe(
    root, manifest, output, zip_threads, refine_threads, *, refine_policy="fixed5", vad_model=None
):
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
    zpath, spath = root / "configs/zipformer_paced.toml", root / "configs/sensevoice_eval.toml"
    zconfig, sconfig = load_evaluation_config(zpath), load_evaluation_config(spath)
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
        "sensevoice_config": sconfig if refine_threads else None,
        "config_files_sha256": {"zipformer": digest(zpath), "sensevoice": digest(spath)},
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
    if refine_policy != "fixed5":
        info["settings"].update(
            refine_policy=refine_policy,
            vad=True,
            context_frames=3200 if refine_policy == "vad-continuation200" else 0,
            context_selection="continuation" if refine_policy == "vad-continuation200" else "all",
        )
        info["main_pid_components"] = ["zipformer", "online_vad", "coordinator_and_sampling"]
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
        info["warmup_input"] = {"frames": warm_frames, "float32_sha256": warm.sha256}
        if refine_threads:
            sense = create_adapter(sconfig["model"], RATE, root)
            info["sensevoice_model_files"] = {
                k: {"path": str(p), "sha256": digest(p)} for k, p in sense.model_files.items()
            }
            channel = RefineChannel(root, sconfig["model"], warm, output)
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
        ):
            arguments = (
                adapter,
                sources,
                telemetry,
                channel.submit if channel else None,
                zlog,
                jlog,
            )
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
        if adapter:
            adapter.close()
        info["resource_sampling_error"] = resources.error if resources else None
        info["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "worker.json", info)
        if (output / "track.json").exists():
            from edge_asr.tools.reports.concurrent import summarize

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
    args = parser.parse_args()
    return probe(
        Path.cwd(),
        args.manifest,
        args.output,
        args.zip_threads,
        args.refine_threads,
        refine_policy=args.refine_policy,
        vad_model=args.vad_model,
    )


if __name__ == "__main__":
    raise SystemExit(main())
