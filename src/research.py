"""Stage-by-stage graph construction and execution loop."""
from .context import compact_source
from .director import build_stage, verify_answer
from .executor import execute_stage, normalize_graph
from .notebook import ResearchNotebook


def _result(answer, stages, trace, outputs, notebook, error=""):
    return {"answer": answer, "stages": stages, "trace": trace, "outputs": outputs,
            "research_state": notebook.to_dict(), "error": error}


def _candidate(value):
    candidate = str(value or "").strip()
    return "" if candidate.lower() in {"none", "unknown", "no match", "no match found", "not found", "n/a"} else candidate


def _stage_stats(outputs, nodes):
    """Count tool successes and failures from stage outputs."""
    successful, failed = 0, 0
    for node in nodes:
        output = node.get("output") or {}
        ntype = node.get("type", "")
        if ntype in ("FETCH", "READ_PDF", "BROWSE"):
            if output.get("error"):
                failed += 1
            elif ntype in ("FETCH", "READ_PDF") and output.get("text") and len(str(output.get("text", ""))) >= 100:
                successful += 1
            elif ntype == "BROWSE":
                pages = output.get("pages", []) if isinstance(output.get("pages"), list) else []
                if any(isinstance(p, dict) and len(str(p.get("text", ""))) >= 100 for p in pages):
                    successful += 1
    return successful, failed


async def research(question: str, max_stages: int = 8) -> dict:
    notebook, trace, all_outputs, fallback = ResearchNotebook(), [], {}, ""
    for stage in range(1, max_stages + 1):
        try:
            plan = await build_stage(question, notebook, stage, max_stages)
            notebook.set_conditions(plan.get("conditions"))
            blocks = normalize_graph(plan, stage)
            build_node = notebook.add_node("BUILD", {"stage": stage, "objective": plan.get("objective", ""), "blocks": blocks})
            print(f"\n🧱 S{stage}: {plan.get('objective', '')[:120]}")
            for block in blocks:
                print(f"  [{block['type']}] {block['id']}" + (f" ← {block['depends_on']}" if block["depends_on"] else ""))

            pre_claims, pre_leads, pre_hypotheses = len(notebook.claims), len(notebook.leads), len(notebook.hypotheses)
            outputs, nodes = await execute_stage(question, plan, notebook, stage, build_node)
            all_outputs.update(outputs)
            n_successful, n_failed = _stage_stats(outputs, nodes)
            notebook.record_stage_summary(
                stage,
                new_verified=len(notebook.claims) - pre_claims,
                new_leads=len(notebook.leads) - pre_leads,
                successful_pages=n_successful,
                failed_fetches=n_failed,
                candidate_changes=len(notebook.hypotheses) - pre_hypotheses,
            )
            stage_trace = {"stage": stage, "build_node": build_node, "objective": plan.get("objective", ""),
                           "rationale": plan.get("rationale", ""), "nodes": nodes}

            candidates = [value for value in outputs.values() if isinstance(value, dict) and _candidate(value.get("answer_candidate"))]
            rejected_this_stage = []
            for solver in reversed(candidates):
                candidate, ids = _candidate(solver["answer_candidate"]), set(solver.get("support_claim_ids") or [])
                fallback = candidate or fallback
                claims = [claim for claim in notebook.claims if claim["id"] in ids]
                sources = {
                    claim["source_id"]: compact_source(all_outputs.get(claim.get("source_block_id", claim["source_id"]), {}), 3000)
                    for claim in claims
                }
                verdict = await verify_answer(question, candidate, claims, sources, notebook.conditions, notebook.hypotheses)
                verify_node = notebook.add_node("VERIFY", {"stage": stage, "candidate": candidate, **verdict})
                stage_trace["verification"] = {"node": verify_node, "candidate": candidate, **verdict}
                if verdict.get("accepted"):
                    notebook.answer = candidate
                    trace.append(stage_trace)
                    return _result(f"ANSWER: {candidate}", stage, trace, all_outputs, notebook)
                reason = str(verdict.get("reason", "verification rejected"))
                notebook.reject_answer(candidate, reason)
                rejected_this_stage.append(candidate)
                if fallback == candidate:
                    fallback = ""
                notebook.questions = (notebook.questions + [reason])[-12:]
            if rejected_this_stage and notebook.stage_summaries:
                notebook.stage_summaries[-1]["verifier_rejected"] = rejected_this_stage
            trace.append(stage_trace)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            trace.append({"stage": stage, "error": error})
            return _result("", stage, trace, all_outputs, notebook, error)

    answer = fallback or "NEEDS_EVIDENCE: no supported answer candidate"
    return _result(f"ANSWER: {answer}" if fallback else answer, max_stages, trace, all_outputs, notebook)
