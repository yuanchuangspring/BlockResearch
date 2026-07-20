"""Execute a Builder-generated stage DAG with complete dependency flow."""
import asyncio, re, time

from .context import compact_source, useful
from .director import solve_node, verify_answer
from .tools import browse, search_many, calculate, fetch_page, read_pdf, run_python
from .recorder import record

TOOLS = {"SEARCH", "BROWSE", "FETCH", "READ_PDF", "CALCULATE", "PYTHON"}


def normalize_graph(plan, stage):
    raw = plan.get("blocks") if isinstance(plan, dict) else []
    raw = raw if isinstance(raw, list) else []
    raw = [block for block in raw if isinstance(block, dict)][:8]
    names, blocks = {}, []
    for i, block in enumerate(raw, 1):
        old = str(block.get("id") or f"b{i}")
        names[old] = f"s{stage}_{re.sub(r'[^a-zA-Z0-9_]+', '_', old)}"
    for block in raw:
        kind, old = str(block.get("type", "")).upper(), str(block.get("id") or "")
        if kind not in TOOLS | {"SOLVE", "VERIFY"} or old not in names:
            continue
        deps = [names[item] for item in block.get("depends_on", []) if item in names and item != old]
        blocks.append({"id": names[old], "type": kind, "params": block.get("params") or {}, "depends_on": deps})
    return blocks


def _dependency_queries(observations):
    queries = []
    for value in observations.values():
        if not isinstance(value, dict): continue
        items = value.get("queries", [])
        queries += items if isinstance(items, list) else [items]
    return list(dict.fromkeys(str(item).strip() for item in queries if str(item).strip()))[:16]


def _dependency_urls(observations, auto_select=False):
    proposed, allowed = [], set()
    for value in observations.values():
        if not isinstance(value, dict): continue
        urls = value.get("urls", [])
        proposed += urls if isinstance(urls, list) else [urls]
        results = [item for item in value.get("results", [])
                   if isinstance(item, dict) and not item.get("query_echo")]
        allowed |= {str(item.get("url", "")) for item in results}
        if auto_select:
            proposed += [item.get("url", "") for item in results]
        allowed |= {str(item.get("url", "")) for item in value.get("pages", []) if isinstance(item, dict)}
    chosen, domains = [], set()
    for url in dict.fromkeys(str(url).strip() for url in proposed if str(url).strip() in allowed):
        domain = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/", 1)[0].lower())
        if domain and domain in domains: continue
        chosen.append(url); domains.add(domain)
    return chosen[:12]


def _rank_stratified(urls, limit):
    """Keep leading results while reserving slots for deeper candidate recall."""
    urls = list(dict.fromkeys(urls))
    if len(urls) <= limit:
        return urls
    head = urls[:min(3, limit)]
    remaining = limit - len(head)
    if remaining <= 0:
        return head
    tail = urls[len(head):]
    positions = [round(i * (len(tail) - 1) / max(remaining - 1, 1)) for i in range(remaining)]
    return head + [tail[i] for i in positions]


async def _tool(block, observations=None):
    p, kind = block["params"], block["type"]
    started = time.monotonic()
    record("tool_request", block_id=block.get("id"), tool=kind, params=p,
           dependency_observations=observations or {})
    try:
        if kind == "SEARCH":
            queries = p.get("queries", p.get("query", []))
            queries = queries if isinstance(queries, list) else [queries]
            if p.get("queries_from_dependencies"):
                queries += _dependency_queries(observations or {})
            value = await search_many(queries, p.get("n", 5))
        elif kind == "BROWSE":
            queries = p.get("queries", p.get("query", []))
            queries = queries if isinstance(queries, list) else [queries]
            if p.get("queries_from_dependencies"):
                queries += _dependency_queries(observations or {})
            value = await browse(queries, p.get("n", 5),
                                 p.get("fetch_per_query", 1), p.get("search_terms", ""))
        elif kind == "FETCH":
            urls = _dependency_urls(observations or {}, p.get("auto_select", False)) if p.get("urls_from_dependencies") else []
            if not urls:
                raw = p.get("urls", [p.get("url", "")])
                urls = raw if isinstance(raw, list) else [raw]
            limit = min(max(int(p.get("max_urls", 5)), 1), 8)
            urls = _rank_stratified([url for url in urls if url], limit) if p.get("auto_select") else [url for url in urls if url][:limit]
            pages = await asyncio.gather(*(fetch_page(url, p.get("search", "")) for url in urls))
            value = pages[0] if len(pages) == 1 else {"pages": pages, "urls": urls}
        elif kind == "READ_PDF": value = await read_pdf(p.get("url", ""), p.get("search", ""))
        elif kind == "CALCULATE": value = calculate(str(p.get("expression", "")))
        else: value = await run_python(str(p.get("code", "")), observations or {})
        result = {"_type": kind, "_branch_id": str(p.get("branch_id", "")).strip(), **value}
        record("tool_response", block_id=block.get("id"), tool=kind,
               seconds=round(time.monotonic() - started, 3), output=result)
        return result
    except Exception as exc:
        result = {"_type": kind, "error": f"{type(exc).__name__}: {exc}"}
        record("tool_response", block_id=block.get("id"), tool=kind,
               seconds=round(time.monotonic() - started, 3), output=result)
        return result


def _ancestors(block, by_id, outputs):
    found, stack = {}, list(block["depends_on"])
    while stack:
        node = stack.pop()
        if node in found: continue
        if node in outputs: found[node] = outputs[node]
        if node in by_id: stack.extend(by_id[node]["depends_on"])
    return found


def _direct_dependencies(block, outputs):
    """A node consumes its declared inputs; upstream transport is already consumed."""
    return {node: outputs[node] for node in block["depends_on"] if node in outputs}


async def execute_stage(question, plan, notebook, stage, build_node=None):
    blocks = normalize_graph(plan, stage)
    record("stage_graph", stage=stage, question=question, builder_output=plan, normalized_blocks=blocks)
    by_id, pending, outputs, trace, graph_ids = {b["id"]: b for b in blocks}, list(blocks), {}, [], {}
    while pending:
        ready = [b for b in pending if all(dep in outputs for dep in b["depends_on"])]
        if not ready:
            for block in pending:
                outputs[block["id"]] = {"error": "cyclic or unresolved dependency"}
            break

        tool_nodes = [block for block in ready if block["type"] in TOOLS]
        if tool_nodes:
            async def timed_tool(block):
                started = time.monotonic()
                value = await _tool(block, _direct_dependencies(block, outputs))
                value["_seconds"] = round(time.monotonic() - started, 2)
                return value
            values = await asyncio.gather(*(timed_tool(block) for block in tool_nodes))
            fresh = {}
            for block, value in zip(tool_nodes, values):
                outputs[block["id"]] = fresh[block["id"]] = value
                deps = [graph_ids[d] for d in block["depends_on"] if d in graph_ids] or ([build_node] if build_node else [])
                graph_ids[block["id"]] = notebook.add_node("TOOL", {"stage": stage, "block_id": block["id"], "tool": block["type"], "output": compact_source(value, 1800)}, deps)
                trace.append({**block, "output": compact_source(value, 3000)})
            for key, value in fresh.items():
                if value.get("_type") == "SEARCH":
                    notebook.add_search_leads({key: value})
            notebook.store_evidence(stage, fresh)
            auditable = {key: value for key, value in fresh.items()
                         if useful(value) and value.get("_type") != "SEARCH"}
            if auditable:
                notebook.add_search_leads(auditable)

        solve_nodes = [item for item in ready if item["type"] == "SOLVE"]
        for block in solve_nodes:
            print(f"  [SOLVE] {block['id']} ({block['params'].get('role', 'domain expert')})", flush=True)
            record("block_status", stage=stage, block_id=block["id"], block_type="SOLVE",
                   status="running", params=block["params"])

        async def run_solver(block):
            observations = _direct_dependencies(block, outputs)
            p = block["params"]
            started = time.monotonic()
            try:
                value = await solve_node(question, p.get("task", "Synthesize evidence"), p.get("role", "domain expert"), notebook, observations)
            except Exception as exc:
                value = {"error": f"{type(exc).__name__}: {exc}", "reasoning": "",
                         "queries": [], "hypotheses": [], "gaps": ["Solver node failed"],
                         "answer_candidate": "", "support_claim_ids": []}
            value["seconds"] = round(time.monotonic() - started, 1)
            return block, value

        solved = await asyncio.gather(*(run_solver(block) for block in solve_nodes))
        for block, value in solved:
            # Translate the adviser's deliberately small contract into the
            # notebook's stable internal representation.
            value["reasoning"] = value.get("memo", value.get("reasoning", ""))
            value["answer_candidate"] = value.get("best_guess", value.get("answer_candidate", ""))
            value["gaps"] = [value["decisive_gap"]] if value.get("decisive_gap") else value.get("gaps", [])
            notebook.record_candidates(stage, value)
            if value.get("candidates") and not value.get("hypotheses"):
                value["hypotheses"] = [
                    {"entity": item.get("name", ""), "coverage": [],
                     "rejected_reason": item.get("why", "") if item.get("status") == "contradicted" else ""}
                    for item in value["candidates"] if isinstance(item, dict)
                ]
            outputs[block["id"]] = value
            deps = [graph_ids[d] for d in block["depends_on"] if d in graph_ids] or ([build_node] if build_node else [])
            graph_ids[block["id"]] = notebook.add_node("SOLVE", {"stage": stage, "block_id": block["id"], "output": compact_source(value, 2400)}, deps)
            observations = _direct_dependencies(block, outputs)
            allowed = {key for key, item in observations.items()
                       if isinstance(item, dict) and item.get("_type") not in {None, "SEARCH"}}
            added = notebook.integrate(value, observations, graph_ids[block["id"]], allowed)
            value["support_claim_ids"] = list(dict.fromkeys((value.get("support_claim_ids") or []) + added))
            notebook.record_plan(value)
            trace.append({**block, "output": compact_source(value, 4000)})
            mark = "✗" if value.get("error") else "✓"
            error = f" | {value['error'][:160]}" if value.get("error") else ""
            fallback = " | fallback=deepseek-v4-pro" if value.get("degraded_model") else ""
            print(f"  [SOLVE {mark}] {block['id']} | {value['seconds']:.0f}s{fallback}{error}", flush=True)
            record("block_status", stage=stage, block_id=block["id"], block_type="SOLVE",
                   status="error" if value.get("error") else "ok",
                   seconds=value["seconds"], output=compact_source(value, 4000))

        verify_nodes = [item for item in ready if item["type"] == "VERIFY"]
        for block in verify_nodes:
            record("block_status", stage=stage, block_id=block["id"], block_type="VERIFY",
                   status="running", params=block["params"])
            observations = _direct_dependencies(block, outputs)
            candidate = str(block["params"].get("candidate", "")).strip()
            placeholder = bool(re.search(r"\b(?:best|candidate|dependency|solver|above|previous)\b", candidate, re.I))
            if block["params"].get("candidate_from_dependencies") or not candidate or placeholder:
                candidate = next((str(value.get("best_guess") or value.get("answer_candidate") or "").strip()
                                  for value in reversed(list(observations.values())) if isinstance(value, dict)
                                  and (value.get("best_guess") or value.get("answer_candidate"))), "")
            started = time.monotonic()
            if candidate:
                sources = {key: compact_source(value, 2500) for key, value in observations.items()
                           if isinstance(value, dict) and value.get("_type") in TOOLS}
                source_ids = {item.get("source_block_id") or item.get("source_id")
                              for item in notebook.claims}
                for passage in notebook.source_excerpts(source_ids):
                    key = passage.get("source_block_id")
                    sources.setdefault(key, {"_type": "EVIDENCE", "passages": []})
                    sources[key]["passages"].append(passage)
                value = await verify_answer(question, candidate, notebook.claims, sources,
                                            notebook.conditions, notebook.hypotheses, notebook.inferences)
            else:
                value = {"accepted": False, "reason": "no candidate supplied"}
            value.update({"_type": "VERIFY", "candidate": candidate,
                          "seconds": round(time.monotonic() - started, 1)})
            notebook.record_verification(stage, candidate, value)
            outputs[block["id"]] = value
            deps = [graph_ids[d] for d in block["depends_on"] if d in graph_ids] or ([build_node] if build_node else [])
            graph_ids[block["id"]] = notebook.add_node("VERIFY", {"stage": stage, **value}, deps)
            trace.append({**block, "output": value})
            print(f"  [VERIFY {'✓' if value.get('accepted') else '✗'}] {candidate} | {value['seconds']:.0f}s", flush=True)
            record("block_status", stage=stage, block_id=block["id"], block_type="VERIFY",
                   status="ok" if value.get("accepted") else "error",
                   seconds=value["seconds"], output=value)
        done = {block["id"] for block in ready}
        pending = [block for block in pending if block["id"] not in done]
    return outputs, trace
