"""Rendering and answer helpers shared across modules."""

import json
import re


def json_text(value, limit=60000):
    return json.dumps(value, ensure_ascii=False, default=str)[:limit]


def answer_value(text: str):
    for line in reversed(str(text).strip().splitlines()):
        match = re.search(r"(?:FINAL\s*)?(?:ANSWER|答案)\s*[：:]\s*(.+)", line, re.I)
        if match:
            return match.group(1).strip()
    return ""


def preview_one(block_id, value):
    if value.get("results"):
        return "\n".join(
            f"{block_id}[{i}]: {item.get('title', '')} | {item.get('url', '')} | {item.get('snippet', '')[:350]}"
            for i, item in enumerate(value["results"])
        )
    if value.get("search_hits"):
        hits = "\n".join(
            f"{item.get('page', item.get('term', 'hit'))}: {item['text'][:900]}"
            for item in value["search_hits"][:8]
        )
        return f"{block_id} hits:\n{hits}"
    if value.get("text"):
        return f"{block_id}: {value['text'][:1800].replace(chr(10), ' ')}"
    if "data" in value:
        return f"{block_id} data: {json_text(value['data'], 6000)}"
    if value.get("queries") or value.get("hypotheses"):
        return f"{block_id} specialist output: {json_text({key: value.get(key) for key in ('reasoning', 'hypotheses', 'queries', 'gaps')}, 9000)}"
    if value.get("reasoning"):
        return f"{block_id}: {value['reasoning'][-1200:]}"
    if "value" in value or "stdout" in value or "decision" in value:
        return f"{block_id}: {json_text(value, 4000)}"
    return f"{block_id}: ERROR {value.get('error', 'empty output')}"


def data_preview(outputs, limit=36000):
    rendered, size = [], 0
    for block_id, value in reversed(list(outputs.items())):
        if block_id == "question" or not isinstance(value, dict):
            continue
        item = preview_one(block_id, value)
        if size + len(item) <= limit:
            rendered.append(item)
            size += len(item)
    return "\n".join(reversed(rendered)) or "(no data)"


def evidence_gaps(outputs):
    gaps = []
    for value in outputs.values():
        if not isinstance(value, dict) or not value.get("reasoning"):
            continue
        for line in value["reasoning"].splitlines():
            if line.strip().upper().startswith("NEEDS_EVIDENCE:"):
                gaps.append(line.split(":", 1)[1].strip())
    return "\n".join(f"- {gap}" for gap in gaps[-6:]) or "(none)"


def useful(value):
    return bool(
        value.get("reasoning") or value.get("text") or value.get("results") or
        value.get("search_hits") or value.get("stdout") or "data" in value or
        "value" in value or "decision" in value
    )


def compact_source(value, text_limit=6000):
    """Keep every evidence type while bounding any one source's context share."""
    value = dict(value) if isinstance(value, dict) else {"value": value}
    if "text" in value:
        value["text"] = value["text"][:text_limit]
    if "links" in value:
        value["links"] = value["links"][:12]
    if "results" in value:
        value["results"] = value["results"][:16]
    if isinstance(value.get("pages"), list):
        value["pages"] = [
            compact_source(page, min(text_limit, 1800)) for page in value["pages"][:3]
        ]
    if "search_hits" in value:
        value["search_hits"] = [
            {**hit, "text": hit.get("text", "")[:1000]} for hit in value["search_hits"][:4]
        ]
    for field in ("first_page", "last_page", "stdout", "reasoning"):
        if field in value:
            value[field] = value[field][-2500:]
    return value


def final_evidence(outputs, limit=60000):
    evidence = {"sources": {}, "prior_analyses": {}}
    for key, value in reversed(list(outputs.items())):
        if key == "question" or not isinstance(value, dict) or value.get("error"):
            continue
        clipped = dict(value)
        if "text" in clipped:
            clipped["text"] = clipped["text"][:1000 if value.get("search_hits") else 4000]
        for field in ("first_page", "last_page", "stdout", "reasoning"):
            if field in clipped:
                clipped[field] = clipped[field][-2000:]
        if "links" in clipped:
            clipped["links"] = clipped["links"][:12]
        if "search_hits" in clipped:
            clipped["search_hits"] = [
                {**hit, "text": hit.get("text", "")[:2000]} for hit in clipped["search_hits"][:4]
            ]
        group = "prior_analyses" if value.get("reasoning") else "sources"
        candidate = {**evidence[group], key: clipped}
        if len(json_text({**evidence, group: candidate})) <= limit:
            evidence[group][key] = clipped
    return evidence
