"""완료된 실제 동시 로그를 새 추론 없이 재생해 파일 ID 단위 선택 정책을 평가한다."""

import argparse
import json
import platform
import tempfile
import time
from collections import Counter
from pathlib import Path

from edge_asr import merging
from edge_asr.artifacts import digest, read_jsonl, write_json, write_jsonl
from edge_asr.merging import POLICIES, Merger
from edge_asr.metrics import character_errors, distribution, normalize
from edge_asr.tools.reports.concurrent import corpus, summarize


def load_trace(folder):
    native = json.loads((folder / "summary.json").read_text())
    if (
        not native.get("input_complete")
        or not (native.get("refine") or {}).get("complete")
        or native.get("status") != "completed"
    ):
        raise ValueError("완료된 Streaming/Refine 동시 기록이 필요합니다.")
    for name, want in native["source_sha256"].items():
        if digest(folder / name) != want:
            raise ValueError("원시 체크섬 불일치: " + name)
    with tempfile.TemporaryDirectory() as tmp:
        if summarize(folder, Path(tmp) / "verified.json") != native:
            raise ValueError("원시 재집계가 원래 요약과 다릅니다.")
    track = json.loads((folder / "track.json").read_text())
    worker = json.loads((folder / "worker.json").read_text())
    sources = track["sources"]
    by_id = {s["id"]: s for s in sources}
    zip_rows = read_jsonl(folder / "zip-sources.jsonl")
    zmap = {r["id"]: r for r in zip_rows}
    session = digest(folder / "worker.json")
    events = []
    closed = []
    for row in read_jsonl(folder / "zip-events.jsonl"):
        if row["type"] == "partial":
            s = sources[row["source_index"]]
            events.append(
                {
                    "type": "partial",
                    "source_id": s["id"],
                    "at_ns": row["at_ns"],
                    "end": row["end"],
                    "text": row["text"],
                }
            )
        elif row["type"] == "source_final":
            s = by_id[row["id"]]
            original = zmap[s["id"]]
            if (
                row["hypothesis"] != original["hypothesis"]
                or row["finalized_ns"] != original["finalized_ns"]
                or row["end"] != s["end"]
            ):
                raise ValueError("Zip 최종 이벤트와 원본 결과 불일치")
            closed.append(s["id"])
            events.append(
                {
                    "type": "close",
                    "source_id": s["id"],
                    "at_ns": row["finalized_ns"],
                    "end": s["end"],
                    "text": row["hypothesis"],
                }
            )
    if sorted(closed) != sorted(by_id):
        raise ValueError("원본별 Zip 종료 이벤트가 누락/중복됐습니다.")
    jobs = read_jsonl(folder / "refine-jobs.jsonl")
    returns = read_jsonl(folder / "refine-results.jsonl")

    def identity(row):
        return {
            "job_id": row["index"],
            "source_id": row["source_id"],
            "start": row["start"],
            "end": row["end"],
            "context_start": row.get("context_start", row["start"]),
            "context_end": row.get("context_end", row["end"]),
            "payload_sha256": row["payload_float32_sha256"],
        }

    for row in jobs:
        events.append(
            {
                **identity(row),
                "type": "job",
                "at_ns": row["at_ns"],
                "accepted": row["type"] == "enqueue",
            }
        )
    for row in returns:
        events.append(
            {
                **identity(row),
                "type": "refine",
                "at_ns": row["ended_ns"],
                "status": row["status"],
                "text": row.get("hypothesis"),
            }
        )
    priority = {"job": 0, "refine": 1, "partial": 2, "close": 3}
    events.sort(key=lambda e: (e["at_ns"], priority[e["type"]]))
    t0 = worker["replay"]["t0_monotonic_ns"]
    finish = worker["measurement_finished_ns"]
    if any(not t0 <= e["at_ns"] <= finish for e in events):
        raise ValueError("계측 구간 밖의 이벤트 시각입니다.")
    for event in events:
        event["session_id"] = session
    return sources, worker, events, zmap, returns, session


def suffix_change(previous, current):
    before, after = normalize(previous), normalize(current)
    shared = 0
    for a, b in zip(before, after):
        if a != b:
            break
        shared += 1
    return len(before) - shared, len(after) - shared


def evaluate_policy(sources, worker, events, zmap, returns, session, policy):
    engine = Merger(session, policy)
    for source in sources:
        engine.register(source["id"], source["start"], source["end"])
    for event in events:
        engine.apply(event)
    finals = engine.finish(worker["measurement_finished_ns"])
    selected = {r["source_id"]: r["text"] for r in finals}
    zip_hyp = {sid: r["hypothesis"] for sid, r in zmap.items()}
    refine_hyp = {
        s["id"]: " ".join(r["hypothesis"] for r in returns if r["source_id"] == s["id"])
        for s in sources
    }
    # 정답 접근은 선택/표시 상태를 모두 확정한 뒤에만 한다.
    scores = []
    for source in sources:
        sid = source["id"]
        chosen = character_errors(source["reference"], selected[sid], "ko_cer_v1")
        baseline = character_errors(source["reference"], zip_hyp[sid], "ko_cer_v1")
        scores.append(
            {
                "id": sid,
                "reference_characters": chosen["reference_characters"],
                "selected_errors": chosen["errors"],
                "zip_errors": baseline["errors"],
                "error_delta": chosen["errors"] - baseline["errors"],
                "empty_reference": chosen["reference_characters"] == 0,
            }
        )
    changes = [u for u in engine.updates if u["text_changed"]]
    removed = [suffix_change(u["previous_text"], u["text"])[0] for u in changes]
    first = []
    no_visible = 0
    t0 = worker["replay"]["t0_monotonic_ns"]
    for source in sources:
        visible = next(
            (u for u in engine.updates if u["source_id"] == source["id"] and u["text"]), None
        )
        if visible:
            first.append((visible["at_ns"] - t0) / 1e9 - source["start"] / 16000)
        else:
            no_visible += 1
    nonempty = [r for r in scores if not r["empty_reference"]]
    result = {
        "policy": policy,
        "selected_cer": corpus(sources, selected, True),
        "zip_cer": corpus(sources, zip_hyp, True),
        "refine_cer": corpus(sources, refine_hyp, True),
        "selection_counts": dict(Counter(r["model"] for r in finals)),
        "decision_reasons": dict(Counter(r["reason"] for r in finals)),
        "ignored_events": dict(engine.ignored),
        "versus_zip": {
            "improved_sources": sum(r["error_delta"] < 0 for r in nonempty),
            "tied_sources": sum(r["error_delta"] == 0 for r in nonempty),
            "worsened_sources": sum(r["error_delta"] > 0 for r in nonempty),
            "error_delta": sum(r["error_delta"] for r in nonempty),
            "scope": "nonempty_references_only",
        },
        "visible_updates": len(changes),
        "normalized_suffix_rewrite_events": sum(n > 0 for n in removed),
        "normalized_removed_suffix_characters": sum(removed),
        "normalized_final_corrections": sum(
            normalize(r["text"]) != normalize(zip_hyp[r["source_id"]]) for r in finals
        ),
        "trace_first_visible_from_source_start_s": distribution(first),
        "no_visible_sources": no_visible,
        "trace_selection_from_file_end_s": distribution(
            [(r["at_ns"] - t0) / 1e9 - r["end"] / 16000 for r in finals]
        ),
        "trace_wait_after_zip_final_s": distribution(
            [(r["at_ns"] - zmap[r["source_id"]]["finalized_ns"]) / 1e9 for r in finals]
        ),
        "trace_oldest_refine_ready_to_selection_s": distribution(
            [
                (
                    r["at_ns"]
                    - min(x["ended_ns"] for x in returns if x["source_id"] == r["source_id"])
                )
                / 1e9
                for r in finals
                if r["model"] == "refine"
            ]
        ),
        "timing_scope_ko": "저장된 부분 반환/Zip 최종/child ended_ns를 가용성 대리 시각으로 재생. 실제 parent 수신·병합 실행·UI/마이크·단어 정렬 지연은 미측정. 파일 종료를 기다리는 선택이며 VAD 부분 문자열 교체가 아님.",
        "stability_scope_ko": "원문 변경과 정규화 공통 접두사 뒤 지운 접미부를 별도 집계. 제거 문자수는 Levenshtein 오류수나 실제 렌더링 비용이 아님.",
        "unmeasured": {
            "live_merge_dispatch_s": None,
            "ui_display_s": None,
            "aligned_recognition_latency_s": None,
            "per_vad_partial_replacement_cer": None,
        },
    }
    return result, engine.updates, finals, scores


def replay(folder, output, policies=POLICIES):
    if output.exists():
        raise FileExistsError(output)
    if folder.resolve() == output.resolve() or folder.resolve() in output.resolve().parents:
        raise ValueError("원시 내부에 재생 결과를 쓰지 않습니다.")
    if (
        not policies
        or len(set(policies)) != len(policies)
        or any(p not in POLICIES for p in policies)
    ):
        raise ValueError("지원하는 병합 정책 목록이 필요합니다.")
    started = time.perf_counter()
    sources, worker, events, zmap, returns, session = load_trace(folder)
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    for policy in policies:
        result, updates, finals, scores = evaluate_policy(
            sources, worker, events, zmap, returns, session, policy
        )
        dest = output / policy
        dest.mkdir()
        write_json(dest / "summary.json", result)
        write_jsonl(dest / "timeline.jsonl", updates)
        write_jsonl(dest / "decisions.jsonl", finals)
        write_jsonl(dest / "scores.jsonl", scores)
        results[policy] = result
    result = {
        "schema": "merge-trace-replay-v1",
        "session_id": session,
        "native_source_commit": worker["source_commit"],
        "native_refine_policy": worker["settings"].get("refine_policy", "fixed5"),
        "source_count": len(sources),
        "events": len(events),
        "input_files_sha256": {
            p.name: digest(p)
            for p in (
                folder / "summary.json",
                folder / "worker.json",
                folder / "track.json",
                folder / "zip-events.jsonl",
                folder / "refine-jobs.jsonl",
                folder / "refine-results.jsonl",
            )
        },
        "code_sha256": {
            "merging.py": digest(Path(merging.__file__)),
            "merge_replay.py": digest(Path(__file__)),
        },
        "policies": results,
        "scope_ko": "실제 추론 로그를 이용한 정답 비의존 파일 ID 단위 선택 정책의 모델 없는 재생. 새 native ASR/1배속 입력/실제 UI 지연 측정이 아님.",
    }
    write_json(output / "summary.json", result)
    write_json(
        output / "execution.json",
        {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "wall_s": time.perf_counter() - started,
            "native_asr_inference": False,
            "realtime_input_supplied": False,
        },
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", choices=("all", *POLICIES), default="all")
    args = parser.parse_args()
    replay(args.run, args.output, POLICIES if args.policy == "all" else (args.policy,))
    print(
        json.dumps({"output": str(args.output), "native_asr_inference": False}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
