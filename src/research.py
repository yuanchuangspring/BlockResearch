"""Stage-by-stage graph construction and execution loop."""
from .director import build_stage
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
            fallback = _candidate(plan.get("best_guess")) or fallback
            notebook.set_conditions(plan.get("conditions"))
            notebook.record_builder(stage, plan)
            if plan.get("decision") == "answer" and fallback and not plan.get("blocks"):
                notebook.answer = fallback
                return _result(f"ANSWER: {fallback}", stage, trace, all_outputs, notebook)
            blocks = normalize_graph(plan, stage)
            build_node = notebook.add_node("BUILD", {"stage": stage, "objective": plan.get("objective", ""), "blocks": blocks})
            print(f"\n🧱 S{stage}: {plan.get('objective', '')[:120]}")
            for block in blocks:
                print(f"  [{block['type']}] {block['id']}" + (f" ← {block['depends_on']}" if block["depends_on"] else ""))

            pre_claims, pre_inferences = len(notebook.claims), len(notebook.inferences)
            pre_leads, pre_hypotheses = len(notebook.leads), len(notebook.hypotheses)
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
            notebook.stage_summaries[-1]["new_derived_inferences"] = len(notebook.inferences) - pre_inferences
            notebook.record_actions(
                stage, plan, outputs,
                information_gain=(len(notebook.claims) - pre_claims) +
                                 (len(notebook.inferences) - pre_inferences) +
                                 max(0, len(notebook.hypotheses) - pre_hypotheses),
            )
            stage_trace = {"stage": stage, "build_node": build_node, "objective": plan.get("objective", ""),
                           "rationale": plan.get("rationale", ""), "nodes": nodes}

            guesses = [_candidate(value.get("best_guess") or value.get("answer_candidate"))
                       for value in outputs.values() if isinstance(value, dict)]
            fallback = next((guess for guess in reversed(guesses) if guess), fallback)
            accepted = next((value for value in outputs.values() if isinstance(value, dict)
                             and value.get("_type") == "VERIFY" and value.get("accepted")), None)
            if accepted:
                notebook.answer = accepted["candidate"]
                trace.append(stage_trace)
                return _result(f"ANSWER: {accepted['candidate']}", stage, trace, all_outputs, notebook)
            if plan.get("decision") == "answer" and fallback:
                notebook.answer = fallback
                trace.append(stage_trace)
                return _result(f"ANSWER: {fallback}", stage, trace, all_outputs, notebook)
            trace.append(stage_trace)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            trace.append({"stage": stage, "error": error})
            return _result("", stage, trace, all_outputs, notebook, error)

    answer = fallback or "NEEDS_EVIDENCE: no supported answer candidate"
    return _result(f"ANSWER: {answer}" if fallback else answer, max_stages, trace, all_outputs, notebook)
