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


async def run(limit=1, max_stages=8, seed=None):
    sample = pandas.read_csv("/tmp/browsecomp.csv").sample(n=limit, random_state=seed)
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
        rows.append(row)
        print(f"[{number}/{limit}] {'✅' if passed else '❌'} {predicted!r} | expected {expected!r} | {row['seconds']}s", flush=True)

    output = {"correct": correct, "total": limit, "accuracy": correct / limit, "seed": seed, "cases": rows}
    Path("eval_traces").mkdir(exist_ok=True)
    path = Path("eval_traces") / f"trace_browsecomp_random_{time.strftime('%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"ACCURACY: {correct}/{limit} = {correct / limit:.0%}\nTRACE: {path}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-stages", type=int, default=8)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.max_stages, args.seed))
