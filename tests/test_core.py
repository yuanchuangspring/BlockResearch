import unittest
import json
import asyncio, io, zipfile

from src import main
import src.executor as executor
import src.research as research_module
import src.director as director_module
import src.tools as tools_module
from src.executor import _dependency_queries, _dependency_urls, _rank_stratified, normalize_graph
from src.director import _retrieval_inputs_ok, _solver_bundle, _stage_one_language_ok
from src.research import _candidate
from src.notebook import ResearchNotebook
from src.tools import _docx_text, _xlsx_text, _expand_queries, _merge_results, _passage, calculate, run_python
from src.context import compact_source, preview_one
from src.llm import _consume_stream
from src.retrieval import _compact_cards


class CoreTests(unittest.IsolatedAsyncioTestCase):
    def test_portfolio_tracks_leader_and_verification_failures(self):
        notebook = ResearchNotebook()
        notebook.record_candidates(1, {"best_guess": "Ada", "candidates": [
            {"name": "Ada", "status": "plausible", "why": "lead"},
            {"name": "Grace", "status": "plausible", "why": "alternative"}]})
        notebook.record_verification(1, "Ada", {"accepted": False, "reason": "missing edge"})
        branches = notebook.research_portfolio()["live_branches"]
        ada = next(item for item in branches if item["candidate"] == "Ada")
        self.assertEqual(ada["verification_failures"], 1)
        self.assertEqual(len(branches), 2)

    def test_answer_extraction(self):
        self.assertEqual(main.extract_answer("ANSWER: 109 — EURO 2016"), "109 — EURO 2016")

    def test_answer_extraction_preserves_long_complete_lists(self):
        answer = "group: " + ", ".join(f"item-{i}" for i in range(80))
        self.assertGreater(len(answer), 200)
        self.assertEqual(main.extract_answer(f"ANSWER: {answer}"), answer)

    def test_stage_graph_does_not_force_a_solver(self):
        graph = normalize_graph({"blocks": [{"id": "q", "type": "BROWSE", "params": {"queries": ["x"]}}]}, 2)
        self.assertEqual(graph[0]["id"], "s2_q")
        self.assertEqual([item["type"] for item in graph], ["BROWSE"])

    def test_builder_rejects_search_without_queries(self):
        self.assertFalse(_retrieval_inputs_ok({"blocks": [
            {"id": "q", "type": "SEARCH", "params": {"n": 10}}
        ]}))
        self.assertTrue(_retrieval_inputs_ok({"blocks": [
            {"id": "q", "type": "SEARCH", "params": {"queries": ["Ada biography"]}}
        ]}))
        self.assertTrue(_retrieval_inputs_ok({"blocks": [
            {"id": "solve", "type": "SOLVE", "params": {}},
            {"id": "q", "type": "SEARCH", "params": {"queries_from_dependencies": True},
             "depends_on": ["solve"]}
        ]}))

    def test_builder_controls_where_solver_is_used(self):
        graph = normalize_graph({"blocks": [
            {"id": "strategy", "type": "SOLVE", "params": {}},
            {"id": "search", "type": "BROWSE", "params": {}, "depends_on": ["strategy"]},
        ]}, 1)
        self.assertEqual([item["type"] for item in graph], ["SOLVE", "BROWSE"])
        self.assertEqual(graph[-1]["depends_on"], ["s1_strategy"])

    def test_english_stage_one_rejects_unjustified_foreign_query_cluster(self):
        plan = {"blocks": [{"type": "BROWSE", "params": {"queries": ["医学 奖学金", "医生 朋友", "doctor scholarship"]}}]}
        self.assertFalse(_stage_one_language_ok("Which doctor won a scholarship?", plan))

    async def test_question_modeling_precedes_builder_and_conditions_persist(self):
        outputs = [
            {"conditions": [{"id": "k1", "description": "the employee filed suit"},
                            {"id": "k2", "description": "the court certified the class"}]},
            {"objective": "search", "conditions": [],
             "blocks": [{"id": "a", "type": "SEARCH", "params": {"branch_id": "discover:a", "queries": ["clue a"]}},
                        {"id": "b", "type": "SEARCH", "params": {"branch_id": "discover:b", "queries": ["clue b"]}}]},
        ]
        async def fake_ask(*_args, **_kwargs): return outputs.pop(0)
        old = director_module.ask_json
        director_module.ask_json = fake_ask
        notebook = ResearchNotebook()
        try:
            plan = await director_module.build_stage("Who matches both clues?", notebook, 1, 8)
        finally:
            director_module.ask_json = old
        self.assertEqual([item["id"] for item in notebook.conditions], ["k1", "k2"])
        self.assertTrue(plan["blocks"])

    async def test_question_modeler_retries_timeout_and_empty_conditions(self):
        outputs = [TimeoutError("slow"), {"conditions": []},
                   {"conditions": [{"id": "k1", "description": "answer"}]}]
        async def fake_ask(*_args, **_kwargs):
            value = outputs.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        old = director_module.ask_json
        director_module.ask_json = fake_ask
        try:
            modeled = await director_module._model_question("Who?")
        finally:
            director_module.ask_json = old
        self.assertEqual(modeled["conditions"][0]["id"], "k1")
        self.assertFalse(outputs)

    async def test_builder_retries_model_json_failure(self):
        calls = 0
        async def fake_ask(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"conditions": [{"id": "k1", "description": "answer attribute"}]}
            if calls == 2:
                raise ValueError("bad json")
            return {"objective": "recover", "blocks": [
                {"id": "a", "type": "SEARCH", "params": {"branch_id": "discover:a", "queries": ["clue a"]}},
                {"id": "b", "type": "SEARCH", "params": {"branch_id": "discover:b", "queries": ["clue b"]}}]}
        old = director_module.ask_json
        director_module.ask_json = fake_ask
        try:
            plan = await director_module.build_stage("Who?", ResearchNotebook(), 1, 8)
        finally:
            director_module.ask_json = old
        self.assertEqual(calls, 3)
        self.assertEqual(plan["objective"], "recover")

    async def test_builder_retries_empty_blocks(self):
        outputs = [
            {"conditions": [{"id": "k1", "description": "answer"}]},
            {"objective": "empty", "blocks": []},
            {"objective": "recover", "blocks": [{"id": "solve", "type": "SOLVE", "params": {}}]},
        ]
        async def fake_ask(*_args, **_kwargs): return outputs.pop(0)
        old = director_module.ask_json
        director_module.ask_json = fake_ask
        try:
            plan = await director_module.build_stage("Who?", ResearchNotebook(), 1, 8)
        finally:
            director_module.ask_json = old
        self.assertEqual(plan["objective"], "recover")
        self.assertFalse(outputs)

    async def test_builder_rejects_noncontract_answer_then_accepts_standard_answer(self):
        outputs = [{"conditions": [{"id": "k1", "description": "song"}]},
                   {"song": "Porz Goret"},
                   {"decision": "answer", "answer_complete": True,
                    "best_guess": "Porz Goret", "blocks": []}]
        async def fake_ask(*_args, **_kwargs): return outputs.pop(0)
        old = director_module.ask_json
        director_module.ask_json = fake_ask
        notebook = ResearchNotebook()
        notebook.store_evidence(0, {"seed": {"_type": "FETCH", "url": "https://example.test",
                                              "text": "The song is Porz Goret."}})
        try:
            plan = await director_module.build_stage("What song?", notebook, 1, 8)
        finally:
            director_module.ask_json = old
        self.assertEqual(plan, {"decision": "answer", "answer_complete": True,
                                "best_guess": "Porz Goret", "blocks": []})
        self.assertFalse(outputs)

    def test_no_match_sentinel_is_not_an_answer(self):
        self.assertEqual(_candidate("No match found"), "")

    def test_solver_queries_flow_into_dependent_browse(self):
        observations = {"strategy": {"queries": ["source vocabulary", "rare relation pair"]}}
        self.assertEqual(_dependency_queries(observations), ["source vocabulary", "rare relation pair"])

    def test_dynamic_fetch_accepts_only_urls_present_in_search_ancestors(self):
        observations = {
            "search": {"results": [{"url": "https://credible.example/a"}, {"url": "https://credible.example/b"},
                                   {"url": "https://echo.example/query-copy", "query_echo": True}]},
            "selector": {"urls": ["https://credible.example/b", "https://invented.example/x",
                                    "https://echo.example/query-copy"]},
        }
        self.assertEqual(_dependency_urls(observations), ["https://credible.example/b"])

    def test_dynamic_fetch_can_select_ranked_domain_diverse_results_without_llm(self):
        observations = {"search": {"results": [
            {"url": "https://a.example/one"}, {"url": "https://a.example/two"},
            {"url": "https://b.example/page"},
            {"url": "https://echo.example/copy", "query_echo": True},
        ]}}
        self.assertEqual(_dependency_urls(observations, auto_select=True),
                         ["https://a.example/one", "https://b.example/page"])

    def test_auto_fetch_keeps_top_results_and_samples_deeper_candidates(self):
        urls = [f"https://d{i}.example/page" for i in range(12)]
        chosen = _rank_stratified(urls, 5)
        self.assertEqual(chosen[:3], urls[:3])
        self.assertIn(urls[-1], chosen)
        self.assertEqual(len(chosen), 5)

    def test_query_batch_does_not_eagerly_double_every_quoted_query(self):
        self.assertEqual(_expand_queries(['"mascot" "named by Joanna"']), ['"mascot" "named by Joanna"'])

    def test_query_compiler_never_deletes_builder_semantics(self):
        self.assertEqual(_expand_queries(['"former employee" "class action" "class certified" settlement']),
                         ['"former employee" "class action" "class certified" settlement'])
        self.assertEqual(_expand_queries(['"song of the month" "album of the month" "2017" music']),
                         ['"song of the month" "album of the month" "2017" music'])

    def test_query_echo_is_ranked_after_a_real_page(self):
        echo = {"title": "Query copy", "url": "https://x.test/rare-relation-person-place-year-event"}
        real = {"title": "Archive record", "url": "https://archive.test/item/42"}
        rows = _merge_results([("brave", [echo, real])], "rare relation person place year event", 5)
        self.assertEqual(rows[0]["title"], "Archive record")

    def test_query_echo_detects_clue_stuffed_search_url(self):
        from src.tools import _query_echo
        item = {"url": "https://spam.test/search?q=unwanted+delivery+short+story+eyes"}
        self.assertTrue(_query_echo(item, '"unwanted delivery" "short story" eyes 2023'))

    def test_docx_is_extracted_instead_of_treated_as_html(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "<w:document><w:t>Tourist arrivals 5.4%</w:t></w:document>")
        self.assertIn("Tourist arrivals 5.4%", _docx_text(buffer.getvalue()))

    def test_xlsx_is_extracted_as_rows(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/sharedStrings.xml",
                             '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Country</t></si><si><t>Nigeria</t></si></sst>')
            archive.writestr("xl/worksheets/sheet1.xml",
                             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c><c><v>0.560</v></c></row></sheetData></worksheet>')
        self.assertIn("Country\tNigeria\t0.560", _xlsx_text(buffer.getvalue()))

    def test_snippet_is_lead_but_page_is_verified(self):
        notebook = ResearchNotebook()
        audit = {"claims": [{"claim": "Ada was born in 1815", "quote": "Ada was born in 1815", "source_id": "web"}]}
        notebook.add_node("AUDIT", {})
        notebook.integrate(audit, {"web": {"_type": "BROWSE", "results": [{"snippet": "Ada was born in 1815"}], "pages": []}}, "n1")
        self.assertEqual(len(notebook.leads), 1)
        notebook.add_node("AUDIT", {})
        notebook.integrate(audit, {"web": {"_type": "BROWSE", "results": [], "pages": [{"text": "Ada was born in 1815."}]}}, "n2")
        self.assertEqual(len(notebook.claims), 1)
        self.assertEqual(notebook.leads, [])

    def test_auditor_url_resolves_to_exact_fetched_page(self):
        notebook = ResearchNotebook()
        notebook.add_node("AUDIT", {})
        outputs = {"s1_web": {"_type": "BROWSE", "pages": [
            {"url": "https://example.com/article/", "text": "Ada was born in 1815."}
        ]}}
        audit = {"claims": [{"claim": "Ada was born in 1815", "quote": "Ada was born in 1815",
                              "source_id": "https://example.com/article"}]}
        notebook.integrate(audit, outputs, "n1", {"s1_web"})
        self.assertEqual(len(notebook.claims), 1)
        self.assertEqual(notebook.claims[0]["source_id"], "https://example.com/article")
        self.assertEqual(notebook.claims[0]["source_block_id"], "s1_web")

    def test_claim_from_multi_page_fetch_keeps_exact_page_and_passage(self):
        notebook = ResearchNotebook()
        notebook.add_node("SOLVE", {"stage": 1})
        outputs = {"s1_fetch": {"_type": "FETCH", "pages": [
            {"url": "https://primary.test/dream", "text": "Dream Blast hosts an Earth Day environment season."},
            {"url": "https://other.test/story", "text": "Another game also joined Earth Day."},
        ]}}
        audit = {"claims": [{"claim": "Dream Blast has an Earth Day season",
                              "quote": "Dream Blast hosts an Earth Day environment season",
                              "source_id": "s1_fetch", "entities": ["Dream Blast"]}]}
        notebook.integrate(audit, outputs, "n1", {"s1_fetch"})
        self.assertEqual(notebook.claims[0]["source_id"], "https://primary.test/dream")
        pinned = [item for item in notebook.passages if item.get("pinned")]
        self.assertEqual(pinned[0]["url"], "https://primary.test/dream")
        for i in range(50):
            notebook.store_evidence(2, {f"s2_{i}": {"_type": "FETCH", "url": f"https://noise/{i}",
                                                          "text": (f"noise {i} " * 100)}})
        self.assertTrue(any(item.get("url") == "https://primary.test/dream"
                            for item in notebook.evidence_frontier()))

    def test_auditor_numeric_condition_id_maps_to_canonical_id(self):
        notebook = ResearchNotebook()
        notebook.set_conditions([{"id": "k1", "description": "birth city"}])
        notebook.add_node("AUDIT", {})
        audit = {"claims": [{"claim": "Ada was born in London", "quote": "Ada was born in London",
                              "source_id": "page", "condition_ids": ["1", "missing"]}]}
        notebook.integrate(audit, {"page": {"_type": "FETCH", "text": "Ada was born in London"}}, "n1")
        self.assertEqual(notebook.claims[0]["condition_ids"], ["k1"])

    def test_search_results_persist_as_cross_stage_leads(self):
        notebook = ResearchNotebook()
        ids = notebook.add_search_leads({"s1_q": {"_type": "BROWSE", "results": [
            {"title": "Possible company", "snippet": "Signed a partnership", "url": "https://example.com"}
        ]}})
        self.assertEqual(len(ids), 1)
        self.assertEqual(notebook.leads[0]["level"], "lead")
        self.assertIn("Possible company", notebook.prompt())

    def test_evidence_graph_maps_candidate_to_conditions(self):
        notebook = ResearchNotebook()
        notebook.set_conditions([{"id": "k1", "description": "signed partnership"}])
        notebook.leads.append({"id": "c1", "claim": "possible partnership", "level": "lead", "source_id": "s"})
        notebook.record_plan({"hypotheses": [{"entity": "Acme", "coverage": [
            {"condition_id": "k1", "status": "lead", "evidence_ids": ["c1"]}
        ]}]})
        graph = notebook.evidence_graph()
        self.assertIn({"from": "entity:acme", "to": "k1", "type": "lead", "evidence_ids": ["c1"]}, graph["edges"])
        self.assertIn("candidate_ledger", notebook.prompt())

    def test_builder_frontier_preserves_distinct_search_routes(self):
        notebook = ResearchNotebook()
        for route in ("rare person", "event archive"):
            notebook.add_search_leads({route: {"_type": "SEARCH", "results": [
                {"title": f"{route}-{i}", "snippet": "named bridge", "url": f"https://x/{route}/{i}",
                 "query": route} for i in range(12)]}})
        state = json.loads(notebook.prompt())
        self.assertEqual({lead["query"] for lead in state["candidate_leads"]},
                         {"rare person", "event archive"})

    def test_rejected_answer_is_persistent_falsification(self):
        notebook = ResearchNotebook()
        notebook.reject_answer("Wrong", "unsupported")
        self.assertIn('"candidate": "Wrong"', notebook.prompt())

    def test_hypothesis_aliases_merge_and_share_rejection(self):
        notebook = ResearchNotebook()
        notebook.record_plan({"hypotheses": [{"entity": "Charles Ellis", "aliases": ["Charles Ellis Jr."], "coverage": []}]})
        notebook.record_plan({"hypotheses": [{"entity": "Charles Ellis, Ph.D.", "aliases": ["Charles Ellis Jr."], "coverage": []}]})
        notebook.reject_answer("Charles Ellis", "missing decisive evidence")
        self.assertEqual(1, len(notebook.hypotheses))
        self.assertTrue(notebook.hypotheses[0]["rejected_reason"])
        self.assertIn("charles ellis jr.", {item["candidate"] for item in notebook.rejected_answers})

    def test_generic_unknown_profile_is_not_a_candidate(self):
        notebook = ResearchNotebook()
        notebook.record_plan({"hypotheses": [{"entity": "Unknown stadium (likely Kenya)", "coverage": []},
                                                {"entity": "El Wak Stadium", "coverage": []}]})
        self.assertEqual(["El Wak Stadium"], [item["entity"] for item in notebook.hypotheses])

    def test_solver_cannot_promote_lead_to_verified(self):
        notebook = ResearchNotebook()
        notebook.leads.append({"id": "c1", "claim": "lead", "level": "lead", "source_id": "s"})
        notebook.record_plan({"hypotheses": [{"entity": "Acme", "coverage": [
            {"condition_id": "k1", "status": "verified", "evidence_ids": ["c1"]}
        ]}]})
        self.assertEqual(notebook.hypotheses[0]["coverage"][0]["status"], "lead")

    def test_verified_claim_cannot_cover_an_unrelated_condition(self):
        notebook = ResearchNotebook()
        notebook.claims.append({"id": "c1", "claim": "has a degree", "level": "verified",
                                "source_id": "s", "condition_ids": ["k2"]})
        notebook.record_plan({"hypotheses": [{"entity": "Ada", "coverage": [
            {"condition_id": "k1", "status": "verified", "evidence_ids": ["c1"]},
            {"condition_id": "k2", "status": "verified", "evidence_ids": ["c1"]},
        ]}]})
        statuses = {item["condition_id"]: item["status"] for item in notebook.hypotheses[0]["coverage"]}
        self.assertEqual(statuses, {"k1": "lead", "k2": "verified"})

    def test_grounded_inference_persists_and_covers_condition_as_derived(self):
        notebook = ResearchNotebook()
        notebook.set_conditions([{"id": "k1", "description": "event occurred three years later"}])
        notebook.claims += [
            {"id": "c1", "claim": "Event A occurred in 2007", "level": "verified", "source_id": "s1", "condition_ids": []},
            {"id": "c2", "claim": "Event B occurred in 2010", "level": "verified", "source_id": "s2", "condition_ids": []},
        ]
        notebook.record_plan({
            "inferences": [{"conclusion": "Event B occurred three years after Event A", "premise_ids": ["c1", "c2"],
                            "condition_ids": ["k1"], "entities": ["Event B"]}],
            "hypotheses": [{"entity": "Event B", "coverage": [{"condition_id": "k1", "evidence_ids": ["d1"]}]}],
        })
        self.assertEqual(notebook.inferences[0]["level"], "derived")
        self.assertEqual(notebook.hypotheses[0]["coverage"][0]["status"], "derived")
        claims, inferences = notebook.proof([], ["d1"])
        self.assertEqual({item["id"] for item in claims}, {"c1", "c2"})
        self.assertEqual([item["id"] for item in inferences], ["d1"])

    def test_inference_cannot_use_search_lead_as_premise(self):
        notebook = ResearchNotebook()
        notebook.leads.append({"id": "c1", "claim": "snippet", "level": "lead", "source_id": "search"})
        notebook.record_plan({"inferences": [{"conclusion": "invented join", "premise_ids": ["c1"]}]})
        self.assertEqual(notebook.inferences, [])

    def test_inference_rejects_partial_or_multi_condition_grounding(self):
        notebook = ResearchNotebook()
        notebook.set_conditions([{"id": "k1", "description": "one"}, {"id": "k2", "description": "two"}])
        notebook.claims.append({"id": "c1", "claim": "fact", "level": "verified", "source_id": "s", "condition_ids": []})
        notebook.record_plan({"inferences": [
            {"conclusion": "uses a missing premise", "premise_ids": ["c1", "c404"], "condition_ids": ["k1"]},
            {"conclusion": "overbroad", "premise_ids": ["c1"], "condition_ids": ["k1", "k2"]},
        ]})
        self.assertEqual(notebook.inferences, [])

    def test_passage_is_centered_on_dense_query_match(self):
        text = "intro " * 800 + "Four customers accounted for 72.8% of revenue in fiscal 2005." + " tail" * 800
        passage, score = _passage(text, "four customers accounted 72.8")
        self.assertGreater(score, 0)
        self.assertIn("Four customers accounted for 72.8%", passage)
        self.assertNotEqual(passage, text[:3000])

    def test_compact_cards_preserves_each_query_route(self):
        rows = ([{"query": "route a", "title": f"A{i}", "snippet": "x", "url": f"https://a/{i}"}
                 for i in range(5)] +
                [{"query": "route b", "title": f"B{i}", "snippet": "y", "url": f"https://b/{i}"}
                 for i in range(5)])
        cards = _compact_cards(rows, per_query=2)
        self.assertEqual([item["query"] for item in cards], ["route a", "route b"])
        self.assertEqual([len(item["results"]) for item in cards], [2, 2])

    def test_solver_search_view_drops_token_heavy_transport_fields(self):
        results = [{"title": f"Candidate {i}", "snippet": "clue " * 100,
                    "url": f"https://example.com/very/long/path/{i}?tracking=large",
                    "query": "rare relation", "rank": i, "backend": "brave",
                    "query_echo": False} for i in range(20)]
        bundle = _solver_bundle({"s1_q": {"_type": "SEARCH", "queries": ["rare relation"],
                                                   "results": results}})
        view = bundle["s1_q"]
        self.assertEqual(len(view["results"]), 8)
        self.assertEqual(view["result_count"], 20)
        self.assertNotIn("url", view["results"][0])
        self.assertNotIn("backend", view["results"][0])
        self.assertNotIn("query", view["results"][0])
        self.assertEqual(view["results"][0]["query_id"], 1)
        self.assertEqual(view["results"][0]["domain"], "example.com")

    async def test_solver_receives_same_stage_dependency_output(self):
        seen = {}
        async def fake_tool(_block, _observations=None): return {"_type": "FETCH", "text": "The answer is Ada."}
        async def fake_solve(_q, _task, _role, notebook, observations):
            seen.update(observations)
            return {"reasoning": "supported", "answer_candidate": "Ada", "support_claim_ids": [],
                    "claims": [{"claim": "The answer is Ada", "quote": "The answer is Ada",
                                "source_id": "s1_page"}]}
        old = executor._tool, executor.solve_node
        executor._tool, executor.solve_node = fake_tool, fake_solve
        try:
            plan = {"blocks": [
                {"id": "page", "type": "FETCH", "params": {"url": "https://example.com"}},
                {"id": "solve", "type": "SOLVE", "params": {}, "depends_on": ["page"]},
            ]}
            notebook = ResearchNotebook()
            outputs, _ = await executor.execute_stage("Who?", plan, notebook, 1)
        finally:
            executor._tool, executor.solve_node = old
        self.assertIn("s1_page", seen)
        self.assertEqual(outputs["s1_solve"]["answer_candidate"], "Ada")
        self.assertEqual(notebook.claims[0]["level"], "verified")

    async def test_search_many_promotes_total_backend_failure(self):
        async def failed(_query, _n):
            return {"error": "BRAVE_API_KEY is not configured", "results": []}
        old = tools_module.search
        tools_module.search = failed
        try:
            result = await tools_module.search_many(["one", "two"], 5)
        finally:
            tools_module.search = old
        self.assertEqual(result["results"], [])
        self.assertEqual(result["error"], "BRAVE_API_KEY is not configured")

    async def test_one_failed_solver_does_not_erase_parallel_branch(self):
        async def fake_solve(_q, task, _role, _notebook, observations):
            if task == "fail": raise ValueError("bad json")
            return {"reasoning": "joined" if observations else "ok", "hypotheses": [],
                    "answer_candidate": "", "support_claim_ids": []}
        old = executor.solve_node
        executor.solve_node = fake_solve
        try:
            plan = {"blocks": [
                {"id": "bad", "type": "SOLVE", "params": {"task": "fail"}},
                {"id": "good", "type": "SOLVE", "params": {"task": "good"}},
                {"id": "join", "type": "SOLVE", "params": {"task": "join"}, "depends_on": ["bad", "good"]},
            ]}
            outputs, _ = await executor.execute_stage("Who?", plan, ResearchNotebook(), 1)
        finally:
            executor.solve_node = old
        self.assertIn("bad json", outputs["s1_bad"]["error"])
        self.assertEqual(outputs["s1_good"]["reasoning"], "ok")
        self.assertEqual(outputs["s1_join"]["reasoning"], "joined")

    async def test_solver_retries_returned_but_unparseable_response(self):
        calls = 0
        async def fake_ask(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("model returned no valid JSON object")
            return {"memo": "recovered", "queries": [], "claims": [], "candidates": [],
                    "decisive_gap": "", "recommendation": "", "best_guess": "Ada"}
        async def no_sleep(*_args): pass
        old_ask, old_sleep = director_module.ask_json, director_module.asyncio.sleep
        director_module.ask_json, director_module.asyncio.sleep = fake_ask, no_sleep
        try:
            result = await director_module.solve_node("Who?", "answer", "expert",
                                                      ResearchNotebook(), {})
        finally:
            director_module.ask_json, director_module.asyncio.sleep = old_ask, old_sleep
        self.assertEqual(calls, 2)
        self.assertEqual(result["best_guess"], "Ada")

    async def test_deepseek_solver_has_reasoning_output_budget(self):
        seen = {}
        async def fake_ask(_system, _user, model, max_tokens, **_kwargs):
            seen.update(model=model, max_tokens=max_tokens)
            return {"memo": "ok", "claims": [], "candidates": [], "best_guess": ""}
        old_ask, old_model = director_module.ask_json, director_module._model
        director_module.ask_json = fake_ask
        director_module._model = lambda role, default=None: "deepseek-v4-pro"
        try:
            await director_module.solve_node("Who?", "answer", "expert", ResearchNotebook(), {})
        finally:
            director_module.ask_json, director_module._model = old_ask, old_model
        self.assertEqual(seen["max_tokens"], 8192)

    async def test_deepseek_length_retry_doubles_output_budget(self):
        budgets = []
        async def fake_ask(_system, _user, _model, max_tokens, **_kwargs):
            budgets.append(max_tokens)
            if len(budgets) == 1:
                raise RuntimeError("model produced no final answer (finish_reason=length)")
            return {"memo": "ok", "claims": [], "candidates": [], "best_guess": "Ada"}
        async def no_sleep(*_args): pass
        old_ask, old_model, old_sleep = director_module.ask_json, director_module._model, director_module.asyncio.sleep
        director_module.ask_json = fake_ask
        director_module._model = lambda role, default=None: "deepseek-v4-pro"
        director_module.asyncio.sleep = no_sleep
        try:
            result = await director_module.solve_node("Who?", "answer", "expert", ResearchNotebook(), {})
        finally:
            director_module.ask_json, director_module._model, director_module.asyncio.sleep = old_ask, old_model, old_sleep
        self.assertEqual(budgets, [8192, 16384])
        self.assertEqual(result["best_guess"], "Ada")

    async def test_solver_receives_direct_dependencies_not_redundant_ancestors(self):
        seen = {}
        async def fake_tool(block, _observations=None):
            return ({"_type": "SEARCH", "results": [{"url": "https://x.test", "title": "x"}]}
                    if block["type"] == "SEARCH" else
                    {"_type": "FETCH", "url": "https://x.test", "text": "useful page"})
        async def fake_solve(_q, _task, _role, _notebook, observations):
            seen.update(observations)
            return {"memo": "ok", "claims": [], "candidates": [], "best_guess": ""}
        old_tool, old_solve = executor._tool, executor.solve_node
        executor._tool, executor.solve_node = fake_tool, fake_solve
        try:
            await executor.execute_stage("Who?", {"blocks": [
                {"id": "search", "type": "SEARCH", "params": {"queries": ["x"]}},
                {"id": "fetch", "type": "FETCH", "params": {"url": "https://x.test"},
                 "depends_on": ["search"]},
                {"id": "solve", "type": "SOLVE", "params": {}, "depends_on": ["fetch"]},
            ]}, ResearchNotebook(), 1)
        finally:
            executor._tool, executor.solve_node = old_tool, old_solve
        self.assertEqual(set(seen), {"s1_fetch"})

    async def test_verifier_consumes_dependency_best_guess_not_placeholder(self):
        seen = {}
        async def fake_solve(*_args):
            return {"memo": "ranked", "best_guess": "Ada", "candidates": []}
        async def fake_verify(_q, candidate, *_args):
            seen["candidate"] = candidate
            return {"accepted": True, "reason": "supported"}
        old = executor.solve_node, executor.verify_answer
        executor.solve_node, executor.verify_answer = fake_solve, fake_verify
        try:
            outputs, _ = await executor.execute_stage("Who?", {"blocks": [
                {"id": "solve", "type": "SOLVE", "params": {}},
                {"id": "verify", "type": "VERIFY", "params": {"candidate": "best candidate from solver"},
                 "depends_on": ["solve"]},
            ]}, ResearchNotebook(), 1)
        finally:
            executor.solve_node, executor.verify_answer = old
        self.assertEqual(seen["candidate"], "Ada")
        self.assertTrue(outputs["s1_verify"]["accepted"])

    async def test_browse_leads_do_not_require_an_auditor(self):
        async def fake_tool(_block, _observations=None):
            return {"_type": "BROWSE", "results": [{"title": "Named candidate", "snippet": "useful lead",
                                                        "url": "https://example.com"}], "pages": []}
        async def fake_solve(*_args): return {"reasoning": "continued", "answer_candidate": "", "hypotheses": []}
        old = executor._tool, executor.solve_node
        executor._tool, executor.solve_node = fake_tool, fake_solve
        notebook = ResearchNotebook()
        try:
            outputs, _ = await executor.execute_stage("Who?", {"blocks": [
                {"id": "web", "type": "BROWSE", "params": {"queries": ["x"]}},
                {"id": "solve", "type": "SOLVE", "params": {}, "depends_on": ["web"]},
            ]}, notebook, 1)
        finally:
            executor._tool, executor.solve_node = old
        self.assertEqual(outputs["s1_solve"]["reasoning"], "continued")
        self.assertEqual(len(notebook.leads), 1)
        self.assertNotIn("AUDIT", {node["kind"] for node in notebook.graph})

    async def test_research_builds_a_new_graph_each_stage(self):
        stages = []
        async def fake_build(_q, _n, stage, _max):
            stages.append(stage)
            return {"objective": f"stage {stage}", "blocks": [{"id": "solve", "type": "SOLVE", "params": {}}]}
        async def fake_execute(_q, _p, _n, stage, _build):
            return {f"s{stage}_solve": {"answer_candidate": "Ada", "support_claim_ids": []}}, []
        old = research_module.build_stage, research_module.execute_stage
        research_module.build_stage, research_module.execute_stage = fake_build, fake_execute
        try:
            result = await research_module.research("Who?", 3)
        finally:
            research_module.build_stage, research_module.execute_stage = old
        self.assertEqual(stages, [1, 2, 3])
        self.assertEqual(result["answer"], "ANSWER: Ada")
        self.assertEqual([node["kind"] for node in result["research_state"]["graph"]], ["BUILD", "BUILD", "BUILD"])

    async def test_best_guess_is_returned_even_without_verification(self):
        async def fake_build(_q, _n, stage, _max):
            return {"objective": str(stage), "blocks": [{"id": "solve", "type": "SOLVE", "params": {}}]}
        async def fake_execute(_q, _p, _n, stage, _build):
            return {f"s{stage}_solve": {"answer_candidate": "Wrong", "support_claim_ids": []}}, []
        old = research_module.build_stage, research_module.execute_stage
        research_module.build_stage, research_module.execute_stage = fake_build, fake_execute
        try:
            result = await research_module.research("Who?", 1)
        finally:
            research_module.build_stage, research_module.execute_stage = old
        self.assertEqual(result["answer"], "ANSWER: Wrong")

    async def test_builder_best_guess_survives_a_tool_only_stage(self):
        async def fake_build(_q, _n, stage, _max):
            return {"objective": str(stage), "best_guess": "Intermediate Anchor",
                    "blocks": [{"id": "search", "type": "SEARCH", "params": {}}]}
        async def fake_execute(_q, _p, _n, stage, _build):
            return {f"s{stage}_search": {"_type": "SEARCH", "results": []}}, []
        old = research_module.build_stage, research_module.execute_stage
        research_module.build_stage, research_module.execute_stage = fake_build, fake_execute
        try:
            result = await research_module.research("Who?", 1)
        finally:
            research_module.build_stage, research_module.execute_stage = old
        self.assertEqual(result["answer"], "ANSWER: Intermediate Anchor")

    async def test_solver_cannot_silently_replace_builder_best_guess(self):
        async def fake_build(_q, _n, stage, _max):
            return {"objective": str(stage), "best_guess": "Builder Candidate",
                    "blocks": [{"id": "solve", "type": "SOLVE", "params": {}}]}
        async def fake_execute(_q, _p, _n, stage, _build):
            return {f"s{stage}_solve": {"best_guess": "Unsupported Solver Guess"}}, []
        old = research_module.build_stage, research_module.execute_stage
        research_module.build_stage, research_module.execute_stage = fake_build, fake_execute
        try:
            result = await research_module.research("Who?", 1)
        finally:
            research_module.build_stage, research_module.execute_stage = old
        self.assertEqual(result["answer"], "ANSWER: Builder Candidate")

    def test_calculate_is_restricted(self):
        self.assertEqual(calculate("ceil(1037 * 0.04)")["value"], 42)
        self.assertIn("error", calculate("__import__('os')"))

    async def test_python_is_restricted(self):
        self.assertEqual((await run_python("print(sum(range(5)))"))["stdout"].strip(), "10")
        result = await run_python("print(DATA['rows'][1])", {"rows": [3, 7]})
        self.assertEqual(result["stdout"].strip(), "7")
        self.assertIn("error", await run_python("import os"))

    def test_stage_summary_is_null_before_any_stage(self):
        notebook = ResearchNotebook()
        prompt = notebook.prompt()
        self.assertIn('"last_stage": null', prompt)

    def test_stage_summary_appears_in_prompt_after_recording(self):
        notebook = ResearchNotebook()
        notebook.record_stage_summary(1, new_verified=2, new_leads=12,
                                       successful_pages=3, failed_fetches=1, candidate_changes=0)
        prompt = notebook.prompt()
        self.assertIn('"new_verified_claims": 2', prompt)
        self.assertIn('"new_leads": 12', prompt)
        self.assertIn('"successful_pages": 3', prompt)
        self.assertIn('"failed_fetches": 1', prompt)

    def test_stage_summary_only_keeps_last_four(self):
        notebook = ResearchNotebook()
        for i in range(1, 7):
            notebook.record_stage_summary(i, new_verified=i, new_leads=i,
                                           successful_pages=0, failed_fetches=0, candidate_changes=0)
        self.assertEqual(len(notebook.stage_summaries), 4)
        self.assertEqual(notebook.stage_summaries[0]["stage"], 3)
        self.assertEqual(notebook.stage_summaries[-1]["stage"], 6)

    def test_action_ledger_exposes_zero_gain_retrieval_to_next_builder(self):
        notebook = ResearchNotebook()
        notebook.record_actions(1, {
            "focus_condition_ids": ["k2"],
            "blocks": [{"type": "SEARCH", "params": {"queries": ["rare phrase"]}},
                       {"type": "FETCH", "params": {"url": "https://example.com/page"}}],
        }, {}, information_gain=0)
        state = notebook.prompt()
        self.assertIn('"information_gain": 0', state)
        self.assertIn('"rare phrase"', state)
        self.assertIn('"k2"', state)

    def test_solver_state_excludes_search_lead_noise(self):
        notebook = ResearchNotebook()
        notebook.leads.append({"id": "c1", "claim": "SEO noise", "level": "lead", "source_id": "s"})
        self.assertNotIn("SEO noise", notebook.solver_state())

    def test_solver_bundle_budget_preserves_every_direct_dependency(self):
        bundle = {
            "search": {"_type": "SEARCH", "queries": ["q"], "results": [
                {"title": "candidate", "snippet": "A" * 300 + "Bana dynasty",
                 "domain": "example.test", "rank": 1}] * 8},
            "fetch_identity": {"_type": "FETCH", "pages": [{
                "url": "https://example.test/kundavai",
                "text": "X" * 2200 + "Rajaraja's sister married Vandiyadevan of the Bana dynasty"}]},
            "fetch_count": {"_type": "FETCH", "pages": [{
                "url": "https://example.test/bana",
                "text": "Y" * 1800 + "The list contains 10 known kings"}]},
        }
        packed = director_module._bounded_bundle(bundle, 5500)
        self.assertLessEqual(len(packed), 5500)
        self.assertIn("Vandiyadevan", packed)
        self.assertIn("10 known kings", packed)
        self.assertNotIn("_omitted", packed)

    def test_solver_bundle_selects_relevant_late_fetch_page(self):
        pages = [{"url": f"https://example.test/noise-{i}",
                  "text": "generic unrelated bibliography" * 80} for i in range(6)]
        pages[-1] = {"url": "https://example.test/cv", "text":
                     "First publication in The Practitioner: Decisive Article Title"}
        bundle = director_module._solver_bundle(
            {"fetch": {"_type": "FETCH", "pages": pages}},
            "identify the first publication in The Practitioner")
        packed = director_module._bounded_bundle(bundle, 3000)
        self.assertIn("Decisive Article Title", packed)

    def test_solver_bundle_selects_relevant_middle_pdf_hit(self):
        hits = [{"page": i, "text": "generic bibliography" * 80} for i in range(16)]
        hits[7] = {"page": 36, "text":
                   "First publication in The Practitioner: Decisive Middle Hit"}
        observations = {"fetch": {"_type": "FETCH", "pages": [{
            "url": "https://example.test/cv.pdf", "text": "CV", "search_hits": hits}]}}
        bundle = director_module._solver_bundle(
            observations, "identify first publication in The Practitioner")
        packed = director_module._bounded_bundle(bundle, 3000)
        self.assertIn("Decisive Middle Hit", packed)

    def test_solver_bundle_prioritizes_enumerated_ruler_passage(self):
        names = "Jayanandivarman\nVijayaditya I, son of Jayanandivarman\nMalladeva, son of Vijayaditya I\nVallavaraiyan Vandiyadevan"
        bundle = {"fetch": {"_type": "FETCH", "pages": [{
            "url": "https://example.test/bana", "text": "generic history " * 300,
            "search_hits": [
                {"term": "known", "text": "generic known history " * 100},
                {"term": "kings", "text": names},
            ]}]}}
        packed = director_module._bounded_bundle(bundle, 3000)
        self.assertIn("Jayanandivarman", packed)
        self.assertIn("Vallavaraiyan Vandiyadevan", packed)

    def test_solver_search_card_keeps_relation_match_beyond_snippet_prefix(self):
        snippet = ("张合静，硕士研究生，研究工作被ICASSP接收。" + "背景介绍。" * 80 +
                   "王文博，哈尔滨工业大学博士研究生，该研究工作被人工智能顶会AAAI 2024接收。")
        observations = {"search": {"_type": "SEARCH", "queries": ["智汇论坛 博士研究生 顶会接收"],
                                   "results": [{"title": "智汇论坛", "snippet": snippet,
                                                "url": "https://example.test/forum", "rank": 1}]}}
        bundle = director_module._solver_bundle(observations, "哪位博士研究生的研究工作被顶尖会议接收")
        packed = director_module._bounded_bundle(bundle, 3000)
        self.assertIn("王文博", packed)
        self.assertIn("AAAI 2024", packed)

    def test_solver_bundle_keeps_competing_directional_relations(self):
        observations = {"search": {"_type": "SEARCH", "queries": ["S16 棋士证书 同时开启"],
            "results": [
                {"title": "3月14日公告", "snippet": "S17赛季棋士证书和S16新赛季同时开启，持续12周。",
                 "url": "https://example.test/march", "rank": 1},
                {"title": "1月13日公告", "snippet": "S16赛季棋士证书和S16新赛季同时开启，持续8周。",
                 "url": "https://example.test/january", "rank": 2}]}}
        bundle = director_module._solver_bundle(
            observations, "哪一个新赛季开始时间与S16赛季棋士证书同时开启，并持续多少周")
        packed = director_module._bounded_bundle(bundle, 3000)
        self.assertIn("S17赛季棋士证书和S16新赛季", packed)
        self.assertIn("持续12周", packed)
        self.assertIn("S16赛季棋士证书和S16新赛季", packed)
        self.assertIn("持续8周", packed)

    def test_unextracted_source_survives_as_cross_stage_evidence(self):
        notebook = ResearchNotebook()
        text = "A" * 1700 + "MIDDLE CANDIDATE" + "B" * 3300 + "TAIL FACT"
        notebook.store_evidence(1, {"s1_fetch": {"_type": "FETCH", "url": "https://x.test", "text": text}})
        state = notebook.prompt()
        self.assertIn("evidence_frontier", state)
        self.assertIn("MIDDLE CANDIDATE", state)
        self.assertIn("TAIL FACT", state)

    def test_search_hits_do_not_discard_decisive_middle_table_row(self):
        notebook = ResearchNotebook()
        text = "HEADER match " + "A" * 2300 + "Sandra Gongora rank 19 bronze" + "B" * 2300
        notebook.store_evidence(1, {"fetch": {
            "_type": "FETCH", "url": "https://example.test/table", "text": text,
            "search_hits": [{"term": "match", "text": text}],
        }})
        self.assertTrue(any("Sandra Gongora rank 19 bronze" in item["text"]
                            for item in notebook.passages))

    def test_multi_page_fetch_preview_keeps_tail_evidence(self):
        value = {"_type": "FETCH", "pages": [{
            "url": "https://example.test/table",
            "text": "A" * 2600 + "Sandra Gongora rank 19",
        }]}
        preview = preview_one("fetch", value)
        self.assertIn("Sandra Gongora rank 19", preview)
        self.assertNotIn("ERROR empty output", preview)

    def test_compact_source_keeps_deep_term_centered_search_hit(self):
        hits = [{"term": f"term{i}", "text": "A" * 500 + f"term{i} evidence" + "B" * 2400}
                for i in range(8)]
        hits[-1]["text"] = "A" * 500 + "kings Jayanandivarman through Vandiyadevan" + "B" * 2400
        hits[-1]["term"] = "kings"
        compact = compact_source({"search_hits": hits}, 1800)
        rendered = json.dumps(compact, ensure_ascii=False)
        self.assertIn("Jayanandivarman through Vandiyadevan", rendered)
        self.assertEqual(len(compact["search_hits"]), 4)

    async def test_stream_timeout_is_idle_not_total_duration(self):
        class Choice:
            finish_reason = None
            delta = type("Delta", (), {"content": "x", "reasoning_content": None})()
        class Stream:
            def __init__(self): self.i = 0
            def __aiter__(self): return self
            async def __anext__(self):
                if self.i == 3: raise StopAsyncIteration
                self.i += 1
                await asyncio.sleep(.03)
                return type("Chunk", (), {"choices": [Choice()]})()
        async def request(): return Stream()
        content, _, _ = await _consume_stream(request(), idle_timeout=.05, max_seconds=.2)
        self.assertEqual(content, "xxx")

    async def test_stream_still_times_out_when_it_goes_silent(self):
        class Stream:
            def __aiter__(self): return self
            async def __anext__(self):
                await asyncio.sleep(.1)
        async def request(): return Stream()
        with self.assertRaises(TimeoutError):
            await _consume_stream(request(), idle_timeout=.02, max_seconds=.2)

    async def test_explicitly_rejected_fallback_is_not_returned(self):
        async def fake_build(_q, _n, _stage, _max):
            return {"objective": "verify", "best_guess": "Silver", "blocks": [
                {"id": "verify", "type": "VERIFY", "params": {"candidate": "Silver"}}]}
        async def fake_execute(_q, _p, notebook, stage, _build):
            verdict = {"_type": "VERIFY", "candidate": "Silver", "accepted": False,
                       "reason": "identity condition unsupported"}
            notebook.record_verification(stage, "Silver", verdict)
            return {f"s{stage}_verify": verdict}, []
        old = research_module.build_stage, research_module.execute_stage
        research_module.build_stage, research_module.execute_stage = fake_build, fake_execute
        try:
            result = await research_module.research("Which medal?", 1)
        finally:
            research_module.build_stage, research_module.execute_stage = old
        self.assertEqual(result["answer"], "NEEDS_EVIDENCE: no supported answer candidate")

    def test_candidate_memory_preserves_ranked_adviser_candidates(self):
        notebook = ResearchNotebook()
        notebook.record_candidates(2, {"best_guess": "Ada", "candidates": [
            {"name": "Ada", "status": "supported", "why": "two direct sources"},
            {"name": "Grace", "status": "plausible", "why": "date unresolved"},
        ]})
        state = notebook.prompt()
        self.assertIn('"name": "Ada"', state)
        self.assertIn('"best_count": 1', state)
        self.assertIn('"name": "Grace"', state)

    def test_candidate_cannot_be_contradicted_without_verified_claim(self):
        notebook = ResearchNotebook()
        notebook.record_candidates(1, {"candidates": [
            {"name": "Ada", "status": "supported", "why": "direct source"}]})
        notebook.record_candidates(2, {"candidates": [
            {"name": "Ada", "status": "contradicted", "why": "Grace also matches"}]})
        self.assertEqual(notebook.candidate_memory["ada"]["status"], "plausible")

    def test_only_verifier_can_promote_candidate_to_verified(self):
        notebook = ResearchNotebook()
        notebook.record_candidates(1, {"candidates": [
            {"name": "Ada", "status": "supported", "why": "adviser ranking"}]})
        self.assertEqual(notebook.candidate_memory["ada"]["status"], "plausible")
        notebook.record_verification(1, "Ada", {"accepted": True, "reason": "all conditions cited"})
        self.assertEqual(notebook.candidate_memory["ada"]["status"], "verified")

    def test_candidate_contradiction_requires_verified_claim_id(self):
        notebook = ResearchNotebook()
        notebook.claims.append({"id": "c1", "claim": "Ada failed k1", "level": "verified"})
        notebook.record_candidates(1, {"candidates": [{
            "name": "Ada", "status": "contradicted", "why": "failed k1",
            "contradiction_claim_ids": ["c1"]}]})
        self.assertEqual(notebook.candidate_memory["ada"]["status"], "contradicted")
        self.assertEqual(notebook.candidate_memory["ada"]["contradiction_claim_ids"], ["c1"])

    def test_verification_preserves_partial_condition_result(self):
        notebook = ResearchNotebook()
        notebook.record_verification(2, "Ada and Grace", {
            "accepted": False, "reason": "k2 is missing",
            "supported_condition_ids": ["k1"], "unsupported_condition_ids": ["k2"],
            "contradicted_condition_ids": []})
        saved = notebook.verification_history[-1]
        self.assertEqual(saved["supported_condition_ids"], ["k1"])
        self.assertEqual(saved["unsupported_condition_ids"], ["k2"])
        self.assertEqual(saved["contradicted_condition_ids"], [])

    async def test_builder_can_end_before_stage_limit_with_best_guess(self):
        async def fake_build(*_args):
            return {"decision": "answer", "best_guess": "Ada", "objective": "stop", "blocks": []}
        old = research_module.build_stage
        research_module.build_stage = fake_build
        try:
            result = await research_module.research("Who?", 8)
        finally:
            research_module.build_stage = old
        self.assertEqual(result["answer"], "ANSWER: Ada")
        self.assertEqual(result["stages"], 1)

    async def test_builder_third_identical_decision_stops(self):
        notebook = ResearchNotebook()
        notebook.set_conditions([{"id": "k1", "description": "identity"}])
        notebook.store_evidence(1, {"seed": {"_type": "FETCH", "url": "https://example.test",
                                              "text": "Ada matches the identity clue."}})
        notebook.builder_history = [
            {"stage": 1, "best_guess": "Ada"}, {"stage": 2, "best_guess": "Ada"}]
        async def fake_ask(*_args, **_kwargs):
            return {"decision": "continue", "answer_complete": True,
                    "best_guess": "Ada", "objective": "more checking",
                    "blocks": [{"id": "q", "type": "SEARCH", "params": {"queries": ["Ada"]}}]}
        old = director_module.ask_json
        director_module.ask_json = fake_ask
        try:
            plan = await director_module.build_stage("Who?", notebook, 3, 8)
        finally:
            director_module.ask_json = old
        self.assertEqual(plan["decision"], "answer")
        self.assertEqual(plan["blocks"], [])

    async def test_builder_does_not_early_stop_on_repeated_partial_answer(self):
        notebook = ResearchNotebook()
        notebook.set_conditions([
            {"id": "k1", "description": "name the dynasty"},
            {"id": "k2", "description": "state the number of kings"},
        ])
        notebook.builder_history = [
            {"stage": 1, "best_guess": "Bana Dynasty"},
            {"stage": 2, "best_guess": "Bana Dynasty"},
        ]
        async def fake_ask(*_args, **_kwargs):
            return {"decision": "continue", "answer_complete": False,
                    "best_guess": "Bana Dynasty", "objective": "count the kings",
                    "blocks": [{"id": "q", "type": "SEARCH",
                                "params": {"queries": ["Bana kings list"]}}]}
        old = director_module.ask_json
        director_module.ask_json = fake_ask
        try:
            plan = await director_module.build_stage("Name the dynasty and number of kings", notebook, 3, 8)
        finally:
            director_module.ask_json = old
        self.assertEqual(plan["decision"], "continue")
        self.assertTrue(plan["blocks"])

    async def test_builder_rejects_ungrounded_direct_answer(self):
        outputs = [
            {"conditions": [{"id": "k1", "description": "requested amount"}]},
            {"decision": "answer", "answer_complete": True,
             "best_guess": "$1,487,753", "blocks": []},
            {"decision": "continue", "answer_complete": False,
             "best_guess": "", "objective": "retrieve evidence",
             "blocks": [{"id": "q", "type": "SEARCH",
                         "params": {"queries": ["film initial run gross"]}}]},
        ]
        async def fake_ask(*_args, **_kwargs): return outputs.pop(0)
        old = director_module.ask_json
        director_module.ask_json = fake_ask
        try:
            plan = await director_module.build_stage("How much did it gross?", ResearchNotebook(), 1, 8)
        finally:
            director_module.ask_json = old
        self.assertEqual(plan["decision"], "continue")
        self.assertFalse(outputs)

    async def test_builder_repair_for_ungrounded_malformed_answer_requires_retrieval(self):
        outputs = [
            {"conditions": [{"id": "k1", "description": "identify dynasty"},
                            {"id": "k2", "description": "state known king count"}]},
            {"answer": "Bana dynasty", "count": 5},
            {"decision": "continue", "answer_complete": False,
             "best_guess": "Bana dynasty, 5 known kings", "objective": "retrieve the chain",
             "blocks": [{"id": "q", "type": "SEARCH", "params": {
                 "queries": ["Brihadeeswarar Temple builder sister husband dynasty",
                             "Bana dynasty known kings list"]}, "depends_on": []}]},
        ]
        prompts = []
        async def fake_ask(_system, user, *_args, **_kwargs):
            prompts.append(user)
            return outputs.pop(0)
        old = director_module.ask_json
        director_module.ask_json = fake_ask
        try:
            plan = await director_module.build_stage("Name the dynasty and king count", ResearchNotebook(), 1, 8)
        finally:
            director_module.ask_json = old
        self.assertEqual(plan["decision"], "continue")
        self.assertTrue(plan["blocks"])
        self.assertIn("MUST NOT answer from model memory", prompts[-1])

    def test_verifier_rejected_candidates_recorded_in_summary(self):
        notebook = ResearchNotebook()
        notebook.record_stage_summary(1, new_verified=0, new_leads=5,
                                       successful_pages=0, failed_fetches=2, candidate_changes=1,
                                       verifier_rejected=["Wrong Candidate"])
        prompt = notebook.prompt()
        self.assertIn("Wrong Candidate", prompt)

    def test_support_level_matches_quote_across_newlines_in_text_field(self):
        notebook = ResearchNotebook()
        # Quote has words separated by a space, but in the source text
        # they appear on different lines (newline-separated).
        # This tests the fix for JSON-encoding breaking newline matching.
        output = {"_type": "FETCH", "url": "https://example.com",
                  "text": "Originalsprache\nDeutsch\n(\nWienerisch\n)\nErscheinungsjahre\n1975–1979\nLänge\n45\nMinuten"}
        level = notebook._support_level(
            {"claim": "series is in German", "quote": "Originalsprache Deutsch"}, output)
        self.assertEqual(level, "verified")

    def test_evidence_graph_includes_stage_summaries_in_to_dict(self):
        notebook = ResearchNotebook()
        notebook.record_stage_summary(1, new_verified=1, new_leads=3,
                                       successful_pages=1, failed_fetches=0, candidate_changes=0)
        state = notebook.to_dict()
        self.assertEqual(state["stage_summaries"][0]["new_verified_claims"], 1)
        # to_dict should not crash; verify it has the core fields
        for key in ("claims", "leads", "hypotheses", "conditions", "sources",
                    "rejected_answers", "evidence_graph", "graph"):
            self.assertIn(key, state)


if __name__ == "__main__":
    unittest.main()
