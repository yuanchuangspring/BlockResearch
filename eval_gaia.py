"""Evaluate BlockResearch on deterministic attachment-free GAIA questions."""

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from datasets import load_dataset
from dotenv import load_dotenv

from src.main import extract_answer, research

load_dotenv(".env")


def norm(value):
    value = re.sub(r"[^\w\s]", "", str(value).strip().lower())
    return re.sub(r"\s+", " ", value).strip()


async def run(limit=10, max_stages=6):
    dataset = load_dataset("gaia-benchmark/GAIA", "2023_all", split="validation")
    cases = [x for x in dataset if not x.get("file_name")][:limit]
    Path("eval_traces").mkdir(exist_ok=True)
    trace_path = Path("eval_traces") / f"trace_gaia{limit}_{time.strftime('%m%d_%H%M%S')}.txt"
    correct, summary = 0, []

    with trace_path.open("w") as trace:
        for i, case in enumerate(cases, 1):
            started = time.time()
            try:
                result = await research(case["Question"], max_stages=max_stages)
                predicted = extract_answer(result["answer"], case["Question"])
                error = ""
            except Exception as exc:
                result, predicted, error = {"trace": []}, "", str(exc)
            expected = case["Final answer"]
            ok = norm(predicted) == norm(expected)
            correct += ok
            row = {
                "index": i, "task_id": case["task_id"], "level": case["Level"],
                "expected": expected, "predicted": predicted, "correct": ok,
                "seconds": round(time.time() - started), "error": error,
            }
            summary.append(row)
            print(f"[{i:02d}/{len(cases)}] {'✅' if ok else '❌'} L{case['Level']} {predicted!r} | expected {expected!r} | {row['seconds']}s", flush=True)
            trace.write(json.dumps({
                **row, "question": case["Question"], "research_trace": result.get("trace", [])
            }, ensure_ascii=False, indent=2) + "\n\n")
            trace.flush()

        accuracy = correct / len(cases) if cases else 0
        footer = {"correct": correct, "total": len(cases), "accuracy": accuracy, "cases": summary}
        trace.write(json.dumps(footer, ensure_ascii=False, indent=2))
    print(f"\nACCURACY: {correct}/{len(cases)} = {accuracy:.0%}")
    print(f"TRACE: {trace_path}")
    return footer


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-stages", type=int, default=6)
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.max_stages))
