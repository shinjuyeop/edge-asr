"""Qwen 단독 WAV 1배속·VAD·누적 부분 전사. 오디오 encoder 증분 실행은 아니다."""

import argparse
import json
import math
import os
import re
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from threading import Lock

from edge_asr.adapters import create_adapter
from edge_asr.artifacts import digest, write_event, write_json
from edge_asr.audio_policy import VAD_POLICIES
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import Audio, float_digest, load_audio, load_manifest, prepare_track
from edge_asr.metrics import character_errors, distribution
from edge_asr.qwen.realtime.continuous import POLICY_VERSIONS
from edge_asr.qwen.realtime.session import CaptionSession
from edge_asr.qwen.realtime.stability import content_updates
from edge_asr.qwen.realtime.stability import summarize as summarize_stability
from edge_asr.resources import measure_process
from edge_asr.runtime import environment, limits_verified
from edge_asr.segmentation import SEGMENT_LIMITS_S
from edge_asr.streaming import read_events, run_paced
from edge_asr.vad_probability import BoundedProbabilityVad, K2SileroProbability


def session_snapshot(session):
    """현재 세션 상태만 읽는다. 정상 배출 직후와 close 뒤의 호출 시점은 실행기가 정한다."""
    return {
        "results": session.results,
        "segments": session.segments,
        "pipeline": session.pipeline.metadata(),
        "partial_scheduler": session.scheduler_stats,
    }


def collect_completed_metrics(info, session, events, sources, hypothesis, *, token_stream=False):
    """정상 배출 직후 전체 시도를 단계별로 기록한다. 집계 도중 실패해도 앞선 지표를 보존한다."""
    successes = [r for r in session.results if r["status"] == "success" and r["displayed"]]
    partials = [r for r in successes if r["kind"] == "partial"]
    finals = [r for r in session.results if r["kind"] == "final"]
    info.update(
        caption_stability=summarize_stability(events),
        content_updates=content_updates(events, session.segments),
        submitted_requests=sum(e["type"] == "submitted" for e in events),
        coalesced_partial_requests=sum(e["type"] == "coalesced" for e in events),
        first_caption_from_track_start_s=min((r["published_s"] for r in successes), default=None),
        first_partial_from_track_start_s=min((r["published_s"] for r in partials), default=None),
        final_from_owned_end_s=distribution(
            [r["published_s"] - r["end_frame"] / 16000 for r in finals]
        ),
        inference_queue_wait_s=distribution([r["queue_wait_s"] for r in session.results]),
        failed_calls=sum(r["status"] == "failed" for r in session.results),
        cancelled_calls=sum(r["status"] == "cancelled" for r in session.results),
        empty_partial_calls=sum(r.get("failure_kind") == "empty_partial" for r in session.results),
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
        token_display_policy="hold_replayed_prefix_v1" if token_stream else "completed_result",
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


def run(
    root,
    config_path,
    manifest,
    output,
    vad_model,
    *,
    adapter_factory=None,
    vad_factory=None,
    start_signal=None,
    microphone_device=None,
    microphone_seconds=120,
    stop_signal=None,
    reference="",
    **settings,
):
    config = load_evaluation_config(config_path)
    if microphone_device and (
        not math.isfinite(microphone_seconds) or not 0 < microphone_seconds <= 600
    ):
        raise ValueError("마이크 입력 제한은0초 초과600초 이하입니다.")
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
        "scope": "1배속 입력과 추론 동시 진행. 접두 PCM 재전사·음성 KV 재계산. 선택적 고정 text prefix KV 유지·GGUF 토큰 배포를 지원하며 encoder 증분 실행·마이크·음향 단어 시각은 미구현.",
    }
    receipt = output / "worker.json"
    write_json(receipt, info)
    adapter = session = log = None
    try:
        if microphone_device:
            info["input"] = {
                "kind": "microphone",
                "device": microphone_device,
                "duration_limit_s": microphone_seconds,
                "reference": reference,
                "clock_scope": "arecord 시작 직전 시계와16kHz 프레임 기준. 하드웨어 ADC·음향 단어 시각 미계측.",
            }
            info["scope"] = (
                "RP5 ALSA 마이크16kHz·모노 PCM16 수집과 추론 동시 진행. 원음 recording.wav 보존. 지연은 입력 프레임/VAD 구간 기준으로 하드웨어 ADC·음향 단어 시각 미계측."
            )
            audio, sources = None, []
        else:
            audio, sources, manifest_sha = prepare_track(
                root, manifest, gap_s=settings.get("gap_s", 1)
            )
            info.update(sources=sources, input_manifest_sha256=manifest_sha, audio=audio.metadata())
        samples, _ = load_manifest(manifest, root)
        warmup = load_audio(samples[0].audio_path, 16000)
        source_frames = len(warmup.samples)
        maximum = settings.get("max_segment_s", 5)
        if not math.isfinite(maximum) or maximum <= 0:
            raise ValueError("워밍업의 ASR 최대 구간은 유한한 양수여야 합니다.")
        end = min(source_frames, round(maximum * 16000))
        if end < source_frames:
            pcm = warmup.samples[:end]
            warmup = Audio(pcm, 16000, float_digest(pcm))
        info["warmup_input"] = {
            "policy": "leading_max_segment_v1",
            "source_id": samples[0].id,
            "source_audio_sha256": samples[0].audio_sha256,
            "source_frames": source_frames,
            "start_frame": 0,
            "end_frame": end,
            "payload_float32_sha256": float_digest(warmup.samples),
            "scope": "첫 원음의 ASR 최대 구간 이내 PCM만 워밍업. 평가 입력은 전체 원음 유지.",
        }
        vad = (
            vad_factory
            or (
                lambda p: BoundedProbabilityVad(
                    K2SileroProbability(p),
                    threshold=settings.get("vad_threshold", 0.5),
                    max_speech_s=settings.get("vad_max_speech_s", 5),
                    min_silence_s=settings.get("vad_min_silence_s", 0.3),
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
                try:
                    text = adapter.transcribe(warmup)
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("첫 추론·워밍업 출력 오류")
                    row.update(status="success", hypothesis=text)
                finally:
                    row["adapter_result"] = deepcopy(getattr(adapter, "last_result_metadata", None))
        if stack_hef := os.environ.get("EDGE_ASR_HAILO_CNN_STACK_HEF"):
            frame_counts = []
            for row in info["warmup"]:
                stderr = (row["adapter_result"] or {}).get("native_stderr", "")
                match = re.search(r"^edge_hailo_cnn_stack frames=([1-9]\d*) ", stderr, re.M)
                if not match:
                    raise RuntimeError(
                        "CNN HEF를 선택했지만 워밍업에서 Hailo 실행을 확인하지 못했습니다."
                    )
                frame_counts.append(int(match[1]))
            info["audio_accelerator"] = {
                "device": "Hailo-8L",
                "boundary": "cnn_stack",
                "hef_sha256": digest(Path(stack_hef)),
                "warmup_frames": frame_counts,
            }
        info["status"] = "preparing" if start_signal else "replaying"
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
            if k not in ("gap_s", "vad_threshold", "vad_max_speech_s", "vad_min_silence_s")
        }
        session = CaptionSession(
            adapter,
            vad,
            len(audio.samples) if audio else None,
            emit,
            info["telemetry"],
            **session_settings,
        )
        paced = {
            "chunk_ms": 100,
            "queue_capacity_chunks": 10,
            "drain_timeout_s": settings.get("drain_timeout_s", 30),
        }

        def start_gate():
            info["status"] = "ready"
            write_json(receipt, info)
            deadline = time.perf_counter() + 180
            while not start_signal.exists():
                if time.perf_counter() > deadline:
                    raise TimeoutError("청취 화면의 시작 요청을 180초 동안 받지 못했습니다.")
                time.sleep(0.02)
            target = json.loads(start_signal.read_text())["start_monotonic_s"]
            if not isinstance(target, (int, float)) or not math.isfinite(target):
                raise ValueError("유한한 예약 시작 시각이 필요합니다.")
            if not 0 <= target - time.perf_counter() <= 10:
                raise ValueError("예약 시작 시각은 현재부터 10초 이내여야 합니다.")
            time.sleep(max(0, target - time.perf_counter()))

        def started(t0):
            write_json(output / "clock.json", {"t0_monotonic_s": t0 / 1e9})
            info["status"] = "replaying"
            write_json(receipt, info)

        info["replay_resources"] = {}
        with measure_process(info["replay_resources"], sample_device=True):
            if microphone_device:
                from edge_asr.microphone import record

                session.start_stream()
                audio = record(
                    microphone_device,
                    microphone_seconds,
                    output,
                    info["telemetry"],
                    lambda pcm, start, end: session.accept_at(session, pcm, start, end),
                    start_gate=start_gate if start_signal else None,
                    on_start=started,
                    on_update=lambda: write_json(receipt, info),
                    stop_signal=stop_signal,
                )
                sources = [
                    {
                        "id": "microphone",
                        "reference": reference,
                        "audio_sha256": audio.sha256,
                        "start": 0,
                        "end": len(audio.samples),
                    }
                ]
                info.update(sources=sources, audio=audio.metadata(), status="draining")
                write_json(receipt, info)
                hypothesis = session.finish_stream(session)
            else:
                hypothesis = run_paced(
                    session,
                    audio,
                    paced,
                    output / "stream",
                    info["telemetry"],
                    chunk_handler=session.accept_at,
                    start_gate=start_gate if start_signal else None,
                    on_start=started if start_signal else None,
                )
        info.update(
            hypothesis=hypothesis,
            **session_snapshot(session),
            vad=vad.metadata(),
            vad_probability=vad.probability.metadata()
            if hasattr(vad.probability, "metadata")
            else None,
            inference_queue_peak=session.worker.queue_peak,
        )
        events = read_events(output / "jobs.jsonl")
        collect_completed_metrics(
            info, session, events, sources, hypothesis, token_stream=settings.get("token_stream")
        )
    except Exception as error:
        info.update(status="failed", error={"type": type(error).__name__, "message": str(error)})
    finally:
        if session is not None:
            session.close()
            info.update(
                **session_snapshot(session),
                skipped_silence=session.skipped,
                token_timings=list(session.token_timings.values()),
            )
        if adapter is not None and (session is None or not session.worker.thread.is_alive()):
            adapter.close()
        if log is not None and (session is None or not session.worker.thread.is_alive()):
            log.close()
        write_json(receipt, info)
    return info


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/gguf.toml"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-signal", type=Path, help="선택 청취 도구의 예약 시작 JSON")
    parser.add_argument(
        "--microphone-device", help="RP5 ALSA 입력 장치. 지정하면 manifest는 워밍업에만 사용"
    )
    parser.add_argument("--microphone-seconds", type=float, default=120)
    parser.add_argument(
        "--stop-signal", type=Path, help="마이크 입력만 정상 종료하고 최종 추론 배출"
    )
    parser.add_argument(
        "--reference", default="", help="마이크 전체 녹음의 선택 정답. 미입력 시 CER 미측정"
    )
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
    parser.add_argument(
        "--vad-min-silence-s",
        type=float,
        default=argparse.SUPPRESS,
        help="일반 VAD 종료를 확정할 무음 길이. 기본 0.3초",
    )
    parser.add_argument(
        "--onset-context-s",
        type=float,
        default=argparse.SUPPRESS,
        help="발화 시작 앞에서 이전 구간이 소유하지 않은 PCM 최대 0.2초를 추가",
    )
    parser.add_argument(
        "--split-on-pause",
        action="store_true",
        default=argparse.SUPPRESS,
        help="상한 전 0.8초 안에서 짧은 쉼이 끝나면 발화 재개 지점에서 분할",
    )
    parser.add_argument(
        "--partial-after-s", type=float, default=2, choices=(0.45, 0.6, 0.8, 1, 2, 3)
    )
    parser.add_argument("--partial-interval-s", type=float, default=1, choices=(0.5, 1, 2, 4))
    parser.add_argument(
        "--partial-mode", choices=("fixed", "adaptive", "continuous"), default="fixed"
    )
    parser.add_argument("--partial-min-new-s", type=float, default=0.8)
    parser.add_argument("--partial-tail-margin-s", type=float, default=0.2)
    parser.add_argument("--adaptive-policy", choices=("recent", "budgeted"), default="recent")
    parser.add_argument(
        "--partial-cost-model",
        choices=("total", "audio", "audio_scaled"),
        default="total",
        help="budgeted 후속 비용 추정. audio는 음성 비용 확대만, audio_scaled는 축소도 적용",
    )
    parser.add_argument("--partial-budget-s", type=float, default=2.5)
    parser.add_argument(
        "--protect-short-followup",
        action="store_true",
        help="budgeted: 첫 성공 결과가4자 미만이면 후속1회의 비용 예산만 예외. 유휴/횟수/끝 여유 유지",
    )
    parser.add_argument("--partial-max-calls", type=int, default=2)
    parser.add_argument(
        "--continuous-policy",
        choices=tuple(POLICY_VERSIONS),
        default="latest",
        help="paced는 계산량 배분, headroom은 호출 종료 여유 확보, headroom_followup은 끝에서 새 토큰을 표시할 여유가 있으면 1회 추가, first_text는 첫 표시까지의 시간을 별도 추정",
    )
    parser.add_argument(
        "--first-snapshot-cap-s",
        type=float,
        default=argparse.SUPPRESS,
        help="first_text 지연 최초 요청의 PCM 길이. 새 PCM 한 번 분량이 더 쌓였을 때만 적용",
    )
    parser.add_argument(
        "--followup-gap-target-s",
        type=float,
        default=argparse.SUPPRESS,
        help="headroom_followup의 추가 요청을 생략했을 때 예상되는 표시 공백의 최소 초",
    )
    parser.add_argument(
        "--partial-thermal-policy",
        choices=("pause", "pace"),
        default="pause",
        help="continuous: pace는82~85°C/열 감속에 점진적 갱신 감속. 저전압·계측결측·예외고온 보류",
    )
    parser.add_argument(
        "--cancel-partial-for-final",
        action="store_true",
        help="continuous 선택: final 대기 시 실행 중 partial을 다음 토큰 경계에서 취소",
    )
    parser.add_argument(
        "--partial-followup-s",
        type=float,
        choices=(1, 2, 4),
        help="각 구간의 첫 부분 요청 뒤 한 번만 적용할 갱신 간격. 생략하면 기존 반복 간격 유지",
    )
    parser.add_argument("--max-segment-s", type=int, default=5, choices=SEGMENT_LIMITS_S)
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
    return parser


def main():
    args = vars(argument_parser().parse_args())
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
