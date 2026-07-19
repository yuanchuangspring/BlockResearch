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
        self.inferences = []
        self.hypotheses, self.questions, self.reasoning, self.rejected_answers = [], [], [], []
        self.conditions, self.entities, self.sources = [], {}, {}
        self.graph, self.answer = [], ""
        self.stage_summaries, self.action_ledger, self.builder_history = [], [], []
        self.candidate_memory = {}
        self.adviser_history, self.verification_history = [], []

    def add_node(self, kind, payload, depends_on=()):
        node_id = f"n{len(self.graph) + 1}"
        self.graph.append({"id": node_id, "kind": kind, "depends_on": list(depends_on), **payload})
        return node_id

    def _claim_id(self):
        numbers = [int(item["id"][1:]) for item in self.claims + self.leads if str(item.get("id", "")).startswith("c")]
        return f"c{max(numbers, default=0) + 1}"

    def _inference_id(self):
        numbers = [int(item["id"][1:]) for item in self.inferences if str(item.get("id", "")).startswith("d")]
        return f"d{max(numbers, default=0) + 1}"

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
                # Search in raw text fields first (avoids JSON newline-escaping bugs),
                # then fall back to the full JSON body.
                text_fields = " ".join(
                    _text(v) for k, v in document.items()
                    if k in ("text", "title", "snippet", "first_page", "last_page", "stdout")
                    and isinstance(v, str)
                ).lower()
                json_body = _text(json.dumps(document, ensure_ascii=False, default=str)).lower()
                body = f"{text_fields} {json_body}"
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
            if output.get("_type") not in {"SEARCH", "BROWSE"}:
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
        known = {item["id"] for item in self.claims + self.inferences}
        valid_conditions = {item["id"] for item in self.conditions}
        existing = {(_text(item.get("conclusion")).lower(), tuple(item.get("premise_ids", []))) for item in self.inferences}
        for item in _items(plan.get("inferences")):
            if not isinstance(item, dict):
                continue
            conclusion = _text(item.get("conclusion"))
            requested = list(dict.fromkeys(_text(value) for value in _items(item.get("premise_ids")) if _text(value)))
            premises = [value for value in requested if value in known]
            key = (conclusion.lower(), tuple(premises))
            if not conclusion or not premises or len(premises) != len(requested) or key in existing:
                continue
            condition_ids = [_text(value) for value in _items(item.get("condition_ids"))
                             if _text(value) in valid_conditions]
            if len(set(condition_ids)) > 1:
                continue
            self.inferences.append({
                "id": self._inference_id(), "conclusion": conclusion, "premise_ids": premises,
                "condition_ids": list(dict.fromkeys(condition_ids)),
                "entities": list(dict.fromkeys(_text(value) for value in _items(item.get("entities")) if _text(value))),
                "level": "derived",
            })
            known.add(self.inferences[-1]["id"])
            existing.add(key)
        self.inferences = self.inferences[-40:]
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
                evidence = {x["id"]: x for x in self.claims + self.leads + self.inferences}
                coverage = []
                for cover in _items(item.get("coverage")):
                    if not isinstance(cover, dict) or not _text(cover.get("condition_id")): continue
                    condition_id = _text(cover["condition_id"])
                    ids = [x for x in _items(cover.get("evidence_ids")) if x in evidence]
                    verified = any(evidence[x]["level"] == "verified" and condition_id in evidence[x].get("condition_ids", []) for x in ids)
                    derived = any(evidence[x]["level"] == "derived" and condition_id in evidence[x].get("condition_ids", []) for x in ids)
                    status = "verified" if verified else "derived" if derived else "lead" if ids else "unknown"
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

    def record_stage_summary(self, stage: int, new_verified: int, new_leads: int,
                             successful_pages: int, failed_fetches: int, candidate_changes: int,
                             verifier_rejected: list = None):
        consecutive = 1
        if self.stage_summaries and self.stage_summaries[-1]["new_verified_claims"] == 0:
            consecutive = self.stage_summaries[-1].get("consecutive_zero_stages", 1) + (1 if new_verified == 0 else 0)
        self.stage_summaries.append({
            "stage": stage, "new_verified_claims": new_verified, "new_leads": new_leads,
            "successful_pages": successful_pages, "failed_fetches": failed_fetches,
            "candidate_changes": candidate_changes, "verifier_rejected": verifier_rejected or [],
            "consecutive_zero_stages": consecutive if new_verified == 0 else 0,
        })
        self.stage_summaries = self.stage_summaries[-4:]

    def record_actions(self, stage, plan, outputs, information_gain):
        """Remember attempted retrievals so the next Builder chooses a new action."""
        for block in (plan.get("blocks") or []):
            if not isinstance(block, dict) or str(block.get("type", "")).upper() not in {"SEARCH", "BROWSE", "FETCH", "READ_PDF"}:
                continue
            params = block.get("params") or {}
            targets = params.get("queries", params.get("urls", params.get("url", [])))
            targets = targets if isinstance(targets, list) else [targets]
            self.action_ledger.append({
                "stage": stage, "type": str(block.get("type", "")).upper(),
                "targets": [_text(item) for item in targets if _text(item)][:8],
                "focus": [_text(item) for item in (plan.get("focus_condition_ids") or []) if _text(item)],
                "information_gain": information_gain,
            })
        self.action_ledger = self.action_ledger[-24:]

    def record_builder(self, stage, plan):
        self.builder_history.append({
            "stage": stage,
            "objective": _text(plan.get("objective")),
            "decision": _text(plan.get("decision")) or "continue",
            "best_guess": _text(plan.get("best_guess")),
            "focus_condition_ids": [_text(x) for x in _items(plan.get("focus_condition_ids")) if _text(x)],
            "expected_observation": _text(plan.get("expected_observation")),
            "rationale": _text(plan.get("rationale")),
        })
        self.builder_history = self.builder_history[-4:]

    def record_candidates(self, stage, report):
        for item in _items(report.get("candidates")):
            if not isinstance(item, dict): continue
            name = _text(item.get("name"))
            if not name: continue
            key = name.lower()
            old = self.candidate_memory.get(key, {"name": name})
            old.update({"status": _text(item.get("status")) or old.get("status", "plausible"),
                        "why": _text(item.get("why")) or old.get("why", ""),
                        "last_updated_stage": stage})
            self.candidate_memory[key] = old
        guess = _text(report.get("best_guess"))
        if guess:
            key = guess.lower()
            self.candidate_memory.setdefault(key, {"name": guess, "status": "plausible", "why": ""})
            self.candidate_memory[key]["last_best_stage"] = stage
        self.adviser_history.append({
            "stage": stage, "best_guess": guess,
            "decisive_gap": _text(report.get("decisive_gap")),
            "recommendation": _text(report.get("recommendation")),
            "memo": _text(report.get("memo"))[:800],
        })
        self.adviser_history = self.adviser_history[-3:]

    def record_verification(self, stage, candidate, verdict):
        record = {"stage": stage, "candidate": _text(candidate),
                  "accepted": bool(verdict.get("accepted")),
                  "reason": _text(verdict.get("reason"))[:1000]}
        self.verification_history.append(record)
        self.verification_history = self.verification_history[-3:]
        if not record["accepted"] and record["reason"]:
            self.questions = (self.questions + [record["reason"]])[-12:]

    def solver_state(self):
        """Compact proof state; raw retrieval observations are supplied separately."""
        return json.dumps({
            "conditions": self.conditions,
            "verified_claims": self.claims[-20:],
            "candidate_condition_graph": self.hypotheses[-12:],
            "candidate_memory": list(self.candidate_memory.values())[-16:],
            "decisive_gaps": self.questions[-6:],
            "rejected_answers": self.rejected_answers[-8:],
        }, ensure_ascii=False)

    def prompt(self):
        referenced = {evidence for item in self.hypotheses for cover in _items(item.get("coverage"))
                      for evidence in _items(cover.get("evidence_ids"))}
        selected = [item for item in self.leads if item["id"] in referenced]
        # Keep the retrieval frontier route-diverse. A later broad SEARCH must not
        # evict every intermediate entity found by an earlier route.
        recent, seen_queries = [], set()
        for item in reversed(self.leads):
            query = _text(item.get("query")) or f"source:{item.get('source_id', '')}"
            if query in seen_queries:
                continue
            seen_queries.add(query)
            recent.append(item)
            if len(recent) >= 12:
                break
        selected += [item for item in reversed(recent) if item not in selected]
        last_stage = self.stage_summaries[-1] if self.stage_summaries else None
        verified_by_candidate = {
            item.get("entity", ""): len({cover.get("condition_id") for cover in _items(item.get("coverage"))
                                         if cover.get("status") in {"verified", "derived"}})
            for item in self.hypotheses
        }
        proof_threshold = max(2, (len(self.conditions) + 1) // 2)
        recent_guesses = [item.get("best_guess", "") for item in self.builder_history if item.get("best_guess")]
        stable_guess = recent_guesses[-1] if len(recent_guesses) >= 2 and recent_guesses[-1] == recent_guesses[-2] else ""
        return json.dumps({
            "source_policy": "search_leads are untrusted navigation hints, never facts; ignore query echoes, unrelated domains, and snippets without a concrete named entity",
            "candidate_policy": "hypotheses contain concrete named entities only; any candidate contradicting a required condition is pruned and must not drive confirmation search",
            "conditions": self.conditions, "verified_claims": self.claims[-24:],
            "derived_inferences": self.inferences[-16:], "candidate_leads": selected[-12:],
            "candidate_condition_graph": self.hypotheses[-12:], "decisive_gaps": self.questions[-6:],
            "candidate_memory": list(self.candidate_memory.values())[-16:],
            "candidate_search_control": {
                "verified_conditions_by_candidate": verified_by_candidate,
                "proof_threshold_before_narrowing": proof_threshold,
                "exploration_required": max(verified_by_candidate.values(), default=0) < proof_threshold,
            },
            "rejected_answers": self.rejected_answers,
            "last_stage": last_stage,
            "recent_builder_decisions": self.builder_history,
            "recent_adviser_reports": self.adviser_history,
            "recent_verifications": self.verification_history,
            "stop_guidance": {
                "stable_best_guess": stable_guess,
                "rule": "answer when the same candidate leads for two decisions and no live alternative has evidence likely to overtake it; unresolved confidence-only details do not justify another stage",
            },
            "failed_or_completed_actions": self.action_ledger[-12:],
        }, ensure_ascii=False, indent=2)

    def evidence_graph(self):
        nodes = ([{"id": x["id"], "type": "condition", "label": x["description"]} for x in self.conditions] +
                 [{"id": f"entity:{key}", "type": "entity", "label": value["name"]} for key, value in self.entities.items()] +
                 [{"id": x["id"], "type": "claim", "label": x["claim"], "level": x["level"]} for x in self.claims + self.leads] +
                 [{"id": x["id"], "type": "inference", "label": x["conclusion"], "level": "derived"} for x in self.inferences] +
                 [{"id": f"source:{key}", "type": "source", **value} for key, value in self.sources.items()])
        edges = []
        for claim in self.claims + self.leads:
            edges.append({"from": claim["id"], "to": f"source:{claim['source_id']}", "type": "supported_by", "level": claim["level"]})
            edges += [{"from": claim["id"], "to": condition, "type": "addresses"} for condition in claim.get("condition_ids", [])]
            edges += [{"from": f"entity:{entity.lower()}", "to": claim["id"], "type": "mentioned_in"} for entity in claim.get("entities", [])]
        for inference in self.inferences:
            edges += [{"from": premise, "to": inference["id"], "type": "premise_of"} for premise in inference["premise_ids"]]
            edges += [{"from": inference["id"], "to": condition, "type": "derives"} for condition in inference.get("condition_ids", [])]
            edges += [{"from": f"entity:{entity.lower()}", "to": inference["id"], "type": "inferred_about"}
                      for entity in inference.get("entities", [])]
        for hypothesis in self.hypotheses:
            entity = f"entity:{hypothesis['entity'].lower()}"
            edges += [{"from": entity, "to": item["condition_id"], "type": item.get("status", "unknown"),
                       "evidence_ids": item.get("evidence_ids", [])} for item in hypothesis.get("coverage", [])]
        return {"nodes": nodes, "edges": edges}

    def to_dict(self):
        return {
            "claims": self.claims, "leads": self.leads, "inferences": self.inferences, "hypotheses": self.hypotheses,
            "conditions": self.conditions, "entities": self.entities, "sources": self.sources,
            "rejected_answers": self.rejected_answers,
            "evidence_graph": self.evidence_graph(),
            "open_questions": self.questions, "reasoning": self.reasoning,
            "graph": self.graph, "stage_summaries": self.stage_summaries, "verified_answer": self.answer,
            "action_ledger": self.action_ledger, "builder_history": self.builder_history,
            "candidate_memory": list(self.candidate_memory.values()),
            "adviser_history": self.adviser_history,
            "verification_history": self.verification_history,
        }

    def proof(self, claim_ids, inference_ids):
        """Return a closed proof subgraph rooted at the Solver-cited evidence."""
        claims = {item["id"]: item for item in self.claims}
        inferences = {item["id"]: item for item in self.inferences}
        wanted_claims, wanted_inferences = set(), set()
        stack = [value for value in list(claim_ids) + list(inference_ids) if value]
        while stack:
            item_id = stack.pop()
            if item_id in claims:
                wanted_claims.add(item_id)
            elif item_id in inferences and item_id not in wanted_inferences:
                wanted_inferences.add(item_id)
                stack.extend(inferences[item_id].get("premise_ids", []))
        return ([claims[item_id] for item_id in wanted_claims],
                [inferences[item_id] for item_id in wanted_inferences])
