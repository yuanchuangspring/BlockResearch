"""Diagnose existing eval traces without running new agents.

Extract per-stage knowledge-increment and system-health metrics
from research_trace and research_state inside eval output JSON files.
"""
import json, sys
from pathlib import Path
from collections import defaultdict


def _safe_list(value):
    return value if isinstance(value, list) else []


def diagnose_trace(case: dict) -> dict:
    """Return per-case diagnostic metrics."""
    trace = _safe_list(case.get("research_trace", []))
    state = case.get("research_state") or {}
    graph = _safe_list(state.get("graph", []))

    # --- per-stage audit metrics ---
    audit_by_stage = defaultdict(lambda: {"proposed": 0, "accepted": 0, "leads": 0})
    for node in graph:
        if node.get("kind") != "AUDIT":
            continue
        stage = node.get("stage", 0)
        audit_by_stage[stage]["proposed"] += len(_safe_list(node.get("proposed_claims", [])))
        audit_by_stage[stage]["accepted"] += len(_safe_list(node.get("accepted_claim_ids", [])))
        audit_by_stage[stage]["leads"] += len(_safe_list(node.get("lead_ids", [])))

    # --- first verified stage ---
    first_verified_stage = None
    verified_by_stage = {}
    max_consecutive_zero = 0
    current_zero_run = 0
    for stage_idx in range(1, len(trace) + 2):
        accepted = audit_by_stage.get(stage_idx, {}).get("accepted", 0)
        verified_by_stage[stage_idx] = accepted
        if first_verified_stage is None and accepted > 0:
            first_verified_stage = stage_idx
        if accepted == 0 and audit_by_stage.get(stage_idx, {}).get("proposed", 0) >= 0:
            current_zero_run += 1
            max_consecutive_zero = max(max_consecutive_zero, current_zero_run)
        else:
            current_zero_run = 0

    # --- tool success/failure ---
    tool_attempts = {"FETCH": 0, "READ_PDF": 0, "BROWSE": 0}
    tool_successes = {"FETCH": 0, "READ_PDF": 0, "BROWSE": 0}
    tool_errors = defaultdict(int)
    for stage_data in trace:
        for node in _safe_list(stage_data.get("nodes", [])):
            ntype = node.get("type", "")
            output = node.get("output") or {}
            if ntype in ("FETCH", "READ_PDF"):
                tool_attempts[ntype] = tool_attempts.get(ntype, 0) + 1
                if output.get("text") and len(str(output.get("text", ""))) >= 100:
                    tool_successes[ntype] = tool_successes.get(ntype, 0) + 1
                if output.get("error"):
                    tool_errors[f"{ntype}:{str(output['error'])[:80]}"] += 1
            elif ntype == "BROWSE":
                tool_attempts["BROWSE"] += 1
                pages = _safe_list(output.get("pages", []))
                successful = sum(1 for p in pages if isinstance(p, dict) and len(str(p.get("text", ""))) >= 100)
                if successful > 0:
                    tool_successes["BROWSE"] += 1

    total_fetch_attempts = tool_attempts.get("FETCH", 0) + tool_attempts.get("READ_PDF", 0)
    total_fetch_successes = tool_successes.get("FETCH", 0) + tool_successes.get("READ_PDF", 0)

    # --- first named candidate stage ---
    first_named_candidate_stage = None
    for stage_data in trace:
        for node in _safe_list(stage_data.get("nodes", [])):
            if node.get("type") != "SOLVE":
                continue
            output = node.get("output") or {}
            candidate = str(output.get("answer_candidate", "")).strip()
            if candidate and candidate.lower() not in {"none", "no match", "no match found", "not found", "n/a", ""}:
                first_named_candidate_stage = stage_data.get("stage")
                break
        if first_named_candidate_stage:
            break

    # --- candidate prune count ---
    rejected = _safe_list(state.get("rejected_answers", []))

    # --- answer condition coverage (from final verification) ---
    final_verdict = {}
    for stage_data in reversed(trace):
        if "verification" in stage_data:
            final_verdict = stage_data["verification"]
            break

    # --- final answer condition coverage from hypotheses ---
    hypotheses = _safe_list(state.get("hypotheses", []))
    answer = str(state.get("verified_answer") or case.get("predicted", "")).strip()
    answer_coverage = {}
    for hyp in hypotheses:
        if str(hyp.get("entity", "")).lower() == answer.lower():
            coverage = _safe_list(hyp.get("coverage", []))
            statuses = {}
            for cov in coverage:
                statuses[cov.get("condition_id", "?")] = cov.get("status", "?")
            answer_coverage = statuses
            break

    # --- total verified claims ---
    total_verified = len(_safe_list(state.get("claims", [])))

    # --- Solver errors ---
    solver_errors = 0
    for stage_data in trace:
        for node in _safe_list(stage_data.get("nodes", [])):
            if node.get("type") == "SOLVE":
                output = node.get("output") or {}
                if output.get("error"):
                    solver_errors += 1

    return {
        "question": str(case.get("question", ""))[:120],
        "predicted": str(case.get("predicted", "")),
        "expected": str(case.get("expected", "")),
        "correct": bool(case.get("correct")),
        "seconds": case.get("seconds", 0),
        "error": str(case.get("error", "")),
        "stages_run": len(trace),
        # Knowledge increment
        "first_verified_stage": first_verified_stage,
        "verified_by_stage": dict(verified_by_stage),
        "total_verified_claims": total_verified,
        "max_consecutive_zero_audit": max_consecutive_zero,
        # Tool health
        "fetch_attempts": total_fetch_attempts,
        "fetch_successes": total_fetch_successes,
        "fetch_success_rate": round(total_fetch_successes / max(total_fetch_attempts, 1), 2),
        "browse_attempts": tool_attempts.get("BROWSE", 0),
        "browse_with_pages": tool_successes.get("BROWSE", 0),
        "top_tool_errors": dict(tool_errors.most_common(5) if hasattr(tool_errors, 'most_common') else list(tool_errors.items())[:5]),
        # Candidate tracking
        "first_named_candidate_stage": first_named_candidate_stage,
        "candidate_prune_count": len(rejected),
        # Answer quality
        "verifier_accepted": bool(final_verdict.get("accepted")),
        "verifier_reason": str(final_verdict.get("reason", ""))[:200],
        "answer_condition_coverage": answer_coverage,
        "solver_errors": solver_errors,
    }


def analyze_file(path: str) -> list[dict]:
    """Return list of per-case diagnostics from a trace file."""
    with open(path) as f:
        data = json.load(f)
    cases = _safe_list(data.get("cases", []))
    return [diagnose_trace(c) for c in cases if isinstance(c, dict)]


def summarize(diagnostics: list[dict]) -> dict:
    """Aggregate across cases."""
    n = len(diagnostics)
    if n == 0:
        return {"total": 0}
    correct = sum(1 for d in diagnostics if d["correct"])
    errors = sum(1 for d in diagnostics if d["error"])
    normal_wrong = n - correct - errors
    return {
        "total": n,
        "correct": correct,
        "accuracy": round(correct / n, 3),
        "normal_wrong": normal_wrong,
        "abnormal_error": errors,
        "mean_seconds": round(sum(d["seconds"] for d in diagnostics) / n, 0),
        "median_seconds": round(sorted(d["seconds"] for d in diagnostics)[n // 2], 0),
        "mean_total_verified": round(sum(d["total_verified_claims"] for d in diagnostics) / n, 1),
        "mean_first_verified_stage": round(
            sum(d["first_verified_stage"] or 99 for d in diagnostics) / n, 1
        ),
        "mean_max_consecutive_zero": round(
            sum(d["max_consecutive_zero_audit"] for d in diagnostics) / n, 1
        ),
        "mean_fetch_success_rate": round(
            sum(d["fetch_success_rate"] for d in diagnostics) / n, 2
        ),
        "cases_with_first_verified_s1": sum(1 for d in diagnostics if d["first_verified_stage"] == 1),
        "cases_with_any_verified": sum(1 for d in diagnostics if d["total_verified_claims"] > 0),
        "cases_verifier_accepted": sum(1 for d in diagnostics if d["verifier_accepted"]),
    }


def print_case(d: dict, idx: int = 0):
    """Pretty-print one case diagnostic."""
    print(f"\n{'='*80}")
    print(f"Case #{idx} | {'✅ CORRECT' if d['correct'] else '❌ WRONG'} | {d['seconds']}s | {d['stages_run']} stages")
    if d["error"]:
        print(f"  ⚠️  Error: {d['error']}")
    print(f"  Q: {d['question']}...")
    print(f"  Predicted: {d['predicted'][:100]}")
    print(f"  Expected:  {d['expected'][:100]}")
    print(f"\n  Knowledge Increment:")
    print(f"    First verified claim:    stage {d['first_verified_stage'] or 'NONE'}")
    print(f"    Total verified claims:   {d['total_verified_claims']}")
    print(f"    Max consecutive 0/0:     {d['max_consecutive_zero_audit']}")
    print(f"    Verified by stage:       {d['verified_by_stage']}")
    print(f"\n  Tool Health:")
    print(f"    Fetch success:           {d['fetch_successes']}/{d['fetch_attempts']} ({d['fetch_success_rate']:.0%})")
    print(f"    Browse with pages:       {d['browse_with_pages']}/{d['browse_attempts']}")
    if d["top_tool_errors"]:
        print(f"    Top tool errors:         {d['top_tool_errors']}")
    print(f"\n  Candidate Tracking:")
    print(f"    First named candidate:   stage {d['first_named_candidate_stage'] or 'NONE'}")
    print(f"    Candidate prunes:        {d['candidate_prune_count']}")
    print(f"    Verifier accepted:       {d['verifier_accepted']}")
    if d["verifier_reason"]:
        print(f"    Verifier reason:         {d['verifier_reason'][:150]}")
    if d["answer_condition_coverage"]:
        print(f"    Answer coverage:         {d['answer_condition_coverage']}")
    if d["solver_errors"]:
        print(f"    Solver errors:           {d['solver_errors']}")


def main():
    if len(sys.argv) < 2:
        paths = sorted(Path("eval_traces").glob("trace_browsecomp_*.json"))
    else:
        paths = [Path(p) for p in sys.argv[1:] if Path(p).exists()]
    if not paths:
        print("No trace files found.")
        return

    all_diags = []
    for path in paths:
        try:
            diags = analyze_file(str(path))
            all_diags.extend(diags)
        except Exception as exc:
            print(f"  SKIP {path.name}: {exc}")

    for i, d in enumerate(all_diags):
        print_case(d, i + 1)

    s = summarize(all_diags)
    print(f"\n{'='*80}")
    print(f"SUMMARY across {s['total']} cases:")
    print(f"  Accuracy:           {s['correct']}/{s['total']} = {s['accuracy']:.1%}")
    print(f"  Normal wrong:       {s['normal_wrong']}")
    print(f"  Abnormal errors:    {s['abnormal_error']}")
    print(f"  Mean time:          {s['mean_seconds']:.0f}s (median {s['median_seconds']:.0f}s)")
    print(f"  Mean total claims:  {s['mean_total_verified']}")
    print(f"  Mean 1st claim stg: {s['mean_first_verified_stage']}")
    print(f"  Cases claims in S1: {s['cases_with_first_verified_s1']}/{s['total']}")
    print(f"  Any verified:       {s['cases_with_any_verified']}/{s['total']}")
    print(f"  Mean max 0/0 run:   {s['mean_max_consecutive_zero']}")
    print(f"  Mean fetch success: {s['mean_fetch_success_rate']:.0%}")
    print(f"  Verifier accepted:  {s['cases_verifier_accepted']}/{s['total']}")


if __name__ == "__main__":
    main()
