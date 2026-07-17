import unittest
import io, zipfile

from src import main
import src.executor as executor
import src.research as research_module
from src.executor import _dependency_queries, normalize_graph
from src.director import _stage_one_language_ok
from src.research import _candidate
from src.notebook import ResearchNotebook
from src.tools import _docx_text, _expand_queries, calculate, run_python


class CoreTests(unittest.IsolatedAsyncioTestCase):
    def test_answer_extraction(self):
        self.assertEqual(main.extract_answer("ANSWER: 109 — EURO 2016"), "109 — EURO 2016")

    def test_stage_graph_is_canonical_and_has_solver(self):
        graph = normalize_graph({"blocks": [{"id": "q", "type": "BROWSE", "params": {"queries": ["x"]}}]}, 2)
        self.assertEqual(graph[0]["id"], "s2_q")
        self.assertEqual(graph[-1]["type"], "SOLVE")
        self.assertEqual(graph[-1]["depends_on"], ["s2_q"])

    def test_terminal_tool_is_closed_by_sink_solver(self):
        graph = normalize_graph({"blocks": [
            {"id": "strategy", "type": "SOLVE", "params": {}},
            {"id": "search", "type": "BROWSE", "params": {}, "depends_on": ["strategy"]},
        ]}, 1)
        self.assertEqual(graph[-1]["type"], "SOLVE")
        self.assertEqual(graph[-1]["depends_on"], ["s1_search"])

    def test_english_stage_one_rejects_unjustified_foreign_query_cluster(self):
        plan = {"blocks": [{"type": "BROWSE", "params": {"queries": ["医学 奖学金", "医生 朋友", "doctor scholarship"]}}]}
        self.assertFalse(_stage_one_language_ok("Which doctor won a scholarship?", plan))

    def test_no_match_sentinel_is_not_an_answer(self):
        self.assertEqual(_candidate("No match found"), "")

    def test_solver_queries_flow_into_dependent_browse(self):
        observations = {"strategy": {"queries": ["source vocabulary", "rare relation pair"]}}
        self.assertEqual(_dependency_queries(observations), ["source vocabulary", "rare relation pair"])

    def test_quoted_search_has_relaxed_variant(self):
        self.assertIn("mascot named by Joanna", _expand_queries(['"mascot" "named by Joanna"']))

    def test_docx_is_extracted_instead_of_treated_as_html(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "<w:document><w:t>Tourist arrivals 5.4%</w:t></w:document>")
        self.assertIn("Tourist arrivals 5.4%", _docx_text(buffer.getvalue()))

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
        self.assertIn("candidate_condition_graph", notebook.prompt())

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

    async def test_solver_receives_same_stage_dependency_output(self):
        seen = {}
        async def fake_tool(_block, _observations=None): return {"_type": "FETCH", "text": "The answer is Ada."}
        async def fake_audit(_q, _n, _o):
            return {"claims": [{"claim": "The answer is Ada", "quote": "The answer is Ada", "source_id": "s1_page"}]}
        async def fake_solve(_q, _task, _role, notebook, observations):
            seen.update(observations)
            return {"reasoning": "supported", "answer_candidate": "Ada", "support_claim_ids": [notebook.claims[0]["id"]]}
        old = executor._tool, executor.audit_evidence, executor.solve_node
        executor._tool, executor.audit_evidence, executor.solve_node = fake_tool, fake_audit, fake_solve
        try:
            plan = {"blocks": [
                {"id": "page", "type": "FETCH", "params": {"url": "https://example.com"}},
                {"id": "solve", "type": "SOLVE", "params": {}, "depends_on": ["page"]},
            ]}
            notebook = ResearchNotebook()
            outputs, _ = await executor.execute_stage("Who?", plan, notebook, 1)
        finally:
            executor._tool, executor.audit_evidence, executor.solve_node = old
        self.assertIn("s1_page", seen)
        self.assertEqual(outputs["s1_solve"]["answer_candidate"], "Ada")
        self.assertEqual(notebook.claims[0]["level"], "verified")

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

    async def test_research_builds_a_new_graph_each_stage(self):
        stages = []
        async def fake_build(_q, _n, stage, _max):
            stages.append(stage)
            return {"objective": f"stage {stage}", "blocks": [{"id": "solve", "type": "SOLVE", "params": {}}]}
        async def fake_execute(_q, _p, _n, stage, _build):
            return {f"s{stage}_solve": {"answer_candidate": "Ada", "support_claim_ids": []}}, []
        async def fake_verify(*_args): return {"accepted": len(stages) == 2, "reason": "continue"}
        old = research_module.build_stage, research_module.execute_stage, research_module.verify_answer
        research_module.build_stage, research_module.execute_stage, research_module.verify_answer = fake_build, fake_execute, fake_verify
        try:
            result = await research_module.research("Who?", 3)
        finally:
            research_module.build_stage, research_module.execute_stage, research_module.verify_answer = old
        self.assertEqual(stages, [1, 2])
        self.assertEqual(result["answer"], "ANSWER: Ada")
        self.assertEqual([node["kind"] for node in result["research_state"]["graph"]], ["BUILD", "VERIFY", "BUILD", "VERIFY"])

    async def test_rejected_candidate_is_not_returned_as_fallback(self):
        async def fake_build(_q, _n, stage, _max):
            return {"objective": str(stage), "blocks": [{"id": "solve", "type": "SOLVE", "params": {}}]}
        async def fake_execute(_q, _p, _n, stage, _build):
            return {f"s{stage}_solve": {"answer_candidate": "Wrong", "support_claim_ids": []}}, []
        async def fake_verify(*_args): return {"accepted": False, "reason": "unsupported"}
        old = research_module.build_stage, research_module.execute_stage, research_module.verify_answer
        research_module.build_stage, research_module.execute_stage, research_module.verify_answer = fake_build, fake_execute, fake_verify
        try:
            result = await research_module.research("Who?", 1)
        finally:
            research_module.build_stage, research_module.execute_stage, research_module.verify_answer = old
        self.assertEqual(result["answer"], "NEEDS_EVIDENCE: no supported answer candidate")

    async def test_verifier_failure_cannot_leak_unverified_candidate(self):
        async def fake_build(_q, _n, stage, _max):
            return {"objective": str(stage), "conditions": [{"id": "k1", "description": "answer"}],
                    "blocks": [{"id": "solve", "type": "SOLVE", "params": {}}]}
        async def fake_execute(_q, _p, _n, stage, _build):
            return {f"s{stage}_solve": {"answer_candidate": "Intermediate Anchor", "support_claim_ids": []}}, []
        async def fake_verify(*_args): raise RuntimeError("verifier failed")
        old = research_module.build_stage, research_module.execute_stage, research_module.verify_answer
        research_module.build_stage, research_module.execute_stage, research_module.verify_answer = fake_build, fake_execute, fake_verify
        try:
            result = await research_module.research("Who?", 1)
        finally:
            research_module.build_stage, research_module.execute_stage, research_module.verify_answer = old
        self.assertEqual(result["answer"], "")
        self.assertIn("verifier failed", result["error"])

    def test_calculate_is_restricted(self):
        self.assertEqual(calculate("ceil(1037 * 0.04)")["value"], 42)
        self.assertIn("error", calculate("__import__('os')"))

    async def test_python_is_restricted(self):
        self.assertEqual((await run_python("print(sum(range(5)))"))["stdout"].strip(), "10")
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

    def test_verifier_rejected_candidates_recorded_in_summary(self):
        notebook = ResearchNotebook()
        notebook.record_stage_summary(1, new_verified=0, new_leads=5,
                                       successful_pages=0, failed_fetches=2, candidate_changes=1,
                                       verifier_rejected=["Wrong Candidate"])
        prompt = notebook.prompt()
        self.assertIn("Wrong Candidate", prompt)

    def test_evidence_graph_includes_stage_summaries_in_to_dict(self):
        notebook = ResearchNotebook()
        notebook.record_stage_summary(1, new_verified=1, new_leads=3,
                                       successful_pages=1, failed_fetches=0, candidate_changes=0)
        state = notebook.to_dict()
        # to_dict should not crash; verify it has the core fields
        for key in ("claims", "leads", "hypotheses", "conditions", "sources",
                    "rejected_answers", "evidence_graph", "graph"):
            self.assertIn(key, state)


if __name__ == "__main__":
    unittest.main()
