"""고정 구간 정책의 Offline ASR 파일 평가. 동일 원음 전체 정답으로 채점한다."""

import argparse
import hashlib
import os
import subprocess
import time
from array import array
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.adapters import create_adapter
from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.audio_policy import preprocess
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import Audio, float_digest, load_audio, load_manifest, pcm_bytes
from edge_asr.resources import RssSampler
from edge_asr.runtime import environment
from edge_asr.segmentation import SegmentPipeline
from edge_asr.vad import MODEL_SHA

CONDITIONS = (
    "full_batch",
    "fixed_windows_5s",
    "bounded_vad_5s",
    "bounded_vad_5s_context200ms",
    "bounded_vad_5s_onset200ms",
    "bounded_vad_5s_continuation200ms",
)
VAD_CONDITIONS = CONDITIONS[2:]
RATE, MAXIMUM = 16000, 80000


def context_settings(condition):
    if condition not in CONDITIONS:
        raise ValueError("지원하는 구간 평가 조건이 필요합니다.")
    frames = 3200 if condition.endswith("200ms") else 0
    selection = (
        "onset"
        if condition.endswith("onset200ms")
        else ("continuation" if condition.endswith("continuation200ms") else "all")
    )
    return frames, selection


class MeasuredProbability:
    """이 검사에서만 VAD 신경망 호출 비용을 wrapper/ASR 비용과 분리한다."""

    def __init__(self, probability):
        self.probability = probability
        self.wall_s = self.cpu_s = 0.0

    def reset(self):
        self.probability.reset()

    def __call__(self, samples):
        began, cpu = time.perf_counter(), time.process_time()
        try:
            return self.probability(samples)
        finally:
            self.wall_s += time.perf_counter() - began
            self.cpu_s += time.process_time() - cpu


def measure(audio, condition, adapter, vad=None, *, convert=list):
    if condition not in CONDITIONS or (condition in VAD_CONDITIONS and vad is None):
        raise ValueError("고정한 조건과 해당 VAD가 필요합니다.")
    if not isinstance(audio, Audio) or audio.sample_rate_hz != RATE or not audio.samples:
        raise ValueError("비어 있지 않은 16kHz Audio가 필요합니다.")
    # 전체 원음의 검증용 PCM/해시는 계측 밖에서 준비한다. 실제 입력 처리는 아래에서 한다.
    expected, _ = preprocess(audio.samples, "identity")
    expected_sha = hashlib.sha256(pcm_bytes(expected)).hexdigest()
    context_frames, selection = context_settings(condition)
    segments = []
    result = {
        "condition": condition,
        "input_pcm_sha256": expected_sha,
        "input_frames": len(expected),
        "context_frames": context_frames,
        "maximum_owned_frames": None if condition == "full_batch" else MAXIMUM,
        "asr_wall_s": 0.0,
        "asr_cpu_s": 0.0,
        "segments": segments,
        "vad_inference_s": None,
        "vad_inference_cpu_s": None,
    }
    if selection != "all":
        result["context_selection"] = selection
    probability = getattr(vad, "probability", None)
    vad_before = (probability.wall_s, probability.cpu_s) if probability else None
    reset_before = getattr(getattr(probability, "probability", None), "reset_count", None)
    pipeline = None

    def emit(event, pcm):
        start, end = event["owned_start_frame"], event["owned_end_frame"]
        applied = context_frames if selection == "all" else event["applied_context_frames"]
        if (
            not 0 <= start < end <= len(expected)
            or (segments and start < segments[-1]["owned_end_frame"])
            or event["context_start_frame"] != max(0, start - applied)
            or event["context_end_frame"] != end
            or (condition != "full_batch" and end - start > MAXIMUM)
            or pcm != expected[event["context_start_frame"] : end]
        ):
            raise ValueError("구간 경계/중복/문맥/PCM 검증 실패")
        sha = hashlib.sha256(pcm_bytes(pcm)).hexdigest()
        if event.get("payload_sha256", sha) != sha:
            raise ValueError("구간 payload 해시 불일치")
        row = {
            **event,
            "segment_id": len(segments),
            "payload_sha256": sha,
            "payload_frames": len(pcm),
            "status": "failed",
            "hypothesis": None,
            "adapter_result": None,
        }
        segments.append(row)
        payload = Audio(array("f", (x / 32768 for x in pcm)), RATE, sha)
        began, cpu = time.perf_counter(), time.process_time()
        try:
            text = adapter.transcribe(payload)
            if not isinstance(text, str):
                raise TypeError("ASR 전사는 문자열이어야 합니다.")
            row.update(
                status="success",
                hypothesis=text,
                adapter_result=deepcopy(getattr(adapter, "last_result_metadata", None)),
            )
        except Exception as error:
            row["adapter_result"] = deepcopy(getattr(adapter, "last_result_metadata", None))
            row["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            row["inference_s"] = time.perf_counter() - began
            row["process_cpu_s"] = time.process_time() - cpu
            result["asr_wall_s"] += row["inference_s"]
            result["asr_cpu_s"] += row["process_cpu_s"]

    began, cpu = time.perf_counter(), time.process_time()
    result["started_monotonic_s"] = time.monotonic()
    result.update(status="failed", hypothesis=None, error=None)
    try:
        if condition in VAD_CONDITIONS:
            pipeline = SegmentPipeline(
                vad,
                "identity",
                5,
                emit,
                convert=convert,
                context_frames=context_frames,
                context_selection=selection,
                native_budget_frames=None,
            )
            for offset in range(0, len(audio.samples), 1600):
                pipeline.accept(
                    audio.samples[offset : offset + 1600],
                    final=offset + 1600 >= len(audio.samples),
                )
            pipeline.finish()
            if pipeline.metadata()["processed_pcm_sha256"] != expected_sha:
                raise ValueError("공통 identity PCM 해시 불일치")
        else:
            pcm, _ = preprocess(audio.samples, "identity")
            size = len(pcm) if condition == "full_batch" else MAXIMUM
            for start in range(0, len(pcm), size):
                end = min(start + size, len(pcm))
                emit(
                    {
                        "reason": condition,
                        "owned_start_frame": start,
                        "owned_end_frame": end,
                        "context_start_frame": start,
                        "context_end_frame": end,
                    },
                    pcm[start:end],
                )
        result.update(status="success", hypothesis=" ".join(r["hypothesis"] for r in segments))
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        result.update(
            pipeline_wall_s=time.perf_counter() - began,
            pipeline_cpu_s=time.process_time() - cpu,
            ended_monotonic_s=time.monotonic(),
            selected_frames=sum(r["owned_end_frame"] - r["owned_start_frame"] for r in segments),
            payload_frames_total=sum(r["payload_frames"] for r in segments),
            pipeline=pipeline.metadata() if pipeline else None,
        )
        if vad_before:
            result["vad_inference_s"] = probability.wall_s - vad_before[0]
            result["vad_inference_cpu_s"] = probability.cpu_s - vad_before[1]
        if reset_before is not None:
            result["vad_resets_during_utterance"] = (
                probability.probability.reset_count - reset_before
            )
    return result


def probe(
    root,
    config_path,
    manifest,
    output,
    condition,
    vad_model,
    *,
    warmup_first_window=False,
    warmup_wav=None,
):
    if warmup_first_window and warmup_wav is not None:
        raise ValueError("첫 원본 워밍업과 별도 WAV 워밍업은 동시에 선택할 수 없습니다.")
    config = load_evaluation_config(config_path)
    model, evaluation = config["model"], config["evaluation"]
    if (
        condition not in CONDITIONS
        or model["family"] not in ("sensevoice", "qwen3_asr")
        or model["num_threads"] not in (1, 2)
        or (model["family"] == "sensevoice" and model.get("use_itn") is not False)
        or (
            model.get("language"),
            config["audio"]["sample_rate_hz"],
            evaluation["mode"],
            evaluation["warmup_runs"],
            evaluation["normalization"],
        )
        != ("ko", RATE, "batch", 3, "ko_cer_v1")
    ):
        raise ValueError(
            "SenseVoice(ITN false)/Qwen 1T·2T/ko/16k/batch/워밍업 3회/ko_cer_v1 조건입니다."
        )
    if condition in VAD_CONDITIONS and digest(vad_model) != MODEL_SHA:
        raise ValueError("기존 검증한 Silero 가중치가 필요합니다.")
    samples, manifest_sha = load_manifest(manifest, root)
    if any(s.audio_sha256 is None for s in samples):
        raise ValueError("모든 원본 WAV의 SHA256이 필요합니다.")
    if (output / "worker.json").exists() or (output / "predictions.jsonl").exists():
        raise FileExistsError("기존 결과를 덮어쓰지 않습니다.")
    output.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "schema_version": 1,
            "mode": "batch",
            "id": s.id,
            "reference": s.reference,
            "audio_path": str(s.audio_path),
            "split": s.split,
            "condition": s.condition,
            "expected_audio_sha256": s.audio_sha256,
            "status": "not_attempted",
            "hypothesis": None,
            "audio": None,
            "elapsed_s": None,
            "process_cpu_s": None,
            "resources": None,
            "error": None,
            "segmentation_condition": condition,
            "segmented": None,
        }
        for s in samples
    ]
    info = {
        "kind": "segmented_accuracy",
        "status": "preparing",
        "process_id": os.getpid(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "condition": condition,
        "config": config,
        "config_sha256": digest(config_path),
        "manifest_sha256": manifest_sha,
        "planned": len(samples),
        "model_files": {},
        "warmup": {"asr_successful": 0, "vad_successful": 0},
        "settings": {
            "preprocessing": "identity",
            "context_frames": context_settings(condition)[0],
            "maximum_s": 5,
            "prefix_frames": 0,
            "suffix_frames": 0,
            "join": "single_space_in_segment_order",
            "deduplication": False,
            "fallback": False,
            "text_filter": False,
        },
        "timing_scope": "elapsed_s는 파일 읽기/검증용 참조 PCM/로딩/워밍업/결과 기록을 제외한 전처리+구간화+ASR+본문 연결+계측/구간 검증 비용. 별도 asr_wall_s와 VAD 신경망 비용은 부분집합.",
        "unmeasured": {
            k: None
            for k in (
                "streaming_first_text_s",
                "acoustic_endpoint_latency_s",
                "refine_delay_s",
                "drops",
                "backlog",
            )
        },
    }
    if context_settings(condition)[1] != "all":
        info["settings"]["context_selection"] = context_settings(condition)[1]
    write_json(output / "worker.json", info)
    write_jsonl(output / "predictions.jsonl", rows)
    adapter = None
    code = 0
    try:
        audios = [load_audio(s.audio_path, RATE) for s in samples]
        for s, audio, row in zip(samples, audios, rows):
            if audio.sha256 != s.audio_sha256:
                raise ValueError("원본 WAV 해시 불일치")
            row["audio"] = audio.metadata()
        adapter = create_adapter(model, RATE, root)
        info["adapter_metadata"] = deepcopy(adapter.metadata)
        info["model_files"] = {
            k: {"path": str(p), "sha256": digest(p)} for k, p in adapter.model_files.items()
        }
        began, cpu = time.perf_counter(), time.process_time()
        with RssSampler() as loading:
            adapter.load()
        info["asr_loading"] = {
            "wall_s": time.perf_counter() - began,
            "cpu_s": time.process_time() - cpu,
            "resources": loading.metadata(),
        }
        vad, probability, convert = None, None, list
        if condition in VAD_CONDITIONS:
            from edge_asr.vad_probability import BoundedProbabilityVad, K2SileroProbability

            began, cpu = time.perf_counter(), time.process_time()
            with RssSampler() as loading:
                probability = K2SileroProbability(vad_model)
                vad = BoundedProbabilityVad(MeasuredProbability(probability))
            convert = lambda x: probability.np.asarray(x, dtype=probability.np.float32)
            info["vad_loading"] = {
                "wall_s": time.perf_counter() - began,
                "cpu_s": time.process_time() - cpu,
                "resources": loading.metadata(),
            }
            info["vad_model"] = {"path": str(vad_model), "sha256": MODEL_SHA}
            info["probability_runtime"] = probability.metadata()
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
        info["environment"] = environment()
        warm = audios[0]
        if warmup_wav is not None:
            warm = load_audio(warmup_wav, RATE)
            if len(warm.samples) > MAXIMUM:
                raise ValueError("별도 워밍업 WAV는 최대5초입니다. 자동으로 자르지 않습니다.")
            info["warmup_source"] = {"path": str(warmup_wav), "audio_sha256": warm.sha256}
            info["warmup_input"] = {
                "frames": len(warm.samples),
                "float32_sha256": float_digest(warm.samples),
            }
        if warmup_first_window:
            samples = warm.samples[:MAXIMUM]
            warm = Audio(samples, RATE, float_digest(samples))
            info["warmup_input"] = {"frames": len(samples), "float32_sha256": warm.sha256}
        warm_began, warm_cpu = time.perf_counter(), time.process_time()
        for _ in range(3):
            if not isinstance(adapter.transcribe(warm), str):
                raise TypeError("워밍업 전사는 문자열이어야 합니다.")
            info["warmup"]["asr_successful"] += 1
        info["warmup"].update(
            asr_wall_s=time.perf_counter() - warm_began, asr_cpu_s=time.process_time() - warm_cpu
        )
        if vad:
            warm_began, warm_cpu = time.perf_counter(), time.process_time()
            # 같은 첫 발화의 VAD만 워밍업. ASR 호출을 추가하지 않는다.
            for _ in range(3):
                pipeline = SegmentPipeline(
                    vad,
                    "identity",
                    5,
                    lambda *a: None,
                    convert=convert,
                    context_frames=info["settings"]["context_frames"],
                    context_selection=info["settings"].get("context_selection", "all"),
                    native_budget_frames=None,
                )
                for offset in range(0, len(warm.samples), 1600):
                    pipeline.accept(
                        warm.samples[offset : offset + 1600],
                        final=offset + 1600 >= len(warm.samples),
                    )
                pipeline.finish()
                info["warmup"]["vad_successful"] += 1
            info["warmup"].update(
                vad_wall_s=time.perf_counter() - warm_began,
                vad_cpu_s=time.process_time() - warm_cpu,
            )
        info["status"] = "running"
        write_json(output / "worker.json", info)
        for audio, row in zip(audios, rows):
            with RssSampler() as usage:
                result = measure(audio, condition, adapter, vad, convert=convert)
            row.update(
                segmented=result,
                status=result["status"],
                hypothesis=result["hypothesis"],
                elapsed_s=result["pipeline_wall_s"],
                process_cpu_s=result["pipeline_cpu_s"],
                resources=usage.metadata(),
                error=result["error"],
            )
            if row["status"] != "success":
                code = 1
            write_jsonl(output / "predictions.jsonl", rows)
        info["status"] = "completed" if code == 0 else "completed_with_failures"
        if probability:
            info["probability_runtime_after"] = probability.metadata()
    except BaseException as error:
        code = 1
        info.update(status="failed", error=f"{type(error).__name__}: {error}")
        if isinstance(error, KeyboardInterrupt):
            code = 130
    finally:
        if adapter is not None:
            try:
                adapter.close()
            except Exception as error:
                code = 1
                info.update(status="cleanup_failed", cleanup_error=str(error))
        info["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_jsonl(output / "predictions.jsonl", rows)
        write_json(output / "worker.json", info)
        from edge_asr.legacy.tools.reports.segmented_accuracy import summarize

        summarize(output / "predictions.jsonl", output)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/legacy/sensevoice_eval.toml"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--condition", choices=CONDITIONS, required=True)
    parser.add_argument(
        "--warmup-wav", type=Path, help="평가 입력과 분리한 최대5초 16kHz PCM16 WAV"
    )
    parser.add_argument(
        "--warmup-first-window", action="store_true", help="첫 원본 앞 최대5초로 ASR/VAD 워밍업"
    )
    parser.add_argument(
        "--vad-model", type=Path, default=Path("models/silero-vad-k2-20261029/silero_vad.onnx")
    )
    args = parser.parse_args()
    return probe(
        Path.cwd(),
        args.config,
        args.manifest,
        args.output,
        args.condition,
        args.vad_model,
        warmup_first_window=args.warmup_first_window,
        warmup_wav=args.warmup_wav,
    )


if __name__ == "__main__":
    raise SystemExit(main())
