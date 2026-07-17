"""Persistent epistemic memory and the incrementally built research graph."""

import json
import re


def _text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _items(value):
    return value if isinstance(value, list) else []


class ResearchNotebook:
    def __init__(self):
        self.claims, self.leads = [], []
        self.hypotheses, self.questions, self.reasoning, self.rejected_answers = [], [], [], []
        self.conditions, self.entities, self.sources = [], {}, {}
        self.graph, self.answer = [], ""

    def add_node(self, kind, payload, depends_on=()):
        node_id = f"n{len(self.graph) + 1}"
        self.graph.append({"id": node_id, "kind": kind, "depends_on": list(depends_on), **payload})
        return node_id

    def _claim_id(self):
        numbers = [int(item["id"][1:]) for item in self.claims + self.leads if str(item.get("id", "")).startswith("c")]
        return f"c{max(numbers, default=0) + 1}"

    def set_conditions(self, conditions):
        if self.conditions or not isinstance(conditions, list):
            return
        for i, item in enumerate(conditions[:16], 1):
            if not isinstance(item, dict): continue
            description = _text(item.get("description"))
            if description:
                self.conditions.append({"id": _text(item.get("id")) or f"k{i}", "description": description})

    @staticmethod
    def _support_level(claim, output):
        quote = _text(claim.get("quote")).lower()
        if not quote:
            return ""
        if output.get("_type") == "BROWSE":
            groups = (("verified", output.get("pages", [])), ("lead", output.get("results", [])))
        elif output.get("_type") == "SEARCH":
            groups = (("lead", output.get("results", [])),)
        else:
            groups = (("verified", [output]),)
        quote_tokens = set(re.findall(r"[a-z0-9]+", quote))
        for level, documents in groups:
            for document in _items(documents):
                if not isinstance(document, dict):
                    continue
                body = _text(json.dumps(document, ensure_ascii=False, default=str)).lower()
                overlap = len(quote_tokens & set(re.findall(r"[a-z0-9]+", body))) / max(len(quote_tokens), 1)
                if quote in body or (len(quote_tokens) >= 5 and overlap >= .8):
                    return level
        return ""

    @staticmethod
    def _resolve_source(source, outputs, allowed_sources):
        """Resolve an auditor URL or block id to the exact fetched document."""
        if source in outputs and source in allowed_sources:
            return source, outputs[source]
        normalized = source.rstrip("/")
        for block_id in allowed_sources:
            output = outputs.get(block_id, {})
            documents = [output] + _items(output.get("pages"))
            for document in documents:
                if isinstance(document, dict) and str(document.get("url", "")).rstrip("/") == normalized:
                    return block_id, {"_type": "FETCH", **document}
        return "", {}

    def integrate(self, audit, outputs, audit_node, allowed_sources=None):
        verified = {_text(item.get("claim")).lower() for item in self.claims}
        leads = {_text(item.get("claim")).lower(): item for item in self.leads}
        added = []
        allowed_sources = set(allowed_sources or outputs)
        for item in _items(audit.get("claims")):
            if not isinstance(item, dict):
                continue
            source = str(item.get("source_id", ""))
            source_block, output = self._resolve_source(source, outputs, allowed_sources)
            if not source_block:
                continue
            source_id = source if source != source_block else source_block
            self.sources.setdefault(source_id, {"type": output.get("_type", "SOURCE"),
                                                 "urls": [output.get("url", "")] if output.get("url") else []})
            level = self._support_level(item, output)
            claim = _text(item.get("claim"))
            key = claim.lower()
            if not level or not claim or key in verified:
                continue
            record = {
                "id": self._claim_id(), "claim": claim,
                "quote": _text(item.get("quote")), "source_id": source_id,
                "source_block_id": source_block, "level": level,
                "entities": [_text(x) for x in _items(item.get("entities")) if _text(x)],
                "condition_ids": [],
            }
            valid_conditions = {condition["id"] for condition in self.conditions}
            for value in _items(item.get("condition_ids")):
                condition = _text(value)
                condition = condition if condition in valid_conditions else f"k{condition}"
                if condition in valid_conditions and condition not in record["condition_ids"]:
                    record["condition_ids"].append(condition)
            for entity in record["entities"]:
                self.entities.setdefault(entity.lower(), {"name": entity, "aliases": []})
            if level == "verified" and key in leads:
                self.leads.remove(leads[key])
            elif key in leads:
                continue
            (self.claims if level == "verified" else self.leads).append(record)
            verified.add(key) if level == "verified" else leads.update({key: record})
            added.append(record["id"])
        if added:
            self.graph[-1]["claim_ids"] = added
        return added

    def add_search_leads(self, outputs):
        """Search results are useful leads by construction, never verified evidence."""
        known = {(item.get("url"), _text(item.get("claim")).lower()) for item in self.leads}
        added = []
        for source, output in outputs.items():
            if output.get("_type") != "BROWSE":
                continue
            self.sources[source] = {"type": "BROWSE", "urls": [item.get("url", "") for item in _items(output.get("results"))[:20]]}
            for item in _items(output.get("results"))[:12]:
                title, snippet, url = _text(item.get("title")), _text(item.get("snippet")), str(item.get("url", ""))
                claim = _text(f"{title} — {snippet}")
                key = (url, claim.lower())
                if not claim or key in known:
                    continue
                record = {"id": self._claim_id(), "claim": claim, "quote": snippet,
                          "source_id": source, "url": url, "query": _text(item.get("query")), "level": "lead"}
                self.leads.append(record)
                known.add(key)
                added.append(record["id"])
        return added

    def record_plan(self, plan):
        analysis = _text(plan.get("reasoning") or plan.get("analysis"))
        if analysis:
            self.reasoning.append(analysis)
            self.reasoning = self.reasoning[-4:]
        if isinstance(plan.get("hypotheses"), list):
            merged = {_text(item.get("entity")).lower(): item for item in self.hypotheses if isinstance(item, dict) and _text(item.get("entity"))}
            for item in plan["hypotheses"]:
                entity = _text(item.get("entity")) if isinstance(item, dict) else ""
                if not entity or re.search(r"\b(?:unknown|unidentified|unspecified)\b", entity, re.I): continue
                aliases = [_text(x) for x in _items(item.get("aliases")) if _text(x)]
                names = {entity.lower(), *(alias.lower() for alias in aliases)}
                canonical = next((key for key, old in merged.items()
                                  if names & {key, *(_text(alias).lower() for alias in _items(old.get("aliases")))}),
                                 entity.lower())
                evidence = {x["id"]: x for x in self.claims + self.leads}
                coverage = []
                for cover in _items(item.get("coverage")):
                    if not isinstance(cover, dict) or not _text(cover.get("condition_id")): continue
                    condition_id = _text(cover["condition_id"])
                    ids = [x for x in _items(cover.get("evidence_ids")) if x in evidence]
                    verified = any(evidence[x]["level"] == "verified" and condition_id in evidence[x].get("condition_ids", []) for x in ids)
                    status = "verified" if verified else "lead" if ids else "unknown"
                    if cover.get("status") == "contradicted": status = "contradicted"
                    coverage.append({"condition_id": condition_id, "status": status, "evidence_ids": ids})
                current = merged.get(canonical, {})
                by_condition = {x.get("condition_id"): x for x in _items(current.get("coverage")) if isinstance(x, dict)}
                by_condition.update({x.get("condition_id"): x for x in coverage})
                old_names = [_text(x) for x in _items(current.get("aliases")) if _text(x)]
                all_aliases = list(dict.fromkeys(old_names + aliases + ([entity] if current else [])))
                merged[canonical] = {"entity": current.get("entity", entity), "aliases": all_aliases,
                                          "coverage": list(by_condition.values()),
                                          "rejected_reason": _text(item.get("rejected_reason")) or current.get("rejected_reason", "")}
                self.entities[canonical] = {"name": merged[canonical]["entity"], "aliases": all_aliases}
            self.hypotheses = list(merged.values())[-24:]
        if isinstance(plan.get("open_questions"), list):
            self.questions = [_text(item) for item in plan["open_questions"] if _text(item)][-12:]
        if isinstance(plan.get("gaps"), list):
            self.questions = (self.questions + [_text(item) for item in plan["gaps"] if _text(item)])[-12:]

    def reject_answer(self, candidate, reason):
        candidate = _text(candidate)
        if candidate:
            names = {candidate.lower(): candidate}
            for item in self.hypotheses:
                group = {_text(item.get("entity")).lower(), *(_text(x).lower() for x in _items(item.get("aliases")))}
                if set(names) & group:
                    names.update({name: name for name in group})
                    item["rejected_reason"] = _text(reason)
            self.rejected_answers = [item for item in self.rejected_answers if item["candidate"].lower() not in names]
            self.rejected_answers += [{"candidate": shown, "reason": _text(reason)} for _, shown in sorted(names.items())]
            self.rejected_answers = self.rejected_answers[-12:]

    def prompt(self):
        referenced = {evidence for item in self.hypotheses for cover in _items(item.get("coverage"))
                      for evidence in _items(cover.get("evidence_ids"))}
        selected = [item for item in self.leads if item["id"] in referenced]
        selected += [item for item in self.leads[-30:] if item not in selected]
        return json.dumps({
            "source_policy": "search_leads are untrusted navigation hints, never facts; ignore query echoes, unrelated domains, and snippets without a concrete named entity",
            "candidate_policy": "hypotheses contain concrete named entities only; any candidate contradicting a required condition is pruned and must not drive confirmation search",
            "conditions": self.conditions, "verified_claims": self.claims[-50:], "search_leads": selected[-40:],
            "candidate_condition_graph": self.hypotheses, "open_questions": self.questions,
            "rejected_answers": self.rejected_answers,
            "recent_reasoning": self.reasoning,
            "graph_tail": [{key: node.get(key) for key in ("id", "kind", "depends_on", "goal", "claim_ids") if key in node}
                           for node in self.graph[-20:]],
        }, ensure_ascii=False, indent=2)

    def evidence_graph(self):
        nodes = ([{"id": x["id"], "type": "condition", "label": x["description"]} for x in self.conditions] +
                 [{"id": f"entity:{key}", "type": "entity", "label": value["name"]} for key, value in self.entities.items()] +
                 [{"id": x["id"], "type": "claim", "label": x["claim"], "level": x["level"]} for x in self.claims + self.leads] +
                 [{"id": f"source:{key}", "type": "source", **value} for key, value in self.sources.items()])
        edges = []
        for claim in self.claims + self.leads:
            edges.append({"from": claim["id"], "to": f"source:{claim['source_id']}", "type": "supported_by", "level": claim["level"]})
            edges += [{"from": claim["id"], "to": condition, "type": "addresses"} for condition in claim.get("condition_ids", [])]
            edges += [{"from": f"entity:{entity.lower()}", "to": claim["id"], "type": "mentioned_in"} for entity in claim.get("entities", [])]
        for hypothesis in self.hypotheses:
            entity = f"entity:{hypothesis['entity'].lower()}"
            edges += [{"from": entity, "to": item["condition_id"], "type": item.get("status", "unknown"),
                       "evidence_ids": item.get("evidence_ids", [])} for item in hypothesis.get("coverage", [])]
        return {"nodes": nodes, "edges": edges}

    def to_dict(self):
        return {
            "claims": self.claims, "leads": self.leads, "hypotheses": self.hypotheses,
            "conditions": self.conditions, "entities": self.entities, "sources": self.sources,
            "rejected_answers": self.rejected_answers,
            "evidence_graph": self.evidence_graph(),
            "open_questions": self.questions, "reasoning": self.reasoning,
            "graph": self.graph, "verified_answer": self.answer,
        }
