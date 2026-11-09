"""연속 토큰의 사전 구조 후보를 그대로 재생해 literal 교체의 정확도/보존을 평가한다."""

import argparse
import json
import platform
import tempfile
import time
import unicodedata
from collections import Counter
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl, write_json, write_jsonl
from edge_asr.legacy import token_splice
from edge_asr.legacy.token_splice import POLICIES, TokenSplice
from edge_asr.legacy.tools.reports.concurrent import corpus
from edge_asr.legacy.tools.reports.merge_replay import suffix_change
from edge_asr.legacy.tools.reports.token_alignment import summarize
from edge_asr.metrics import character_errors, distribution, normalize


def load_trace(folder):
    summary = json.loads((folder / "summary.json").read_text())
    if (
        summary.get("schema") != "continuous-token-diagnostic-summary-v1"
        or summary.get("status") != "completed"
        or not summary.get("token_diagnostics", {}).get("complete")
    ):
        raise ValueError("완료된 연속 토큰 진단 기록이 필요합니다.")
    for name, sha in summary["source_sha256"].items():
        if digest(folder / name) != sha:
            raise ValueError("원시 체크섬 불일치: " + name)
    with tempfile.TemporaryDirectory() as directory:
        if summarize(folder, Path(directory) / "summary.json") != summary:
            raise ValueError("원시 진단 재집계 불일치")
    worker = json.loads((folder / "worker.json").read_text())
    track = json.loads((folder / "track.json").read_text())
    snapshots = read_jsonl(folder / "zip-token-snapshots.jsonl")
    results = {r["index"]: r for r in read_jsonl(folder / "refine-results.jsonl")}
    events = []
    session = digest(folder / "worker.json")
    candidates = set()
    for record in read_jsonl(folder / "token-alignment-events.jsonl"):
        original = record["event"]
        kind = original["type"]
        at = record["assessed_ns"]
        if kind == "snapshot":
            event = {"type": kind, "snapshot": snapshots[original["snapshot_index"]]}
        elif kind == "job":
            event = {"type": kind, "job": original["job"]}
        else:
            result = original["result"]
            native = results[result["index"]]
            event = {"type": "receipt", "result": {**result, "text": native.get("hypothesis")}}
        events.append({**event, "session_id": session, "at_ns": at})
        for change in record["changes"]:
            if change["state"] == "structural_candidate":
                candidates.add(change["index"])
            events.append(
                {"type": "candidate", "change": change, "session_id": session, "at_ns": at}
            )
    if len(candidates) != summary["token_diagnostics"]["ever_structural_candidate_jobs"]:
        raise ValueError("사전 후보 집합 불일치")
    return track["sources"], worker, events, session, sorted(candidates)


def overlap_suffix_prefix(left, right):
    a, b = normalize(left), normalize(right)
    for n in range(min(len(a), len(b)), 0, -1):
        if a[-n:] == b[:n]:
            return n
    return 0


def boundary_observations(final):
    """복사 경계의 반복 가능성만 기록. 단어/오류 판정이나 텍스트 필터가 아니다."""
    text = final["text"]
    rows = []
    for part in final["proof"]["parts"]:
        if part["kind"] != "refine":
            continue
        a, b = part["output_range"]
        value = text[a:b]
        left = text[:a]
        right = text[b:]
        punct = lambda c: unicodedata.category(c).startswith("P")
        rows.append(
            {
                "source_id": final["source_id"],
                "job_id": part["job_id"],
                "left_normalized_overlap_characters": overlap_suffix_prefix(left, value),
                "right_normalized_overlap_characters": overlap_suffix_prefix(value, right),
                "left_adjacent_punctuation": left[-1:] + value[:1]
                if left and value and punct(left[-1]) and punct(value[0])
                else None,
                "right_adjacent_punctuation": value[-1:] + right[:1]
                if right and value and punct(value[-1]) and punct(right[0])
                else None,
            }
        )
    return rows


def evaluate(sources, worker, events, session, policy):
    # Source 목록에서 ID/오디오 좌표만 core에 보낸다. 정답은 모든 결정을 끝낸 뒤 읽는다.
    engine = TokenSplice(
        session, [{k: s[k] for k in ("id", "start", "end")} for s in sources], policy
    )
    for event in events:
        engine.apply(event)
    finals = engine.finish(worker["measurement_finished_ns"])
    selected = {r["source_id"]: r["text"] for r in finals}
    baseline = {r["source_id"]: r["original_zip_text"] for r in finals}
    scores = []
    for source in sources:
        sid = source["id"]
        picked = character_errors(source["reference"], selected[sid])
        old = character_errors(source["reference"], baseline[sid])
        scores.append(
            {
                "id": sid,
                "reference": source["reference"],
                "hypothesis": selected[sid],
                "zip_hypothesis": baseline[sid],
                "selected": picked,
                "zip": old,
                "error_delta": picked["errors"] - old["errors"],
            }
        )
    active = [i for r in finals for i in r["active_patch_ids"]]
    changes = [r for r in engine.updates if r["text_changed"]]
    removed = [suffix_change(r["previous_text"], r["text"])[0] for r in changes]
    nonempty = [r for r in scores if r["selected"]["reference_characters"]]
    boundaries = [row for f in finals for row in boundary_observations(f)]
    admitted = [r for r in engine.decisions if r["accepted"]]
    t0 = worker["replay"]["t0_monotonic_ns"]
    source_by_id = {s["id"]: s for s in sources}
    first_visible = []
    for s in sources:
        first = next((u for u in engine.updates if u["source_id"] == s["id"] and u["text"]), None)
        if first:
            first_visible.append((first["at_ns"] - t0) / 1e9 - s["start"] / 16000)
    result = {
        "policy": policy,
        "selected_cer": corpus(sources, selected, True),
        "zip_cer": corpus(sources, baseline, True),
        "versus_zip": {
            "improved_sources": sum(r["error_delta"] < 0 for r in nonempty),
            "tied_sources": sum(r["error_delta"] == 0 for r in nonempty),
            "worsened_sources": sum(r["error_delta"] > 0 for r in nonempty),
            "error_delta": sum(r["error_delta"] for r in nonempty),
        },
        "simulated_patch_admissions": len(admitted),
        "final_active_patches": len(active),
        "final_active_patch_ids": active,
        "decision_reasons": dict(Counter(d["reason"] for d in engine.decisions)),
        "ignored_events": dict(engine.ignored),
        "conservation_checks": engine.conservation_checks,
        "mechanical_missing_or_changed_unselected_zip_characters": 0,
        "final_retained_zip_characters": sum(f["proof"]["retained_zip_characters"] for f in finals),
        "final_replaced_original_characters": sum(
            f["proof"]["replaced_original_characters"] for f in finals
        ),
        "visible_text_changes": len(changes),
        "normalized_suffix_rewrite_events": sum(n > 0 for n in removed),
        "normalized_removed_suffix_characters": sum(removed),
        "boundary_diagnostics": {
            "patches": len(boundaries),
            "left_overlap_at_least_two_characters": sum(
                x["left_normalized_overlap_characters"] >= 2 for x in boundaries
            ),
            "right_overlap_at_least_two_characters": sum(
                x["right_normalized_overlap_characters"] >= 2 for x in boundaries
            ),
            "max_overlap_characters": max(
                [
                    0,
                    *[
                        max(
                            x["left_normalized_overlap_characters"],
                            x["right_normalized_overlap_characters"],
                        )
                        for x in boundaries
                    ],
                ]
            ),
            "adjacent_punctuation_boundaries": sum(
                x["left_adjacent_punctuation"] is not None for x in boundaries
            )
            + sum(x["right_adjacent_punctuation"] is not None for x in boundaries),
            "scope_ko": "중복 가능성의 기술적 관측. 2자는 표시용 고정 집계이며 선택/필터에 쓰지 않음. 실제 인식 삽입/삭제는 CER의 I/D로 별도 평가.",
        },
        "trace_first_visible_from_source_start_s": distribution(first_visible),
        "trace_patch_admission_from_owned_end_s": distribution(
            [(d["at_ns"] - t0) / 1e9 - engine.jobs[d["job_id"]]["end"] / 16000 for d in admitted]
        ),
        "trace_patch_admissions_before_source_audio_end": sum(
            d["at_ns"]
            < t0 + source_by_id[engine.jobs[d["job_id"]]["source_id"]]["end"] / 16000 * 1e9
            for d in admitted
        ),
        "timing_scope_ko": "M3-4 audit assessed_ns의 순서/가용 시점으로 재생한 상태 전환. 새 실시간 입력/ASR/실제 merge dispatch/UI 지연 측정이 아님.",
        "unmeasured": {
            "new_native_asr_rtf": None,
            "new_native_asr_cpu_percent": None,
            "new_native_asr_rss_mib": None,
            "actual_merge_apply_s": None,
            "ui_display_s": None,
            "acoustic_alignment_s": None,
        },
    }
    return result, engine.updates, engine.decisions, finals, scores, boundaries


def replay(folder, output, policies=POLICIES):
    if output.exists():
        raise FileExistsError(output)
    if folder.resolve() == output.resolve() or folder.resolve() in output.resolve().parents:
        raise ValueError("원시 내부에 재생 산출물을 쓰지 않습니다.")
    if (
        not policies
        or len(set(policies)) != len(policies)
        or any(p not in POLICIES for p in policies)
    ):
        raise ValueError("지원 정책이 필요합니다.")
    began = time.perf_counter()
    sources, worker, events, session, candidates = load_trace(folder)
    output.mkdir(parents=True, exist_ok=False)
    out = {}
    for policy in policies:
        summary, timeline, decisions, finals, scores, boundaries = evaluate(
            sources, worker, events, session, policy
        )
        dest = output / policy
        dest.mkdir()
        write_json(dest / "summary.json", summary)
        for name, rows in (
            ("timeline.jsonl", timeline),
            ("decisions.jsonl", decisions),
            ("finals.jsonl", finals),
            ("scores.jsonl", scores),
            ("boundaries.jsonl", boundaries),
        ):
            write_jsonl(dest / name, rows)
        out[policy] = summary
    inputs = sorted(p for p in folder.rglob("*") if p.is_file())
    result = {
        "schema": "guarded-token-splice-replay-v1",
        "native_source_commit": worker["source_commit"],
        "session_id": session,
        "sources": len(sources),
        "structural_candidate_job_ids": candidates,
        "policies": out,
        "input_files_sha256": {str(p.relative_to(folder)): digest(p) for p in inputs},
        "code_sha256": {
            "token_splice.py": digest(Path(token_splice.__file__)),
            "splice_replay.py": digest(Path(__file__)),
        },
        "scope_ko": "정답 비의존 사전 구조 후보의 literal 교체를 저장 로그에서만 재생. 비선택 원본은 보존하며 문장부호/공백/중복 제거 없음. 신규 ASR/실시간 입력/실제 교체/UI 실행 없음.",
    }
    write_json(output / "summary.json", result)
    write_json(
        output / "execution.json",
        {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "wall_s": time.perf_counter() - began,
            "new_native_asr_inference": False,
            "realtime_input_supplied": False,
            "actual_live_replacements": 0,
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
    print(json.dumps({"output": str(args.output), "new_native_asr_inference": False}))


if __name__ == "__main__":
    main()
