"""명시한 로컬 실행의 증분 읽기·음원 검증과 기존 평가기 실행."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
import tomllib
import uuid

from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.dataset import load_manifest, prepare_track, write_wav
from edge_asr.qwen.realtime.replay import argument_parser
from edge_asr.qwen.viewer.analysis import compact_event, source_metrics


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


class Run:
    def __init__(self, path):
        self.path = path.resolve()
        self.id = digest_text(str(self.path))[:16]
        self.worker, self.events, self.summary = {}, [], None
        self.offset, self.stamp = 0, None
        self.summary_key = None
        self.lock = threading.RLock()
        self.audio_path = None

    def refresh(self):
        with self.lock:
            worker = self.path / "worker.json"
            stat = worker.stat() if worker.exists() else None
            stamp = (stat.st_ino, stat.st_mtime_ns, stat.st_size) if stat else None
            if stamp is not None and stamp != self.stamp:
                self.worker = read(worker)
                if self.worker.get("kind") != "qwen3_paced_partial_replay":
                    raise ValueError("Qwen 연속 입력 worker.json이 필요합니다.")
                self.stamp = stamp
            log = self.path / "jobs.jsonl"
            if log.exists():
                with log.open("rb") as stream:
                    stream.seek(self.offset)
                    while line := stream.readline():
                        if not line.endswith(b"\n"):
                            break
                        row = json.loads(line)
                        if row["type"] in ("token", "result"):
                            self.events.append(compact_event(row))
                        self.offset = stream.tell()
            key = (self.stamp, len(self.events))
            if self.worker.get("status") in ("completed", "failed") and self.summary_key != key:
                self.summary = source_metrics(self.worker.get("sources", []), self.events)
                self.summary_key = key

    def snapshot(self, cursor=0):
        with self.lock:
            self.refresh()
            w = self.worker
            guard = read(self.path / "execution.json", {})
            complete = bool(w.get("telemetry", {}).get("complete"))
            terminal = w.get("status") in ("completed", "failed") or bool(
                guard.get("finished_at_utc")
            )
            status = w.get("status", "preparing")
            if terminal and status not in ("completed", "failed"):
                status = "interrupted"
            return {
                "id": self.id,
                "name": str(self.path),
                "status": status,
                "sources": w.get("sources", []),
                "audio": w.get("audio"),
                "config": w.get("config", {}),
                "settings": w.get("settings", {}),
                "source_commit": w.get("source_commit"),
                "cursor": len(self.events),
                "events": self.events[cursor:],
                "summary": self.summary,
                "score": w.get("final_transcript_score") if terminal and complete else None,
                "score_scope": "전체 입력·최종 전사 연결 / 실패 빈 전사 포함 / ko_cer_v1",
                "complete_input": complete,
                "terminal": terminal,
                "dropped_chunks": w.get("telemetry", {}).get("dropped_chunks")
                if terminal
                else None,
                "failed_calls": sum(
                    e.get("status") == "failed" for e in self.events if e["type"] == "result"
                ),
                "clock": read(self.path / "clock.json"),
                "scheduled": read(self.path / "viewer-start.json"),
                "error": w.get("error"),
                "stability": w.get("caption_stability"),
                "guard": guard.get("status"),
                "end_s": max((e.get("published_s", 0) for e in self.events), default=0),
            }


def digest_text(text):
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


class Library:
    def __init__(self, root, runs, profiles, manifests):
        self.root = root.resolve()
        self.runs, self.profiles, self.samples = {}, {}, {}
        self.process = None
        self.active_id = None
        self.lease = None
        self.lock = threading.RLock()
        self.cache = self.root / "results/listener"
        self.cache.mkdir(parents=True, exist_ok=True)
        for path in profiles:
            value = tomllib.loads(path.read_text())
            args = self.replay_args(value, self.cache / "unused", self.cache / "unused.jsonl")
            argument_parser().parse_args(args)
            self.profiles[path.stem] = value
            manifests = [*manifests, self.root / value["manifest"]]
        for path in manifests:
            samples, _ = load_manifest(path, self.root)
            for sample in samples:
                key = (sample.id, sample.audio_sha256)
                self.samples[key] = sample
        for path in runs:
            self.add(path)

    def add(self, path):
        value = Run(path)
        self.runs[value.id] = value
        return value

    def replay_args(self, profile, output, manifest):
        args = [
            "--config",
            str(self.root / profile["config"]),
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--vad-model",
            str(self.root / profile["vad_model"]),
        ]
        for key, value in profile.get("replay", {}).items():
            if key in ("output", "config", "manifest", "start_signal", "vad_model"):
                raise ValueError("replay 표에는 연속 입력 옵션만 지정합니다.")
            if key == "partials":
                if value is False:
                    args.append("--no-partials")
            elif isinstance(value, bool):
                if value:
                    args.append("--" + key.replace("_", "-"))
            else:
                args.extend(["--" + key.replace("_", "-"), str(value)])
        return args

    def catalog(self):
        return {
            "runs": [
                {"id": r.id, "name": str(r.path.relative_to(self.root))} for r in self.runs.values()
            ],
            "profiles": [
                {
                    "id": key,
                    "title": p.get("title", key),
                    "config": p["config"],
                    "replay": p.get("replay", {}),
                    "sources": [
                        {"id": s.id, "reference": s.reference}
                        for s in load_manifest(self.root / p["manifest"], self.root)[0]
                    ],
                }
                for key, p in self.profiles.items()
            ],
        }

    def audio(self, run):
        with run.lock:
            if run.audio_path:
                return run.audio_path
            run.refresh()
            w = run.worker
            if not w.get("sources"):
                raise ValueError("음원 메타데이터 준비 중입니다.")
            directory = self.cache / "audio" / digest_text(str(run.path) + w["audio"]["sha256"])
            directory.mkdir(parents=True, exist_ok=True)
            manifest = directory / "manifest.jsonl"
            rows = []
            for source in w["sources"]:
                sample = self.samples.get((source["id"], source["audio_sha256"]))
                if sample is None:
                    raise ValueError(
                        f"음원 목록에 {source['id']}가 없습니다. --manifest로 등록하세요."
                    )
                rows.append(
                    {
                        "id": sample.id,
                        "reference": source["reference"],
                        "audio_path": str(sample.audio_path),
                        "audio_sha256": sample.audio_sha256,
                    }
                )
            write_jsonl(manifest, rows)
            audio, sources, _ = prepare_track(
                self.root, manifest, gap_s=w["settings"].get("gap_s", 1)
            )
            if sources != w["sources"] or audio.metadata() != w["audio"]:
                raise ValueError("실측 PCM·좌표와 청취 음원이 일치하지 않습니다.")
            target = directory / "audio.wav"
            if not target.exists():
                write_wav(target, (round(x * 32768) for x in audio.samples))
                write_json(
                    directory / "verified.json",
                    {"source_pcm_sha256": audio.sha256, "wav_sha256": digest(target)},
                )
            elif read(directory / "verified.json")["wav_sha256"] != digest(target):
                raise ValueError("기존 청취 WAV 해시가 다릅니다.")
            run.audio_path = target
            return target

    def launch(self, profile_id, selection):
        with self.lock:
            if self.process and self.process.poll() is None:
                raise ValueError("이미 준비 또는 실행 중인 평가가 있습니다.")
            if self.lease:
                self.lease.close()
            self.lease = (self.cache / "inference.lock").open("a")
            try:
                fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.lease.close()
                self.lease = None
                raise ValueError("다른 청취 서버의 평가가 실행 중입니다.") from None
            profile = self.profiles[profile_id]
            samples, _ = load_manifest(self.root / profile["manifest"], self.root)
            if selection == "six":
                samples = samples[:6]
            elif selection != "all":
                samples = [s for s in samples if s.id == selection]
            if not samples:
                raise ValueError("선택한 음원이 없습니다.")
            identifier = time.strftime("listen-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            inputs = self.cache / "inputs" / identifier
            inputs.mkdir(parents=True)
            manifest = inputs / "manifest.jsonl"
            write_jsonl(
                manifest,
                [
                    {
                        "id": s.id,
                        "audio_path": str(s.audio_path),
                        "reference": s.reference,
                        "audio_sha256": s.audio_sha256,
                    }
                    for s in samples
                ],
            )
            output = self.root / "results/optimization/opt3" / identifier
            args = self.replay_args(profile, output, manifest)
            args += ["--start-signal", str(output / "viewer-start.json")]
            argument_parser().parse_args(args)
            script = str(self.root / "scripts/edge_asr.py")
            command = [
                sys.executable,
                script,
                "tools",
                "thermal-run",
                "--output",
                str(output),
                "--timeout-s",
                "1800",
                "--temperature-policy",
                "operational",
                "--skip-temperature-admission",
                "--",
                sys.executable,
                script,
                "tools",
                "replay-qwen3",
                *args,
            ]
            write_json(
                inputs / "launch.json",
                {
                    "command": command,
                    "profile": profile,
                    "scope": "청취 도구 동시 실행. 기존 무관측 벤치마크와 구분.",
                },
            )
            with (inputs / "launcher.log").open("x") as log:
                self.process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    pass_fds=(self.lease.fileno(),),
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1"},
                )
            self.active_id = self.add(output).id
            return self.active_id

    def stop(self, run_id):
        with self.lock:
            if run_id != self.active_id or not self.process or self.process.poll() is not None:
                raise ValueError("이 서버에서 실행 중인 평가만 중단할 수 있습니다.")
            # thermal-run의 KeyboardInterrupt 정리가 자기 추론 프로세스 그룹을 배출한다.
            self.process.send_signal(signal.SIGINT)
            return {"status": "stopping"}

    def start(self, run_id):
        with self.lock:
            if run_id != self.active_id or not self.process or self.process.poll() is not None:
                raise ValueError("이 서버에서 준비 중인 평가만 시작할 수 있습니다.")
            run = self.runs[run_id]
            run.refresh()
            if run.worker.get("status") != "ready":
                raise ValueError("워밍업·입력 준비가 완료되어야 시작할 수 있습니다.")
            path = run.path / "viewer-start.json"
            if path.exists():
                raise ValueError("이미 시작 예약된 실행입니다.")
            value = {"start_monotonic_s": time.perf_counter() + 3}
            write_json(path, value)
            return value
