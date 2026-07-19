import json, unittest
from fastapi.testclient import TestClient

import src.web as web
from src.recorder import record
from src.runtime import env


class WebTests(unittest.TestCase):
    def test_home_and_health(self):
        client = TestClient(web.app)
        self.assertIn("BlockResearch", client.get("/").text)
        self.assertEqual(client.get("/api/health").json(), {"ok": True})

    def test_research_stream_uses_request_local_config(self):
        seen = {}

        async def fake_research(question, max_stages):
            seen.update(question=question, stages=max_stages, key=env("OPENAI_API_KEY"))
            record("stage_graph", stage=1, normalized_blocks=[])
            return {"answer": "ANSWER: Ada", "stages": 1, "outputs": {}}

        old = web.research
        web.research = fake_research
        try:
            response = TestClient(web.app).post("/api/research", json={
                "question": "Who is the answer?", "max_stages": 2,
                "config": {"OPENAI_API_KEY": "request-secret"},
            })
        finally:
            web.research = old
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        self.assertEqual(seen, {"question": "Who is the answer?", "stages": 2,
                                "key": "request-secret"})
        self.assertEqual([item["kind"] for item in events], ["stage_graph", "final"])
        self.assertNotIn("request-secret", response.text)


if __name__ == "__main__":
    unittest.main()
