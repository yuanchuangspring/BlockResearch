"""High-recall query planning kept separate from evidence reasoning."""
import asyncio, os
from openai import APIConnectionError, APITimeoutError

from .context import json_text
from .llm import ask_json


RECALL_PROMPT = """You are the retrieval planner for a deep-research agent. Maximize recall of named answer candidates and named intermediate entities without treating either as evidence.

Model the question as a small relation graph. Choose up to three independent entry edges with source-indexable wording. For each entry edge, issue short queries that can retrieve a concrete node. Do not force a whole multi-edge path into one query. Do not guess unknown node values merely to complete a path. Keep alternate branches when a relation is existential or ambiguous.

Queries must be diverse across graph edges, normally contain one quoted phrase or at most two relations, and stay under 12 meaningful terms. Search source vocabulary rather than benchmark-derived calculations. Return 4-10 candidate seeds, 4-10 intermediate seeds, and 8-12 queries. Also return an ephemeral route ledger recording, for each entry edge, what node type it seeks and which edge would remain after that node is retrieved.

Every candidates/bridges/queries item is a plain string.
Return {"candidates":[],"bridges":[],"queries":[],"routes":[{"anchor":"source-indexable relation","bridge_kind":"entity type","missing_relation":"next graph edge"}]}."""


def _strings(values, limit):
    items = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, dict):
            value = value.get("name") or value.get("title") or ""
        value = str(value).strip()
        if value: items.append(value)
    return list(dict.fromkeys(items))[:limit]


async def _plan_json(prompt, user, model, tokens):
    for attempt in range(1, 4):
        try:
            return await ask_json(prompt, user, model, tokens)
        except (APIConnectionError, APITimeoutError, TimeoutError, ConnectionError) as exc:
            action = "retrying GPT" if attempt < 3 else "switching to deepseek-v4-pro"
            print(f"[RETRIEVAL RETRY] attempt {attempt}/3 failed: {type(exc).__name__}; {action}", flush=True)
            if attempt < 3: await asyncio.sleep(attempt)
    return await ask_json(prompt, user, os.environ.get("FALLBACK_SOLVER_MODEL", "deepseek-v4-pro"), tokens)


async def plan_retrieval(question, model=None):
    value = await _plan_json(RECALL_PROMPT, question,
                             model or os.environ.get("SOLVER_MODEL", "gpt-5.5"), 1536)
    routes = [item for item in value.get("routes", []) if isinstance(item, dict)][:6]
    return {"candidates": _strings(value.get("candidates"), 10),
            "bridges": _strings(value.get("bridges"), 10),
            "queries": _strings(value.get("queries"), 12), "routes": routes}


REFINE_PROMPT = """Expand a research graph from one round of web result cards.

Reject query echoes and pages that contain no concrete node. Copy 2-6 useful named nodes from the cards. For each, retain the query that reached it and identify one unresolved edge on a path toward the requested answer. Generate 5-8 new short queries: normally an exact retrieved node plus that unresolved relation. If no useful node was retrieved on a route, change its entry edge instead of paraphrasing the failed query.

Do not guess missing node values, repeat queries, or concatenate the full question. A node is an answer candidate only when a card connects it to the requested answer relation; otherwise it remains an intermediate pivot.
Return {"candidates":[],"bridges":[],"pivots":[{"entity":"exact retrieved node","source_query":"...","missing_relation":"..."}],"queries":[]}."""


def _compact_cards(results, per_query=3):
    """Keep every search route visible instead of truncating later routes."""
    grouped, order = {}, []
    for row in results:
        if not isinstance(row, dict):
            continue
        query = str(row.get("query", ""))
        if query not in grouped:
            grouped[query] = []
            order.append(query)
        if len(grouped[query]) < per_query:
            grouped[query].append({"title": str(row.get("title", ""))[:240],
                                   "snippet": str(row.get("snippet", ""))[:600],
                                   "url": str(row.get("url", "")),
                                   "rank": row.get("rank")})
    return [{"query": query, "results": grouped[query]} for query in order]


async def refine_retrieval(question, results, model=None):
    user = f"QUESTION:\n{question}\n\nBRAVE RESULTS GROUPED BY ROUTE:\n{json_text(_compact_cards(results), 30000)}"
    value = await _plan_json(REFINE_PROMPT, user,
                             model or os.environ.get("SOLVER_MODEL", "gpt-5.5"), 1536)
    pivots = [item for item in value.get("pivots", []) if isinstance(item, dict)
              and str(item.get("entity", "")).strip()][:8]
    return {"candidates": _strings(value.get("candidates"), 10),
            "bridges": _strings(value.get("bridges"), 10),
            "queries": _strings(value.get("queries"), 8), "pivots": pivots}
