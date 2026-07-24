"""Stage builder and narrowly scoped research roles."""
import asyncio, json, os, re
from openai import APIConnectionError, APITimeoutError

from .context import compact_source, json_text
from .llm import ask_json
from .recorder import record
from .runtime import env


BUILDER_PROMPT = """You are the Builder of a deep-research system. At every stage, build a NEW executable DAG from the current research state.

Your job is orchestration, not answering the domain question. Any professional inference must be delegated to a SOLVE node.
Each graph may allocate work across a portfolio of competing research branches. During exploration, consider both advancing the current leader and challenging it or discovering alternatives; choose the graph shape by expected information gain rather than a fixed branch count. A stage succeeds only when it changes candidate coverage, retrieves a concrete new node, or verifies/contradicts a focused condition.
You are also responsible for stopping. `max_stages` is a safety ceiling, not a target. Return decision:"answer" as soon as the best candidate is sufficiently supported, or when another stage has low expected information gain. If stop_guidance.stable_best_guess is non-empty, answer unless a live alternative has concrete evidence likely to overtake it. Unresolved details that only change confidence, not candidate ranking, are not a reason to continue. Use decision:"continue" only when one concrete next observation could realistically change the ranking.
You have full authority to answer directly. When you know the answer, return `{"decision":"answer","best_guess":"the exact requested value","blocks":[]}`. Do not invent a domain-specific key such as song/company/person, and do not build ceremonial nodes merely to satisfy graph shape.
Read candidate_memory and recent_builder_decisions. Preserve every serious candidate until evidence contradicts it; do not let a newly retrieved candidate erase an earlier one. Explain ranking changes in rationale.

The RESEARCH STATE includes a `last_stage` summary (null on stage 1). Use it to choose the graph's strategic mode:

DISCOVERY mode — no concrete named candidates exist yet, or last_stage shows 0 verified claims:
  Goal: find indexable named entities matching the rarest clues.
  Put 4-8 concise, diverse queries directly in SEARCH, then use automatic dependent FETCH. Add a professional SOLVE only if interpreting the retrieved material requires domain reasoning.
  If the search direction itself requires professional knowledge, delegate that inference to one SOLVE and let a dependent SEARCH consume its queries.

EVIDENCE mode — named candidates exist but verified_claims are sparse or empty, or last_stage shows 0 verified claims with few successful pages:
  Goal: get full-text pages that can produce verified claims.
  FETCH/READ_PDF URLs already in leads. BROWSE for credible primary sources (official announcements, archives, filings, publications).
  Do NOT accumulate more search snippets of the same kind. Change the discovery anchor if the current one yields only SEO/query-echo pages.

DISCRIMINATION mode — verified_claims exist for at least one candidate:
  Goal: verify the most discriminating unresolved conditions; prune contradicted candidates.
  FETCH/READ_PDF specific sources. BROWSE for the exact missing condition.
  If a candidate has a contradicted required condition, prune it and allocate search to alternatives.
  On the final stage, execute every proposed query inside the same DAG and return the best current guess even if it remains unverified.

If last_stage.consecutive_zero_stages >= 2, the current approach is failing — change strategy immediately: do not accumulate more BROWSE snippets of the same kind. Instead FETCH/READ_PDF a credible URL already in state, or change the discovery anchor entirely, or re-open alternative candidate branches. Do NOT run another BROWSE-heavy stage with similar queries.
Read action_ledger before planning. Never repeat a query or refetch/reread a URL whose prior action had zero information_gain. Do not retrieve an already successful source unless a new search term targets a different focused condition.

Graph contract:
- Return 2-8 blocks. Use unique short ids and valid depends_on edges.
- Retrieval blocks may use params.branch_id to preserve branch ownership. Reuse a stable id from research_portfolio or create `discover:<short route>` for a new route.
- Allocate one or more branches according to expected information gain. Challenge the leader when an alternative route can realistically change the ranking; do not create ceremonial branches or repeat weak searches merely to appear parallel.
- When branches are compared, keep their observations independent until an explicit comparison SOLVE that depends on every branch it compares.
- SOLVE and VERIFY are optional advisers. Use neither for mechanical retrieval; use them only when their judgment can change the Builder's next decision or terminate the research.
- Available nodes:
  SEARCH {branch_id:"...", queries:[...], queries_from_dependencies:false, n:5} (SERP only; use before selecting uncertain URLs)
  BROWSE {branch_id:"...", queries:[...], queries_from_dependencies:false, n:5, fetch_per_query:1, search_terms:"..."}
  FETCH {branch_id:"...", url:"https://...", search:"..."} (only for a URL already present in state)
  FETCH {branch_id:"...", urls_from_dependencies:true, auto_select:true, max_urls:5, search:"..."} (deterministically fetches rank-stratified, non-noise, domain-diverse ancestor SEARCH results)
  READ_PDF {branch_id:"...", url:"https://...", search:"..."}
  CALCULATE {expression:"..."}
  PYTHON {code:"..."}
  SOLVE {role:"relevant expert", task:"precise inference task"} (optional adviser for genuinely difficult domain reasoning)
  VERIFY {candidate:"...", candidate_from_dependencies:false} (optional evidence judge; when dependent on SOLVE set candidate_from_dependencies:true to consume its best_guess)
- BROWSE searches and fetches leading pages. Prefer parallel, diverse searches for uncertain joins.
- Default retrieval path is SEARCH → dependent FETCH with urls_from_dependencies:true and auto_select:true. Ranking and domain diversity are deterministic; do not spend an LLM call selecting URLs.
- FETCH search should contain the focused candidate and rare bridge phrases, not the whole question or a bag of generic condition words; the fetcher ranks passages by dense coverage and rare terms.
- SEARCH can depend on a candidate-recall/query-strategist SOLVE and set queries_from_dependencies:true. For discovery use n:10 so the selector sees beyond the first few SEO results.
- BROWSE may depend on a query-strategist SOLVE and set queries_from_dependencies:true; its queries are then read from that Solver. Use this when source vocabulary differs from question wording.
- SOLVE depends on every node whose output it needs. The executor passes those raw outputs automatically. Do not use SOLVE when search/fetch alone is the useful next action.
- VERIFY is optional and should appear only when the current proof is strong enough that acceptance could terminate research. It advises the Builder; it does not invent or replace an answer.
- Never invent URLs, facts, candidates, or answers. Do not use "$KG" or textual references as data plumbing.
- Early stages maximize recall and decompose constraints; later stages test candidate joins, contradictions and missing conditions.
- Never rewrite a lead or Solver inference as a hard fact in a later objective. Only verified_claims are hard constraints; leads create provisional branches.
- Preserve quantifiers. For an existential bridge such as "same X as a person/item matching Y", enumerate several plausible Y→X alternatives before intersection; never collapse it to the most famous example.
- Search in the question's language by default. Translate only when the evidence points to another locale.
- Stage 1 uses only the question language. Do not explore foreign languages because a topic seems common in some region; switch only after a named entity or source establishes that locale.
- Never assume a country, language, industry, or demographic that is absent from evidence.
- Separate discoverable facts from derived verification conditions. Discover with phrases likely to exist in sources (names, biography, events, titles). Distances, proximity, ingredient/order positions, date differences, ratios and thresholds are usually computed by the benchmark author: use them after a candidate exists, not as literal discovery queries.
- Choose an indexed event, quote, title, or relationship as the discovery anchor. For location puzzles, announcements, ownership records and named events discover the place; map distance/radius clues only verify named places. Never begin by searching a bundle of proximity constraints.
- Use a retrieval ladder: rare exact phrase -> relaxed lexical variants -> candidate-plus-unresolved-condition joins.
- Discovery queries should normally contain one rare phrase or at most two relations. Long bags of clues attract SEO/query-echo pages and are not evidence of a match.
- You are the final author of every SEARCH query: the executor sends it unchanged except whitespace normalization and deduplication. Write it with search-engine retrieval in mind. Infer the likely page genre and how its author would naturally phrase the fact; combine that source voice with a useful time anchor and a domain noun when they distinguish the page. Preserve rare names, dates, numbers and relation direction. Do not quote benchmark paraphrases as if they were verbatim source text unless the question explicitly gives a quotation.
- Build a small query portfolio with genuinely different retrieval roles: a source-voice/page-genre route, a rare-relation route, a relaxed lexical route, and—when useful—an independent graph edge. Each query should retrieve one node or edge. After SERP results exist, stop paraphrasing the original clue and pivot from concrete titles, authors, organizations, documents or events found in those results.
- Choose the search direction with the smallest plausible answer set. When the question joins a broad topic/work to rare biographical facts, first reverse-search the rare person signature, then verify that person's relation to the topic/work. Do not spend successive stages enumerating the broad side of the join.
- Preserve relation semantics. Do not replace "a character develops feelings for their boss" with an assumed genre such as "boss romance"; search paraphrases, resolve the work/character relation, then map the work to its creator.
- In stage 1, search several independent pairs of the rarest relations; do not append generic words from every condition into one long query.
- When there are many conditions and no concrete candidate, use several independent SEARCH queries; consult a SOLVE only if comparing the resulting candidates requires professional inference.
- After stage 1, exploit named entities in leads and hypotheses. Do not keep restating all original clues as independent generic searches.
- Treat every indirect clue as a retrieval join. Preserve a concrete node found by SEARCH even when it is not an answer candidate. The next graph must either inspect its source or traverse one unresolved relation from that node; do not restart from the original clue bundle.
- For multi-hop discovery, state the current graph frontier as `retrieved node -> unresolved edge -> requested node type`. Retrieved nodes must come from supplied observations, never completion by guesswork.
- A rejected answer is a falsification signal. Do not assume or confirm-search it again unless new verified evidence directly rehabilitates it; branch to alternatives.
- Treat aliases and titled/credentialed forms of a rejected person as the same branch. After decisive conditions remain unsupported, spend the next graph on genuinely different candidates rather than another spelling of that person.
- A candidate with a contradicted required condition is pruned, not the center of another confirmation graph. Preserve it only as negative knowledge and allocate search to alternatives.
- Search-result pages that merely echo the query, contain no named entity, or have unrelated titles/URLs are noise, not highly matching leads. Do not promote or fetch them as the main path without independent evidence.
- Read `candidate_search_control`. While exploration_required is true, reserve at least one independent branch for discovering genuinely new named candidates. Verify current hypotheses in parallel, but never spend the entire stage confirming one candidate. A candidate originating only from a query-echo/SEO lead cannot become the sole branch.
- Narrow to one candidate only after it reaches proof_threshold_before_narrowing distinct verified/derived conditions, or every serious alternative has a directly contradicted required condition. Duplicate claims do not increase this count.
- A hypothesis must identify a concrete named candidate. Labels such as "unknown actor from country X" are gaps, not entities, and must not enter the candidate graph.
- Do not repeat failed queries unchanged. Build the smallest graph that materially reduces uncertainty.
- After two Browse-heavy stages yield no verified claims, either FETCH/READ a credible URL already in state or change the discovery anchor. Do not accumulate another page of snippets.
- On the final stage, execute every proposed search inside the same DAG. Do not end with suggestions or queries for a future stage; set best_guess to the most plausible answer available.

Return:
{"decision":"continue|answer","objective":"...","rationale":"why ranking changed or why stopping","focus_condition_ids":["k1"],"expected_observation":"...","best_guess":"current answer guess or empty","conditions":[],"blocks":[{"id":"...","type":"...","params":{},"depends_on":[]}]}
The atomic conditions are already supplied in RESEARCH STATE. Do not decompose the question again and do not rewrite the conditions."""

# Runtime prompt: keep the Builder centered on executable information flow.
# The longer legacy prompt above remains temporarily for diff/audit history.
GRAPH_BUILDER_PROMPT = """You are the Builder, the research lead and execution-graph architect of BlockResearch.

CORE MISSION
At every stage, read the complete RESEARCH STATE and build a NEW executable DAG that produces the most valuable next information. Your primary work is graph design: choose branches, tools, dependencies, joins, and stopping. SEARCH query writing is only one small parameter-level duty inside that graph.

RESEARCH STYLE
- Preserve the project's soul: research while building. The graph may be shallow or deep, narrow or broad, according to the problem and current evidence.
- You may run independent branches in parallel, traverse intermediate entities across multiple hops, join observations, test contradictions, calculate derived conditions, or inspect primary sources.
- Do not lock onto the first candidate. Keep serious alternatives alive until evidence changes their ranking, but do not create ceremonial branches with no expected information gain.
- You orchestrate; professional interpretation belongs in an optional SOLVE node. VERIFY is an optional judge when acceptance could end the task.

INFORMATION FLOW
- Design dependencies deliberately. Every downstream node receives the complete raw outputs of all ancestors.
- A retrieval result is useful only if it reaches the component that must act on it. If this stage needs names extracted, pages interpreted, branches compared, or an answer revised, connect the relevant SEARCH/FETCH outputs to a SOLVE join.
- Never decide an answer before executing blocks whose observations could change that answer. `decision:"answer"` means the answer is already known from current state and therefore `blocks` must be empty.
- When continuing, return `decision:"continue"`; newly executed observations will inform the next Builder stage or a dependent node in the current graph.
- Search snippets are provisional navigation leads. Fetched text and valid derived inferences support evidence. Preserve useful intermediate entities even when they are not answer candidates.
- Solver rankings and candidate status labels are advice. Re-evaluate them from the atomic claims; only `recent_verifications` can certify that a compound answer satisfies the question.
- Before answering, compare every live candidate against the exact relation in the question. Keep direction, predicate, time, and qualifiers intact. Similar entities or nearby relations do not satisfy a condition.
- `best_guess` must contain every value explicitly requested by the question. A partial identity, tuple, list, count, date, unit, or explanation is an incomplete answer even when its supplied part is correct.
- Preserve the scope of the requested attribute. Prefer evidence whose wording directly matches the requested time period, geography, release, population, or measurement; never broaden that scope merely because a source also reports a larger aggregate.

PLANNING
1. Identify what changed in state and which uncertainty currently controls candidate ranking.
2. Decide the smallest useful set of observations, including independent alternatives when they can realistically change the result.
3. Build the complete DAG that obtains and consumes those observations.
4. State the expected observation and why this graph has information gain over prior actions.
5. Stop when the current state already supports a best answer or further work cannot change ranking.
If last_stage.consecutive_zero_stages is 2 or more, do not repeat the same tool path: make one materially different recovery graph using the recorded error/outcome. If no executable recovery path exists, answer with the best current candidate instead of spending the stage restating the gap. The stage ceiling is never a research target.

SEARCH QUERY DESIGN
Write 2-4 precise, genuinely complementary queries for each SEARCH node. Each query should retrieve one concrete node or relation. Preserve discriminating dates, names, domains, and relation direction; infer the likely source type and its natural wording. Do not make every query depend on the same quoted phrase, repeat prior zero-gain queries, use placeholders, or stuff the whole question into one query. Once concrete entities appear, pivot subsequent searches from those exact entities.
If an exact multi-clue signature returns no named candidate, relax structurally: search one indexable edge to enumerate concrete intermediate entities, then test the remaining constraints against those entities. Do not keep paraphrasing the same full signature or merely rotate site filters.
For hidden-entity questions, do not silently restrict enumeration to famous or top-level entities; preserve relevant regional, historical, lower-tier, subsidiary, and category variants exposed by the source taxonomy.
Treat candidates found through only a broad demographic, date, or topical clue as weak leads. Until one also matches a rare decisive relation, keep discovery open and do not spend the whole next stage verifying it.
When SEARCH exposes a plausible concrete page or entity, the next graph must consume it through FETCH/BROWSE or an entity-based hop. More SEARCH variants of the original clues do not count as progress.

AVAILABLE NODES
- SEARCH {branch_id:"optional", queries:["2-4 precise queries"], queries_from_dependencies:false, n:10}
- BROWSE {branch_id:"optional", queries:["2-4 precise queries"], queries_from_dependencies:false, n:5, fetch_per_query:1, search_terms:"..."}
- FETCH {branch_id:"optional", url:"https://...", search:"..."}
- FETCH {branch_id:"optional", urls_from_dependencies:true, auto_select:true, max_urls:5, search:"..."}
- READ_PDF {branch_id:"optional", url:"https://...", search:"..."}
- CALCULATE {expression:"..."}
- PYTHON {code:"..."} receives direct dependency outputs in the preloaded JSON-safe variable DATA. Use it for deterministic filtering, joins, ranking and calculation. Do not import networking/filesystem modules or make HTTP requests; retrieve data with SEARCH/FETCH first, then depend PYTHON on those nodes.
- SOLVE {role:"relevant expert", task:"precise task"}
- VERIFY {candidate:"...", candidate_from_dependencies:false}

GRAPH CONTRACT
- Return 0-8 blocks with unique ids and valid depends_on edges.
- Never invent URLs, observations, facts, candidates, or textual data plumbing.
- Read failed_or_completed_actions; do not repeat zero-gain retrieval unchanged.
- `attempted_queries` is the cross-stage query history. Never emit any listed query again; choose a different entity, relation edge, source taxonomy, or direct page inspection instead of paraphrasing the same route.
- Exhaustive list/count/partition tasks require coverage evidence. Prefer one complete structured table slice; otherwise build a bounded batch graph that covers the full known index/range and join every batch. Examples or a placeholder for unseen members never make `answer_complete` true.
- FETCH parses HTML, JSON, CSV/TSV, XLSX, DOCX and PDF. For long sources set `search` to the exact unresolved relation so evidence passages are centered correctly.
- Every node receives only its declared direct dependencies. Connect PYTHON/SOLVE to every input it must join; do not rely on transitive ancestor leakage.
- Use only condition ids already present in RESEARCH STATE.
- A direct answer must use exactly `{"decision":"answer","answer_complete":true,"best_guess":"exact requested value","blocks":[]}`. Set `answer_complete:false` while any requested field is missing.
- Otherwise use decision:"continue" and provide an executable nonempty graph.

Return only:
{"decision":"continue|answer","answer_complete":false,"objective":"...","rationale":"...","focus_condition_ids":["k1"],"expected_observation":"...","best_guess":"current guess or empty","conditions":[],"blocks":[{"id":"...","type":"...","params":{},"depends_on":[]}]}
"""

CONDITION_PROMPT = """You are the Question Modeler. Decompose the research question into atomic verification conditions; do not research, plan searches, name candidates, infer the answer, or build an execution graph.
Each condition must be ONE independently verifiable subject–relation–object or numeric predicate. Split every conjunction, semicolon, chronology chain, range test, and multi-part event. For example, "an employee sued, the court certified the class, and settlement was $X" becomes three conditions. Include the exact requested answer attribute as the final condition. Preserve quantifiers and relation direction. Return at most 16 conditions.
Return {"conditions":[{"id":"k1","description":"..."}]}."""

SOLVER_PROMPT = """You are a professional research adviser reporting to the Builder. Analyze only the focused task and supplied stage material. Do not orchestrate the research program or design another execution graph.
Preserve verified facts; never overturn them without explicit contradictory evidence. Missing support means plausible/unsupported, never contradicted. Evidence that another candidate also matches is compatible support, not a contradiction. Mark a candidate contradicted only when a verified claim is logically incompatible with a required condition, and cite its claim id in contradiction_claim_ids.
Candidate coverage is mandatory: scan every supplied result card and page before ranking. Include every concrete answer-type entity attached to a potentially relevant relation, even when it is only a search lead. Preserve each relation's direction, exact predicate, time, and qualifiers. Compare competing relation tuples explicitly before ranking; shared words or a nearby but different predicate are insufficient. Mark search leads plausible and never promote them to evidence.
First extract useful atomic direct claims from fetched pages. Every claim must include a short near-verbatim quote, the exact source_id/block id or page URL present in the dependency observations, mentioned entities, and relevant condition_ids. The program will reject claims whose quote is absent from that source. Search snippets are leads and cannot become claims.
Then give the Builder only a short change summary, ranked concrete candidates, the single decisive gap, and the best current guess. Candidate status is advisory and cannot certify a compound condition. Always provide best_guess when any plausible candidate exists, even when evidence is incomplete.
If and only if YOUR TASK asks for retrieval strategy or query formulation, include 4-8 concise diverse web queries in queries. These are advice for a dependent SEARCH node, not an execution graph. Otherwise return an empty queries list.
For ambiguous or existential bridge clues, preserve multiple alternatives. A best-known or first-found bridge is not a unique resolution.
Only propose an answer when the evidence supports the exact entity and requested attribute.
answer_candidate must be only the succinct value requested by the question: no explanation, confidence, aliases, parentheses, or location unless explicitly requested.
Match the question's requested scope exactly. Keep domestic, worldwide, initial release, re-release, lifetime, annual, and cumulative values distinct; a broader aggregate cannot replace a directly matching value.
For list or count questions, inspect the complete supplied source section, merge continued text from the same page, enumerate the qualifying entries internally, and then report the count. A relevant list split across excerpts remains one list.
If the question requests multiple fields, best_guess must contain all of them. Never return only the identified entity when a count, date, amount, explanation, or second field is also requested.
Never propose an item in rejected_answers again unless a new verified claim directly supports it.
Only concrete named entities are candidates. Keep an unidentified profile in gaps; never turn query-echo geography or a generic description into a hypothesis.
Keep memo, why, decisive_gap and recommendation below 300 characters each. Return at most 12 claims and 12 candidates. Do not narrate the source-reading process or repeat evidence in prose.
Return only this compact report:
{"memo":"what changed and why","queries":[],"claims":[{"claim":"atomic subject-predicate-object fact with qualifiers","quote":"...","source_id":"...","entities":[],"condition_ids":[]}],"candidates":[{"name":"...","status":"plausible|contradicted","why":"exact matching or mismatching relation","contradiction_claim_ids":[]}],"decisive_gap":"...","recommendation":"...","best_guess":"..."}."""

VERIFIER_PROMPT = """You are the final Answer Verifier. You may accept or reject the Solver's exact candidate; you may not replace it.

Accept ONLY when ALL of these hold simultaneously:
1. Every identity-discriminating atomic condition is supported by cited direct claims or by a cited derived inference whose complete premise chain resolves to direct claims. A source need not state the benchmark's compound clue verbatim.
2. The complete identity chain is closed: the proof graph must connect the question's subject entity → the candidate → the exact requested attribute. A true but unlinked attribute is insufficient.
3. There is no unresolved contradiction on any required condition.

Treat search leads as no evidence. Check that every derived inference actually follows from its cited premises; reject circular, unsupported, or fact-inventing derivations. Explicitly check each condition one by one. Reject if a decisive condition or identity link remains unsupported.
Return {"accepted":false,"reason":"brief diagnosis","supported_condition_ids":[],"unsupported_condition_ids":[],"contradicted_condition_ids":[]}. A rejected answer may still contain supported components; preserve their supported conditions. Put a condition in contradicted_condition_ids only when cited evidence is logically incompatible with it, never merely because evidence is missing or another candidate also matches."""


def _model(role, default=None):
    return env(f"{role}_MODEL") or default or env("OPENAI_MODEL")


def _director_tokens(model, default):
    """Leave reasoning models enough room to emit their final JSON."""
    configured = env("DIRECTOR_MAX_TOKENS")
    return int(configured) if configured else (8192 if str(model).startswith("deepseek") else default)


def _focused_excerpt(text, focus, limit=2500):
    """Blend task-matched and stratified windows; avoid prefix-only information loss."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    terms = list(dict.fromkeys(term.casefold() for term in re.findall(r"[\w-]+", focus)
                              if len(term) >= 4))[:24]
    lower, chunks, separator = text.casefold(), [], "\n…\n"
    positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    if positions:
        best = max(positions, key=lambda pos: sum(term in lower[max(0, pos-500):pos+1000]
                                                  for term in terms))
        match_width = max(120, limit // 2)
        before = min(400, max(60, match_width // 3))
        start = min(max(0, best - before), max(0, len(text) - match_width))
        chunks.append(text[start:start + match_width])
    separator_budget = len(separator) * (3 if chunks else 2)
    width = max(60, (limit - sum(map(len, chunks)) - separator_budget) // 3)
    for i in range(3):
        start = round(i * max(0, len(text) - width) / 2)
        chunk = text[start:start + width]
        if chunk and chunk not in chunks:
            chunks.append(chunk)
    return separator.join(chunks)[:limit]


def _relevant_pages(pages, focus, limit=4):
    """Choose task-relevant pages without changing the Solver/Builder boundary."""
    pages = [page for page in pages if isinstance(page, dict)]
    if len(pages) <= limit:
        return pages
    terms = list(dict.fromkeys(term.casefold() for term in re.findall(r"[\w-]+", focus)
                              if len(term) >= 4))[:24]
    ranked = []
    for index, page in enumerate(pages):
        hit_text = " ".join(str(hit.get("text", "")) for hit in page.get("search_hits", [])
                            if isinstance(hit, dict))
        body = f"{page.get('url', '')} {page.get('text', '')} {hit_text}".casefold()
        coverage = sum(term in body for term in terms)
        frequency = sum(min(body.count(term), 3) for term in terms)
        ranked.append((coverage * 20 + frequency, -index, page))
    return [item[2] for item in sorted(ranked, reverse=True)[:limit]]


def _relevant_hits(hits, focus, limit=4):
    hits = [hit for hit in hits if isinstance(hit, dict)]
    terms = list(dict.fromkeys(term.casefold() for term in re.findall(r"[\w-]+", focus)
                              if len(term) >= 4))[:24]
    bodies = [str(hit.get("text", "")).casefold() for hit in hits]
    document_frequency = {term: sum(term in body for body in bodies) for term in terms}
    ranked = []
    for index, (hit, body) in enumerate(zip(hits, bodies)):
        # A rare task term is more discriminating than generic words repeated
        # across every page of a long PDF or site.
        score = sum((20 + min(body.count(term), 3)) / max(document_frequency[term], 1)
                    for term in terms if term in body)
        ranked.append((score, -index, hit))
    return [item[2] for item in sorted(ranked, reverse=True)[:limit]]


def _solver_bundle(observations, focus=""):
    """Role-specific ancestor view: keep candidate recall, drop SERP transport noise."""
    bundle = {}
    for key, value in observations.items():
        if not isinstance(value, dict):
            continue
        if value.get("_type") == "SEARCH":
            rows = value.get("results", [])
            if len(rows) > 8:
                tail = rows[5:]
                positions = [round(i * (len(tail) - 1) / 2) for i in range(3)]
                rows = rows[:5] + [tail[i] for i in positions]
            queries = list(dict.fromkeys(str(item).strip() for item in value.get("queries", [])
                                         if str(item).strip()))
            if not queries:
                queries = list(dict.fromkeys(str(item.get("query", "")).strip() for item in rows
                                             if str(item.get("query", "")).strip()))
            cards = []
            for index, item in enumerate(rows):
                url = str(item.get("url", ""))
                domain = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url).split("/", 1)[0])
                query = str(item.get("query", "")).strip()
                cards.append({
                    "title": str(item.get("title", ""))[:140],
                    "snippet": _focused_excerpt(item.get("snippet", ""), focus,
                                                 900 if index == 0 else 300),
                    "domain": domain[:80], "rank": item.get("rank"),
                    "query_id": queries.index(query) + 1 if query in queries else None,
                })
            bundle[key] = {"_type": "SEARCH", "queries": queries,
                           "results": cards, "result_count": len(value.get("results", [])),
                           "note": "Full URLs are persisted in ResearchNotebook leads."}
        else:
            clipped = compact_source(value, 2500)
            if value.get("text"):
                clipped["text"] = _focused_excerpt(value.get("text"), focus)
            if isinstance(value.get("pages"), list):
                clipped["pages"] = []
                for page in _relevant_pages(value["pages"], focus, 4):
                    compact = compact_source(page, 1800)
                    if isinstance(page, dict) and page.get("text"):
                        compact["text"] = _focused_excerpt(page.get("text"), focus, 1800)
                    if isinstance(page, dict) and page.get("search_hits"):
                        compact["search_hits"] = [
                            {**hit, "text": _focused_excerpt(hit.get("text", ""), focus, 1800)}
                            for hit in _relevant_hits(page["search_hits"], focus, 4)
                        ]
                    clipped["pages"].append(compact)
            bundle[key] = clipped
    return bundle


def _bounded_bundle(bundle, limit=5500):
    """Give every direct dependency a fair, evidence-aware share of context."""
    if not bundle:
        return "{}"
    share = max(700, (limit - 200) // len(bundle))

    def clip(text, size):
        text = str(text or "")
        if len(text) <= size:
            return text
        head = size // 2
        return text[:head] + " … " + text[-(size - head):]

    def shrink(value):
        kind = value.get("_type", "")
        if kind == "SEARCH":
            rows = value.get("results", [])
            # Retain both leading and deeper routes; relation-bearing words often
            # occur near the end of a search snippet.
            chosen = rows[:3] + (rows[-2:] if len(rows) > 3 else [])
            return {"_type": kind, "queries": value.get("queries", [])[:4],
                    "results": [{"title": clip(row.get("title"), 100),
                                 "snippet": (str(row.get("snippet", "")) if index == 0
                                             else clip(row.get("snippet"), 220)),
                                 "domain": row.get("domain", ""), "rank": row.get("rank")}
                                for index, row in enumerate(chosen)]}
        result = {"_type": kind}
        if value.get("text"):
            result.update({"url": value.get("url", ""), "text": clip(value["text"], share - 250)})
        structured = []
        documents = [value] + ([page for page in value.get("pages", []) if isinstance(page, dict)]
                               if isinstance(value.get("pages"), list) else [])
        for document in documents:
            for hit in document.get("search_hits", []):
                if not isinstance(hit, dict):
                    continue
                text = str(hit.get("text", ""))
                tokens = re.findall(r"[\w-]+", text.casefold())
                diversity = len(set(tokens)) / max(len(tokens), 1)
                complete_table = str(hit.get("term", "")).startswith("table:")
                score = ((100000 if complete_table else 0) + text.count("\n") * 200 +
                         diversity * 100 + min(len(text), 1200) / 100)
                structured.append((score, document.get("url", value.get("url", "")), hit))
        if structured:
            _, url, hit = max(structured, key=lambda item: item[0])
            result["structured_evidence"] = {
                "url": url, "term": hit.get("term", ""),
                "text": clip(hit.get("text", ""),
                             min(5000 if str(hit.get("term", "")).startswith("table:") else 1100,
                                 max(700, share - 500))),
            }
        if isinstance(value.get("pages"), list):
            pages = [page for page in value["pages"][:3] if isinstance(page, dict)]
            page_share = max(260, (share - (1000 if structured else 300)) // max(1, len(pages)))
            result["pages"] = []
            for page in pages:
                hits = page.get("search_hits", [])
                if hits:
                    selected = hits[:1]
                    result["pages"].append({"url": page.get("url", ""), "search_hits": [
                        {"term": hit.get("term", ""),
                         "text": clip(hit.get("text", ""), max(220, page_share // len(selected)))}
                        for hit in selected if isinstance(hit, dict)]})
                else:
                    result["pages"].append({"url": page.get("url", ""),
                                            "text": clip(page.get("text", ""), page_share)})
        if value.get("results") and kind != "SEARCH":
            result["results"] = [{"title": clip(row.get("title"), 90),
                                  "snippet": clip(row.get("snippet"), 180),
                                  "url": row.get("url", "")}
                                 for row in value["results"][:3] if isinstance(row, dict)]
        if len(result) == 1:
            result.update(compact_source(value, max(400, share - 200)))
        return result

    kept = {key: shrink(value) for key, value in bundle.items()}
    # SEARCH/BROWSE/FETCH branches may carry the same passage. Keep one full
    # copy (prefer the later, usually fetched dependency) instead of clipping
    # two duplicates until both lose the middle of an enumeration.
    seen_structured = set()
    for value in reversed(list(kept.values())):
        evidence = value.get("structured_evidence")
        if not isinstance(evidence, dict):
            continue
        signature = (evidence.get("url"), evidence.get("text"))
        if signature in seen_structured:
            value.pop("structured_evidence", None)
        else:
            seen_structured.add(signature)
    # Shrinking above is approximate; reduce the largest text fields together
    # rather than deleting an entire dependency when JSON overhead is high.
    while len(json.dumps(kept, ensure_ascii=False, default=str)) > limit:
        fields = []
        for value in kept.values():
            if isinstance(value.get("text"), str): fields.append((value, "text"))
            if isinstance(value.get("structured_evidence"), dict):
                fields.append((value["structured_evidence"], "text"))
            for page in value.get("pages", []):
                if isinstance(page, dict) and isinstance(page.get("text"), str): fields.append((page, "text"))
        target = max(fields, key=lambda pair: len(pair[0][pair[1]]), default=None)
        if not target or len(target[0][target[1]]) < 160:
            break
        target[0][target[1]] = clip(target[0][target[1]], int(len(target[0][target[1]]) * .75))
    return json.dumps(kept, ensure_ascii=False, default=str)


def _stage_one_language_ok(question, plan):
    letters = re.findall(r"[A-Za-z\u4e00-\u9fff]", question)
    if not letters or sum(ch.isascii() for ch in letters) / len(letters) < .8:
        return True
    queries = []
    for block in plan.get("blocks", []):
        if str(block.get("type", "")).upper() not in {"SEARCH", "BROWSE"}: continue
        value = block.get("params", {}).get("queries", block.get("params", {}).get("query", []))
        queries += value if isinstance(value, list) else [value]
    foreign = sum(bool(re.search(r"[\u4e00-\u9fff]", str(query))) for query in queries)
    return not queries or foreign <= max(1, len(queries) // 4)


def _retrieval_inputs_ok(plan):
    """Reject retrieval nodes that cannot produce a query at runtime."""
    for block in plan.get("blocks", []):
        if not isinstance(block, dict) or str(block.get("type", "")).upper() not in {"SEARCH", "BROWSE"}:
            continue
        params = block.get("params") or {}
        raw = params.get("queries", params.get("query", []))
        queries = raw if isinstance(raw, list) else [raw]
        has_query = any(str(query).strip() for query in queries)
        if not has_query and not params.get("queries_from_dependencies"):
            return False
        if params.get("queries_from_dependencies") and not block.get("depends_on") and not has_query:
            return False
    return True


def _query_key(value):
    """Match operationally identical queries without changing their semantics."""
    return " ".join(str(value).split()).casefold()


def _retrieval_queries_novel(plan, notebook):
    """Reject exact repeats across prior stages and parallel nodes."""
    seen = {_query_key(query) for query in notebook.attempted_queries()}
    for block in plan.get("blocks", []):
        if not isinstance(block, dict) or str(block.get("type", "")).upper() not in {"SEARCH", "BROWSE"}:
            continue
        params = block.get("params") or {}
        raw = params.get("queries", params.get("query", []))
        queries = raw if isinstance(raw, list) else [raw]
        for query in queries:
            key = _query_key(query)
            if key and key in seen:
                return False
            if key:
                seen.add(key)
    return True


async def _model_question(question):
    """Question modeling is a required role, so transient/empty output is retryable."""
    last_error = None
    for attempt in range(1, 4):
        try:
            model = _model("DIRECTOR")
            modeled = await ask_json(CONDITION_PROMPT, f"QUESTION:\n{question}",
                                     model, _director_tokens(model, 3072), attempts=1)
            if isinstance(modeled.get("conditions"), list) and modeled["conditions"]:
                record("role_result", role="question_modeler", attempt=attempt,
                       input={"question": question}, output=modeled)
                return modeled
            raise ValueError("Question Modeler returned no atomic conditions")
        except Exception as exc:
            last_error = exc
            record("role_error", role="question_modeler", attempt=attempt,
                   input={"question": question}, error=f"{type(exc).__name__}: {exc}")
            if attempt < 3:
                print(f"[QUESTION MODELER RETRY] attempt {attempt}/3 failed: "
                      f"{type(exc).__name__}: {str(exc)[:180]}; retrying", flush=True)
                await asyncio.sleep(attempt)
    raise last_error


async def build_stage(question, notebook, stage, max_stages):
    if not notebook.conditions:
        modeled = await _model_question(question)
        notebook.set_conditions(modeled.get("conditions"))
    state = notebook.prompt()
    base_user = f"QUESTION:\n{question}\n\nRESEARCH STATE:\n{state}\n\nSTAGE: {stage}/{max_stages}"
    user, issues, model_failures, validation_attempt = base_user, [], 0, 0
    while validation_attempt < 3:
        try:
            model = _model("DIRECTOR")
            plan = await ask_json(GRAPH_BUILDER_PROMPT, user, model,
                                  _director_tokens(model, 4096), attempts=1)
            record("role_result", role="builder", stage=stage,
                   input={"question": question, "research_state": state,
                          "stage": stage, "max_stages": max_stages}, output=plan)
        except Exception as exc:
            model_failures += 1
            issues = [f"model_error:{type(exc).__name__}"]
            print(f"[BUILDER RETRY] model call {model_failures}/3 failed: {issues[0]}; retrying", flush=True)
            if model_failures >= 3:
                break
            await asyncio.sleep(model_failures)
            continue
        validation_attempt += 1
        language_ok = _stage_one_language_ok(question, plan)
        answering = (plan.get("decision") == "answer"
                     and plan.get("answer_complete") is True
                     and bool(str(plan.get("best_guess", "")).strip()))
        grounded = bool(notebook.claims or notebook.inferences or notebook.passages)
        blocks = plan.get("blocks")
        blocks_ok = isinstance(blocks, list) and (bool(blocks) or answering)
        answer_shape_ok = plan.get("decision") != "answer" or (answering and blocks == [])
        answer_evidence_ok = plan.get("decision") != "answer" or grounded
        retrieval_ok = _retrieval_inputs_ok(plan)
        retrieval_novel = _retrieval_queries_novel(plan, notebook)
        issues = [name for name, ok in (("blocks", blocks_ok), ("answer_shape", answer_shape_ok),
                                        ("answer_evidence", answer_evidence_ok),
                                        ("retrieval_queries", retrieval_ok),
                                        ("retrieval_repetition", retrieval_novel),
                                        ("language", language_ok)) if not ok]
        if not issues:
            guess = str(plan.get("best_guess", "")).strip()
            prior = [str(item.get("best_guess", "")).strip() for item in notebook.builder_history[-2:]]
            if (plan.get("decision") != "answer" and plan.get("answer_complete") is True and grounded
                    and guess and len(prior) == 2
                    and prior[0].casefold() == prior[1].casefold() == guess.casefold()):
                plan.update({"decision": "answer", "blocks": [],
                             "rationale": (str(plan.get("rationale", "")) +
                                           " The Builder selected the same answer for three consecutive decisions; stop.").strip()})
            return plan
        print(f"[BUILDER RETRY] graph {validation_attempt}/3 violated: {', '.join(issues)}; retrying", flush=True)
        if not grounded:
            correction = (
                "RESEARCH STATE contains no fetched evidence, so you MUST NOT answer from model memory. "
                "Return decision:\"continue\", answer_complete:false, and a nonempty executable retrieval DAG. "
                "You may preserve a complete provisional value in best_guess, but SEARCH/BROWSE must discover "
                "the identity chain and requested attribute, with FETCH and SOLVE dependencies when extraction is needed."
            )
        else:
            correction = (
                "Repair the contract while preserving only conclusions supported by RESEARCH STATE. "
                "If the evidence is incomplete, continue with an executable graph."
            )
        repair = (
            f"Your previous JSON violated: {', '.join(issues)}.\n"
            f"INVALID JSON:\n{json.dumps(plan, ensure_ascii=False, default=str)[:6000]}\n\n"
            f"{correction}\n"
            "If answering from existing evidence, return exactly "
            '{"decision":"answer","answer_complete":true,"best_guess":"exact requested value","blocks":[]}. '
            "If continuing, every block must be exactly shaped as "
            '{"id":"unique","type":"SEARCH|BROWSE|FETCH|READ_PDF|CALCULATE|PYTHON|SOLVE|VERIFY",'
            '"params":{},"depends_on":[]}; SEARCH/BROWSE queries belong inside params.queries. '
            "Return only the corrected contract JSON."
        )
        user = f"{base_user}\n\n{repair}"
    raise ValueError(f"Builder returned no executable graph ({', '.join(issues)})")


async def solve_node(question, task, role, notebook, observations):
    bundle = _solver_bundle(observations, f"{question}\n{task}")
    state = notebook.solver_state()
    dependencies = _bounded_bundle(bundle)
    user = f"QUESTION:\n{question}\n\nYOUR ROLE: {role}\nYOUR TASK: {task}\n\nRESEARCH STATE:\n{state}\n\nDEPENDENCY OBSERVATIONS:\n{dependencies}"
    approx_tokens = (len(SOLVER_PROMPT) + len(user) + 2) // 3
    print(f"  [SOLVER INPUT] {len(user)} chars | ≈{approx_tokens} tokens", flush=True)
    record("role_input_budget", role="solver", chars=len(user), approx_tokens=approx_tokens,
           state_chars=len(state), dependency_chars=len(dependencies))
    recoverable = (APIConnectionError, APITimeoutError, TimeoutError, ConnectionError,
                   RuntimeError, ValueError, json.JSONDecodeError)
    primary_model = _model("SOLVER")
    primary_tokens = (int(env("DEEPSEEK_SOLVER_MAX_TOKENS", 8192))
                      if str(primary_model).startswith("deepseek") else 4096)
    for attempt in range(1, 3):
        attempt_tokens = min(primary_tokens * (2 ** (attempt - 1)), 32768)
        try:
            result = await ask_json(SOLVER_PROMPT, user, primary_model, attempt_tokens, attempts=1)
            record("role_result", role="solver", input={"question": question, "role": role,
                   "task": task, "research_state": notebook.solver_state(),
                   "dependency_observations": bundle}, output=result)
            return result
        except recoverable as exc:
            fallback_model = _model("FALLBACK_SOLVER", "deepseek-v4-pro")
            if attempt < 2:
                action = f"retrying {primary_model} with up to {min(primary_tokens * 2, 32768)} output tokens"
            elif fallback_model == primary_model:
                action = f"final retry on {fallback_model}"
            else:
                action = f"switching to {fallback_model}"
            print(f"  [SOLVE RETRY] attempt {attempt}/2 failed: {type(exc).__name__}: "
                  f"{str(exc)[:160]}; {action}", flush=True)
            record("role_error", role="solver", attempt=attempt,
                   input={"question": question, "role": role, "task": task,
                          "research_state": notebook.solver_state(),
                          "dependency_observations": bundle},
                   error=f"{type(exc).__name__}: {exc}")
            if attempt < 2:
                await asyncio.sleep(attempt)
    fallback_model = _model("FALLBACK_SOLVER", "deepseek-v4-pro")
    fallback_tokens = (int(env("DEEPSEEK_SOLVER_MAX_TOKENS", 8192))
                       if str(fallback_model).startswith("deepseek") else 4096)
    if fallback_model == primary_model:
        fallback_tokens = min(primary_tokens * 2, 32768)
    report = await ask_json(SOLVER_PROMPT, user, fallback_model, fallback_tokens, attempts=1)
    record("role_result", role="solver_fallback", input={"question": question, "role": role,
           "task": task, "research_state": notebook.solver_state(),
           "dependency_observations": bundle}, output=report)
    report["degraded_model"] = True
    return report


async def verify_answer(question, candidate, claims, sources, conditions=None, hypotheses=None, inferences=None):
    """Verify one candidate against its proof slice, never the whole notebook."""
    candidate_key = candidate.casefold()
    candidate_hypotheses = [item for item in (hypotheses or [])
                            if str(item.get("entity", "")).casefold() in candidate_key
                            or candidate_key in str(item.get("entity", "")).casefold()]
    cited_ids = {evidence for item in candidate_hypotheses
                 for cover in item.get("coverage", []) if isinstance(cover, dict)
                 for evidence in cover.get("evidence_ids", [])}
    selected_claims = [item for item in claims if item.get("id") in cited_ids or any(
        str(entity).casefold() in candidate_key or candidate_key in str(entity).casefold()
        for entity in item.get("entities", []))]
    if not selected_claims:
        selected_claims = list(claims)[-12:]
    selected_claims = selected_claims[-16:]
    claim_ids = {item.get("id") for item in selected_claims}
    selected_inferences = [item for item in (inferences or [])
                           if item.get("id") in cited_ids or claim_ids.intersection(item.get("premise_ids", []))][-8:]
    source_keys = {str(item.get("source_block_id") or item.get("source_id") or "")
                   for item in selected_claims}
    selected_sources = {key: value for key, value in sources.items() if key in source_keys}
    if not selected_sources:
        selected_sources = dict(list(sources.items())[-4:])
    condition_checklist = "\n".join(
        f"{i+1}. [{c.get('id', '?')}] {c.get('description', '')}"
        for i, c in enumerate((conditions or [])[:16])
    )
    user = (
        f"QUESTION:\n{question}\n\n"
        f"ATOMIC CONDITIONS (every one must be verified):\n{condition_checklist}\n\n"
        f"CANDIDATE COVERAGE GRAPH:\n{json_text(candidate_hypotheses, 5000)}\n\n"
        f"CANDIDATE:\n{candidate}\n\n"
        f"CITED VERIFIED CLAIMS:\n{json_text(selected_claims, 8000)}\n\n"
        f"CITED DERIVED INFERENCES (validate every premise edge):\n{json_text(selected_inferences, 5000)}\n\n"
        f"SOURCE EXCERPTS:\n{json_text(selected_sources, 8000)}\n\n"
        f"Check each condition above. For each one, state whether the cited claims prove it. "
        f"Only accept if EVERY condition is proven AND the identity chain is closed "
        f"(the candidate IS the entity the question asks about, not just someone with a matching attribute)."
    )
    result = await ask_json(VERIFIER_PROMPT, user, _model("VERIFIER"), 2048)
    valid_condition_ids = {str(item.get("id", "")) for item in (conditions or [])}
    for key in ("supported_condition_ids", "unsupported_condition_ids", "contradicted_condition_ids"):
        values = result.get(key) if isinstance(result.get(key), list) else []
        result[key] = list(dict.fromkeys(str(value) for value in values
                                         if str(value) in valid_condition_ids))
    record("role_result", role="verifier", input={"question": question, "candidate": candidate,
           "claims": selected_claims, "sources": selected_sources, "conditions": conditions or [],
           "hypotheses": candidate_hypotheses, "inferences": selected_inferences}, output=result)
    return result
