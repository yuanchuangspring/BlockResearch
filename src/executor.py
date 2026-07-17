"""Execute a Builder-generated stage DAG with complete dependency flow."""
import asyncio, re, time

from .context import compact_source, useful
from .director import audit_evidence, solve_node
from .tools import browse, calculate, fetch_page, read_pdf, run_python

TOOLS = {"BROWSE", "FETCH", "READ_PDF", "CALCULATE", "PYTHON"}


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
        if kind not in TOOLS | {"SOLVE"} or old not in names:
            continue
        deps = [names[item] for item in block.get("depends_on", []) if item in names and item != old]
        blocks.append({"id": names[old], "type": kind, "params": block.get("params") or {}, "depends_on": deps})
    if not any(block["type"] == "SOLVE" for block in blocks):
        blocks.append({"id": f"s{stage}_solve", "type": "SOLVE",
                       "params": {"role": "domain expert", "task": "Synthesize the current evidence and identify the answer or exact remaining gap."},
                       "depends_on": [block["id"] for block in blocks]})
    depended_on = {dependency for block in blocks for dependency in block["depends_on"]}
    terminals = [block for block in blocks if block["id"] not in depended_on]
    if any(block["type"] != "SOLVE" for block in terminals):
        blocks.append({"id": f"s{stage}_synthesize", "type": "SOLVE",
                       "params": {"role": "domain expert", "task": "Synthesize every terminal observation into candidates, evidence coverage, and the exact remaining gaps."},
                       "depends_on": [block["id"] for block in terminals]})
    return blocks


def _dependency_queries(observations):
    queries = []
    for value in observations.values():
        if not isinstance(value, dict): continue
        items = value.get("queries", [])
        queries += items if isinstance(items, list) else [items]
    return list(dict.fromkeys(str(item).strip() for item in queries if str(item).strip()))[:16]


async def _tool(block, observations=None):
    p, kind = block["params"], block["type"]
    try:
        if kind == "BROWSE":
            queries = p.get("queries", p.get("query", []))
            queries = queries if isinstance(queries, list) else [queries]
            if p.get("queries_from_dependencies"):
                queries += _dependency_queries(observations or {})
            value = await browse(queries, p.get("n", 5),
                                 p.get("fetch_per_query", 1), p.get("search_terms", ""))
        elif kind == "FETCH": value = await fetch_page(p.get("url", ""), p.get("search", ""))
        elif kind == "READ_PDF": value = await read_pdf(p.get("url", ""), p.get("search", ""))
        elif kind == "CALCULATE": value = calculate(str(p.get("expression", "")))
        else: value = await run_python(str(p.get("code", "")))
        return {"_type": kind, **value}
    except Exception as exc:
        return {"_type": kind, "error": f"{type(exc).__name__}: {exc}"}


def _ancestors(block, by_id, outputs):
    found, stack = {}, list(block["depends_on"])
    while stack:
        node = stack.pop()
        if node in found: continue
        if node in outputs: found[node] = outputs[node]
        if node in by_id: stack.extend(by_id[node]["depends_on"])
    return found


async def execute_stage(question, plan, notebook, stage, build_node=None):
    blocks = normalize_graph(plan, stage)
    by_id, pending, outputs, trace, graph_ids = {b["id"]: b for b in blocks}, list(blocks), {}, [], {}
    while pending:
        ready = [b for b in pending if all(dep in outputs for dep in b["depends_on"])]
        if not ready:
            for block in pending:
                outputs[block["id"]] = {"error": "cyclic or unresolved dependency"}
            break

        tool_nodes = [block for block in ready if block["type"] in TOOLS]
        if tool_nodes:
            values = await asyncio.gather(*(_tool(block, _ancestors(block, by_id, outputs)) for block in tool_nodes))
            fresh = {}
            for block, value in zip(tool_nodes, values):
                outputs[block["id"]] = fresh[block["id"]] = value
                deps = [graph_ids[d] for d in block["depends_on"] if d in graph_ids] or ([build_node] if build_node else [])
                graph_ids[block["id"]] = notebook.add_node("TOOL", {"stage": stage, "block_id": block["id"], "tool": block["type"], "output": compact_source(value, 1800)}, deps)
                trace.append({**block, "output": compact_source(value, 3000)})
            auditable = {key: value for key, value in fresh.items() if useful(value)}
            if auditable:
                lead_ids = notebook.add_search_leads(auditable)
                started = time.monotonic()
                print(f"  [AUDIT] {', '.join(auditable)}", flush=True)
                audit = await audit_evidence(question, notebook, auditable)
                proposed = audit.get("claims") if isinstance(audit.get("claims"), list) else []
                audit_node = notebook.add_node("AUDIT", {"stage": stage, "seconds": round(time.monotonic() - started, 1),
                                               "proposed_claims": proposed[:20], "lead_ids": lead_ids},
                                               [graph_ids[key] for key in auditable])
                added = notebook.integrate(audit, outputs, audit_node, auditable)
                notebook.graph[-1]["accepted_claim_ids"] = added
                verified = sum(any(item["id"] == claim_id for item in notebook.claims) for claim_id in added)
                print(f"  [AUDIT ✓] {verified}/{len(proposed)} verified, {len(lead_ids)} leads | {time.monotonic() - started:.0f}s", flush=True)

        solve_nodes = [item for item in ready if item["type"] == "SOLVE"]
        for block in solve_nodes:
            print(f"  [SOLVE] {block['id']} ({block['params'].get('role', 'domain expert')})", flush=True)

        async def run_solver(block):
            observations = _ancestors(block, by_id, outputs)
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
            outputs[block["id"]] = value
            notebook.record_plan(value)
            deps = [graph_ids[d] for d in block["depends_on"] if d in graph_ids] or ([build_node] if build_node else [])
            graph_ids[block["id"]] = notebook.add_node("SOLVE", {"stage": stage, "block_id": block["id"], "output": compact_source(value, 2400)}, deps)
            trace.append({**block, "output": compact_source(value, 4000)})
            mark = "✗" if value.get("error") else "✓"
            print(f"  [SOLVE {mark}] {block['id']} | {value['seconds']:.0f}s", flush=True)
        done = {block["id"] for block in ready}
        pending = [block for block in pending if block["id"] not in done]
    return outputs, trace
