"""Replay old BrowseComp cases against query planning and Brave retrieval only."""
import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from src.retrieval import plan_retrieval, refine_retrieval
from src.tools import _expand_queries, fetch_page, search_many


DEFAULT_TRACES = [
    "eval_traces/trace_browsecomp_fixed_0718_154344.json",
    "eval_traces/trace_browsecomp_fixed_0718_163057.json",
    "eval_traces/trace_browsecomp_random_0717_185303.json",
    "eval_traces/trace_browsecomp_random_0717_220649.json",
    "eval_traces/trace_browsecomp_fixed_0718_165623.json",
]

# Diagnostic-only intermediate hops from already inspected development cases.
# These never enter production prompts or isolated-blind evaluation.
KNOWN_BRIDGES = {
    650: ["class action", "fiscal third quarter", "three customers", "four customers"],
    20: ["September 2023", "online literary magazine", "unwanted delivery"],
    1090: ["thesis director", "Fulbright scholar", "speech impairment"],
    1079: ["Inter Allies", "Ghana Armed Forces", "Kotoka International Airport", "37 Military Hospital"],
    1193: ["Meryl Streep", "James Brown", "Hillary Clinton", "Grammy nomination"],
}


def _norm(value):
    return re.sub(r"\W+", " ", str(value).casefold()).strip()


def _case(path):
    data = json.loads(Path(path).read_text())
    return data["cases"][0]


def _historical_urls(case, targets):
    found = []
    def visit(value):
        if isinstance(value, dict):
            url = value.get("url")
            body = _norm(json.dumps(value, ensure_ascii=False))
            if url and any(_norm(target) in body for target in targets): found.append(str(url))
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(case.get("research_trace", []))
    return list(dict.fromkeys(found))[:5]


async def replay(case, fetch=False, rounds=1, plan_only=False):
    started = time.monotonic()
    plan = await plan_retrieval(case["question"])
    candidates = [str(x) for x in plan.get("candidates", [])][:10]
    bridges = [str(x) for x in plan.get("bridges", [])][:10]
    queries = _expand_queries([str(x) for x in plan.get("queries", [])][:12])
    result = {"results": []} if plan_only else await search_many(queries, 10)
    rows = result.get("results", [])
    all_candidates, all_bridges, all_queries, all_rows = list(candidates), list(bridges), list(queries), list(rows)
    pivots = []
    for _ in range(1, rounds if not plan_only else 1):
        refined = await refine_retrieval(case["question"], rows)
        pivots += refined.get("pivots", [])
        new_candidates = [str(x) for x in refined.get("candidates", [])][:10]
        new_bridges = [str(x) for x in refined.get("bridges", [])][:10]
        new_queries = [str(x) for x in refined.get("queries", []) if str(x) not in all_queries][:8]
        if not new_queries: break
        rows = (await search_many(new_queries, 10)).get("results", [])
        all_candidates += new_candidates
        all_bridges += new_bridges
        all_queries += new_queries
        known = {x.get("url", "").rstrip("/") for x in all_rows}
        all_rows += [x for x in rows if x.get("url", "").rstrip("/") not in known]
    target = _norm(case["expected"])
    seed_rank = next((i for i, value in enumerate(all_candidates, 1) if target in _norm(value)), None)
    result_rank = next((i for i, value in enumerate(all_rows, 1)
                        if target in _norm(json.dumps(value, ensure_ascii=False))), None)
    query_rank = min((value.get("rank", 999) for value in all_rows
                      if target in _norm(json.dumps(value, ensure_ascii=False))), default=None)
    known_bridges = KNOWN_BRIDGES.get(case.get("dataset_index"), [])
    discovery_text = _norm(json.dumps(all_candidates + all_bridges + all_rows, ensure_ascii=False))
    bridge_hits = [bridge for bridge in known_bridges if _norm(bridge) in discovery_text]
    overconstrained = [query for query in all_queries
                       if len(re.findall(r'"[^"]+"', query)) > 2 or len(re.findall(r"[A-Za-z0-9]+", query)) > 18]
    fetched = []
    if fetch:
        targets = [case["expected"], *known_bridges]
        matches = [row for row in all_rows if any(_norm(value) in _norm(json.dumps(row, ensure_ascii=False)) for value in targets)][:5]
        urls = [row["url"] for row in matches] or _historical_urls(case, targets)
        pages = await asyncio.gather(*(fetch_page(url, " ".join(targets)) for url in urls))
        fetched = [{"url": page.get("url", ""), "ok": target in _norm(page.get("text", "")),
                    "bridge_hits": [value for value in known_bridges if _norm(value) in _norm(page.get("text", ""))],
                    "error": bool(page.get("error"))} for page in pages]
    return {
        "dataset_index": case.get("dataset_index"), "expected": case["expected"],
        "candidate_seed_rank": seed_rank, "serp_global_rank": result_rank,
        "serp_query_rank": query_rank, "queries": all_queries, "candidate_seeds": all_candidates,
        "bridge_seeds": all_bridges, "bridge_hits": bridge_hits,
        "pivots": pivots,
        "bridge_recall": round(len(bridge_hits) / max(len(known_bridges), 1), 2),
        "overconstrained_queries": overconstrained,
        "result_count": len(all_rows), "query_echoes": sum(bool(x.get("query_echo")) for x in all_rows),
        "search_errors": result.get("search_errors", []),
        "fetched": fetched, "seconds": round(time.monotonic() - started, 1),
    }


async def main(paths, fetch, rounds, plan_only=False):
    reports = []
    for path in paths:
        try:
            report = await replay(_case(path), fetch, rounds, plan_only)
        except Exception as exc:
            case = _case(path)
            report = {"dataset_index": case.get("dataset_index"), "expected": case.get("expected"),
                      "error": f"{type(exc).__name__}: {exc}"}
        reports.append(report)
        rank = report.get("serp_global_rank") or report.get("candidate_seed_rank") or "MISS"
        print(f'{report["dataset_index"]}: {report["expected"]!r} rank={rank} '
              f'echo={report.get("query_echoes", 0)}/{report.get("result_count", 0)} '
              f'{report.get("seconds", "ERR")}s {report.get("error", "")}', flush=True)
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("traces", nargs="*", default=DEFAULT_TRACES)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--rounds", type=int, default=1, help="extra refinement is diagnostic only")
    parser.add_argument("--plan-only", action="store_true", help="test candidates, bridges and query shape without calling Brave")
    args = parser.parse_args()
    rounds = min(max(args.rounds, 1), 3)
    asyncio.run(main(args.traces, args.fetch, rounds, args.plan_only))
