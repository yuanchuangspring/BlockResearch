"""Random-sample BrowseComp evaluation with reproducible full traces."""
import argparse, asyncio, base64, hashlib, json, os, re, time
from pathlib import Path

import pandas
from dotenv import load_dotenv

load_dotenv(".env")
from src.main import extract_answer, research
from src.llm import ask_json


GRADE_PROMPT = """Judge only whether PREDICTED and REFERENCE answer the QUESTION with the same entity or value.
Ignore harmless aliases, added disambiguating context, punctuation, and formatting. Reject contradictions, ambiguity, broader/narrower non-equivalent entities, and different values.
Return {"correct":true,"reason":"brief exact comparison"}."""


def norm(value):
    value = re.sub(r"[^\w\s]", "", str(value).strip().lower())
    return re.sub(r"\s+", " ", value).strip()


def decrypt(value, password):
    encrypted = base64.b64decode(value)
    digest = hashlib.sha256(password.encode()).digest()
    key = digest * (len(encrypted) // len(digest)) + digest[:len(encrypted) % len(digest)]
    return bytes(a ^ b for a, b in zip(encrypted, key)).decode()


def blind_metrics(result, expected):
    target = norm(expected)
    first_stage = None
    timings = {"search": 0.0, "fetch": 0.0, "other_tools": 0.0, "solver": 0.0, "auditor": 0.0}
    for stage in result.get("trace", []):
        for node in stage.get("nodes", []):
            output = node.get("output") or {}
            if first_stage is None and target and target in norm(json.dumps(output, ensure_ascii=False)):
                first_stage = stage.get("stage")
            seconds = float(output.get("_seconds", 0) or output.get("seconds", 0) or 0)
            kind = node.get("type", "")
            if kind == "SOLVE": timings["solver"] += seconds
            elif kind == "SEARCH": timings["search"] += seconds
            elif kind in {"FETCH", "BROWSE", "READ_PDF"}: timings["fetch"] += seconds
            else: timings["other_tools"] += seconds
    for node in (result.get("research_state") or {}).get("graph", []):
        if node.get("kind") == "AUDIT": timings["auditor"] += float(node.get("seconds", 0) or 0)
    return {"candidate_recalled": first_stage is not None, "first_recall_stage": first_stage,
            "stages": len(result.get("trace", [])),
            "timings": {key: round(value, 1) for key, value in timings.items()}}


async def grade(question, predicted, expected):
    if norm(predicted) == norm(expected):
        return {"correct": True, "reason": "normalized exact match"}
    if not predicted:
        return {"correct": False, "reason": "empty prediction"}
    try:
        return await ask_json(GRADE_PROMPT, f"QUESTION: {question}\nPREDICTED: {predicted}\nREFERENCE: {expected}",
                              os.getenv("EVAL_MODEL") or os.getenv("JUDGE_MODEL"), 1024)
    except Exception as exc:
        return {"correct": False, "reason": f"grader error: {exc}"}


async def run(limit=1, max_stages=8, seed=None, indices=None, blind=False):
    dataset_path = Path(os.getenv("BROWSECOMP_PATH", "data/browse_comp_test_set.csv"))
    if not dataset_path.exists():
        raise FileNotFoundError(
            f"BrowseComp dataset missing: {dataset_path}. Download the official encrypted CSV from "
            "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv "
            "or set BROWSECOMP_PATH."
        )
    dataset = pandas.read_csv(dataset_path)
    sample = dataset.loc[indices] if indices else dataset.sample(n=limit, random_state=seed)
    rows, correct = [], 0
    for number, (index, case) in enumerate(sample.iterrows(), 1):
        question = decrypt(case["problem"], case["canary"])
        expected = decrypt(case["answer"], case["canary"])
        started = time.time()
        try:
            result = await research(question, max_stages)
            predicted, error = extract_answer(result["answer"], question), result.get("error", "")
        except Exception as exc:
            result, predicted, error = {"trace": []}, "", f"{type(exc).__name__}: {exc}"
        exact = norm(predicted) == norm(expected)
        verdict = await grade(question, predicted, expected)
        passed = verdict.get("correct") is True
        correct += passed
        row = {"dataset_index": int(index), "question": question, "expected": expected,
               "predicted": predicted, "correct": passed, "exact_correct": exact,
               "grade_reason": verdict.get("reason", ""), "seconds": round(time.time() - started),
               "error": error, "research_trace": result.get("trace", []),
               "research_state": result.get("research_state", {})}
        row.update(blind_metrics(result, expected))
        rows.append(row)
        if blind:
            print(f"[{number}/{len(sample)}] {'✅' if passed else '❌'} | {row['seconds']}s | "
                  f"recall={row['candidate_recalled']}@S{row['first_recall_stage']} | "
                  f"{'error' if error else 'completed'}", flush=True)
        else:
            print(f"[{number}/{len(sample)}] {'✅' if passed else '❌'} {predicted!r} | expected {expected!r} | {row['seconds']}s", flush=True)

    saved_rows = ([{"dataset_index": row["dataset_index"], "correct": row["correct"], "seconds": row["seconds"],
                    "abnormal_error": bool(row["error"]), "candidate_recalled": row["candidate_recalled"],
                    "first_recall_stage": row["first_recall_stage"], "stages": row["stages"],
                    "timings": row["timings"]} for row in rows] if blind else rows)
    total = len(sample)
    output = {"correct": correct, "total": total, "accuracy": correct / total, "seed": seed,
              "blind": blind, "cases": saved_rows}
    Path("eval_traces").mkdir(exist_ok=True)
    kind = "blind" if blind else "fixed" if indices else "random"
    path = Path("eval_traces") / f"trace_browsecomp_{kind}_{time.strftime('%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"ACCURACY: {correct}/{total} = {correct / total:.0%}\nTRACE: {path}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-stages", type=int, default=8)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--indices", help="comma-separated frozen dataset row indices")
    parser.add_argument("--blind", action="store_true", help="save aggregate/non-content diagnostics only")
    args = parser.parse_args()
    indices = [int(value) for value in args.indices.split(",")] if args.indices else None
    asyncio.run(run(args.limit, args.max_stages, args.seed, indices, args.blind))
