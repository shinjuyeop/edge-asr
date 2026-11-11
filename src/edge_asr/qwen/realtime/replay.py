"""Qwen 단독 WAV 1배속·VAD·누적 부분 전사 검사. 실제 마이크나 청크 간 KV 재사용은 아니다."""

import argparse
import os
import subprocess
from copy import deepcopy
from pathlib import Path
from threading import Lock

from edge_asr.adapters import create_adapter
from edge_asr.artifacts import digest, write_event, write_json
from edge_asr.audio_policy import VAD_POLICIES
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio, load_manifest, prepare_track
from edge_asr.metrics import character_errors, distribution
from edge_asr.qwen.realtime.session import CaptionSession
from edge_asr.qwen.realtime.stability import summarize as summarize_stability
from edge_asr.resources import measure_process
from edge_asr.runtime import environment, limits_verified
from edge_asr.streaming import read_events, run_paced
from edge_asr.vad_probability import BoundedProbabilityVad, K2SileroProbability


def run(
    root,
    config_path,
    manifest,
    output,
    vad_model,
    *,
    adapter_factory=None,
    vad_factory=None,
    **settings,
):
    config = load_evaluation_config(config_path)
    if config["model"]["family"] not in ("qwen3_asr", "qwen3_gguf"):
        raise ValueError("Qwen 단독 연속 경로는 ONNX 또는 GGUF 설정이 필요합니다.")
    limits = Path("/proc/self/limits").read_text()
    if not limits_verified(limits):
        raise RuntimeError("thermal-run의 4GiB 주소 공간 제한이 필요합니다.")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("worker.json", "jobs.jsonl", "stream"):
        if (output / name).exists():
            raise FileExistsError(f"기존 실행을 덮어쓰지 않습니다: {output / name}")
    info = {
        "schema_version": 1,
        "kind": "qwen3_paced_partial_replay",
        "status": "preparing",
        "process_id": os.getpid(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip()
        ),
        "config": config,
        "config_sha256": digest(config_path),
        "settings": settings,
        "limits": limits,
        "environment": environment(),
        "telemetry": {},
        "warmup": [],
        "engine_ttft_s": None,
        "acoustic_word_latency_s": None,
        "scope": "1배속 입력과 추론 동시 진행. 접두 PCM 재전사·호출별 KV 초기화. 선택적 GGUF 토큰 배포는 임시 가설이며 encoder 증분 실행·마이크·음향 단어 시각은 미구현.",
    }
    receipt = output / "worker.json"
    write_json(receipt, info)
    adapter = session = log = None
    try:
        audio, sources, manifest_sha = prepare_track(root, manifest, gap_s=settings.get("gap_s", 1))
        info.update(sources=sources, input_manifest_sha256=manifest_sha, audio=audio.metadata())
        samples, _ = load_manifest(manifest, root)
        warmup = load_audio(samples[0].audio_path, 16000)
        vad = (
            vad_factory
            or (
                lambda p: BoundedProbabilityVad(
                    K2SileroProbability(p),
                    threshold=settings.get("vad_threshold", 0.5),
                    max_speech_s=settings.get("vad_max_speech_s", 5),
                )
            )
        )(vad_model)
        info["vad_model_sha256"] = digest(vad_model)
        adapter = (adapter_factory or create_adapter)(config["model"], 16000, root)
        if settings.get("token_stream", False) and (
            getattr(adapter, "supports_token_stream", False) is not True
        ):
            raise ValueError("--token-stream에는 token_callback_v1 별도 GGUF 빌드가 필요합니다.")
        info["model_files"] = {
            name: {"path": str(p), "sha256": digest(p)} for name, p in adapter.model_files.items()
        }
        info["model_metadata"] = deepcopy(adapter.metadata)
        info["model_load"] = {}
        with measure_process(info["model_load"], sample_device=True):
            adapter.load()
        for phase in ["first_inference", *["warmup"] * config["evaluation"]["warmup_runs"]]:
            row = {"phase": phase, "status": "failed"}
            info["warmup"].append(row)
            with measure_process(row):
                text = adapter.transcribe(warmup)
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("첫 추론·워밍업 출력 오류")
                row.update(status="success", hypothesis=text)
        info["status"] = "replaying"
        write_json(receipt, info)
        lock = Lock()
        log = (output / "jobs.jsonl").open("x", encoding="utf-8")

        def emit(event):
            with lock:
                write_event(log, event)
                if event["type"] in ("token", "result") and event["displayed"]:
                    label = "생성중 " if event["type"] == "token" else ""
                    print(
                        f"{event['published_s']:.3f}s {label}{event['kind']} {event['segment_id']} {event.get('hypothesis') or event.get('error')}",
                        flush=True,
                    )

        session_settings = {
            k: v
            for k, v in settings.items()
            if k not in ("gap_s", "vad_threshold", "vad_max_speech_s")
        }
        session = CaptionSession(
            adapter, vad, len(audio.samples), emit, info["telemetry"], **session_settings
        )
        paced = {
            "chunk_ms": 100,
            "queue_capacity_chunks": 10,
            "drain_timeout_s": settings.get("drain_timeout_s", 30),
        }
        info["replay_resources"] = {}
        with measure_process(info["replay_resources"], sample_device=True):
            hypothesis = run_paced(
                session,
                audio,
                paced,
                output / "stream",
                info["telemetry"],
                chunk_handler=session.accept_at,
            )
        info.update(
            hypothesis=hypothesis,
            pipeline=session.pipeline.metadata(),
            vad=vad.metadata(),
            vad_probability=vad.probability.metadata()
            if hasattr(vad.probability, "metadata")
            else None,
            segments=session.segments,
            results=session.results,
            inference_queue_peak=session.worker.queue_peak,
        )
        successes = [r for r in session.results if r["status"] == "success" and r["displayed"]]
        partials = [r for r in successes if r["kind"] == "partial"]
        finals = [r for r in session.results if r["kind"] == "final"]
        events = read_events(output / "jobs.jsonl")
        info.update(
            caption_stability=summarize_stability(events),
            submitted_requests=sum(e["type"] == "submitted" for e in events),
            coalesced_partial_requests=sum(e["type"] == "coalesced" for e in events),
            first_caption_from_track_start_s=min(
                (r["published_s"] for r in successes), default=None
            ),
            first_partial_from_track_start_s=min(
                (r["published_s"] for r in partials), default=None
            ),
            final_from_owned_end_s=distribution(
                [r["published_s"] - r["end_frame"] / 16000 for r in finals]
            ),
            inference_queue_wait_s=distribution([r["queue_wait_s"] for r in session.results]),
            failed_calls=sum(r["status"] != "success" for r in session.results),
            final_count=len(finals),
            partial_count=len(partials),
            skipped_silence=session.skipped,
        )
        native_ttft = []
        for result in session.results:
            native = (result.get("adapter_result") or {}).get("native_result") or {}
            if native.get("first_generated_token_s") is not None:
                native_ttft.append(native["first_generated_token_s"])
        token_timings = list(session.token_timings.values())
        first_visible = [
            r["first_visible_published_s"]
            for r in token_timings
            if r["first_visible_published_s"] is not None
        ]
        info.update(
            engine_ttft_s=distribution(native_ttft) if native_ttft else None,
            engine_ttft_scope="native 호출 시작→첫 비EOG 생성 토큰. 음성 처리/prefill 포함, 큐·Python 표시는 제외. 전체 시도 범위.",
            first_visible_caption_from_track_start_s=min(
                [r["published_s"] for r in successes] + first_visible, default=None
            ),
            token_timings=token_timings,
            token_display_policy="hold_replayed_prefix_v1"
            if settings.get("token_stream")
            else "completed_result",
            token_output_scope="토큰은 UTF-8 접두 문자열의 임시 표시. 이후 실패도 원시/종료 result에 보존하며 임시 가설은 최종 CER에 더하지 않음.",
        )
        reference = " ".join(s["reference"] for s in sources)
        info["final_transcript_score"] = (
            character_errors(reference, hypothesis)
            if all(s["reference"].strip() for s in sources)
            else None
        )
        info["status"] = (
            "completed"
            if not info["failed_calls"]
            and len(finals) + sum(r["kind"] == "final" for r in session.skipped)
            == len(session.segments)
            else "failed"
        )
    except Exception as error:
        info.update(status="failed", error={"type": type(error).__name__, "message": str(error)})
    finally:
        if session is not None:
            session.close()
            info.update(
                results=session.results,
                segments=session.segments,
                pipeline=session.pipeline.metadata(),
                skipped_silence=session.skipped,
                token_timings=list(session.token_timings.values()),
            )
        if adapter is not None and (session is None or not session.worker.thread.is_alive()):
            adapter.close()
        if log is not None and (session is None or not session.worker.thread.is_alive()):
            log.close()
        write_json(receipt, info)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/gguf.toml"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--vad-model", type=Path, default=Path("models/silero-vad-k2-20261029/silero_vad.onnx")
    )
    parser.add_argument("--gap-s", type=float, default=1, choices=(0, 0.5, 1, 2))
    parser.add_argument(
        "--vad-preprocess",
        choices=tuple(VAD_POLICIES),
        default="identity",
        help="VAD 입력만 인과적 보정. ASR에는 identity 원음 유지",
    )
    parser.add_argument(
        "--vad-threshold",
        type=float,
        default=0.5,
        choices=(0.1, 0.2, 0.3, 0.5),
        help="VAD 시작 임계값 후보. 기본0.5 보존",
    )
    parser.add_argument(
        "--vad-max-speech-s",
        type=int,
        default=5,
        choices=(5, 15, 30),
        help="VAD가 판정을 강화하는 활성 길이. ASR 구간 상한과 독립; 기본5초 보존",
    )
    parser.add_argument("--partial-after-s", type=float, default=2, choices=(1, 2, 3))
    parser.add_argument("--partial-interval-s", type=float, default=1, choices=(0.5, 1, 2, 4))
    parser.add_argument("--max-segment-s", type=int, default=5, choices=(3, 5, 8))
    parser.add_argument("--drain-timeout-s", type=int, default=30, choices=(10, 30, 60))
    parser.add_argument("--queue-capacity", type=int, default=4, choices=(2, 4, 8))
    parser.add_argument(
        "--token-stream",
        action="store_true",
        help="별도 token_callback_v1 GGUF 빌드에서 생성 중 UTF-8 텍스트를 임시 표시",
    )
    parser.add_argument(
        "--no-partials",
        dest="partials",
        action="store_false",
        help="VAD/구간은 유지하고 부분 재전사만 끄는 대조",
    )
    parser.add_argument(
        "--silence-policy",
        choices=("preserve", "skip-zero"),
        default="skip-zero",
        help="완전0 PCM만 별도 기록하고 ASR 호출 생략. 조용한 음성 임계값 필터 아님",
    )
    args = vars(parser.parse_args())
    root = Path(__file__).resolve().parents[4]
    result = run(
        root,
        args.pop("config").resolve(),
        args.pop("manifest").resolve(),
        args.pop("output").resolve(),
        args.pop("vad_model").resolve(),
        **args,
    )
    return 0 if result["status"] == "completed" else 1
