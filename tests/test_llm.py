import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import src.llm as llm


def response(content="", reasoning="", finish="stop"):
    message = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


class FakeCompletions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.budgets = []

    async def create(self, **kwargs):
        self.budgets.append(kwargs["max_tokens"])
        return next(self.responses)


class LLMTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_client = llm._client

    def tearDown(self):
        llm._client = self.old_client

    async def test_reasoning_is_not_used_as_the_final_answer(self):
        completions = FakeCompletions([
            response(reasoning="unfinished", finish="length"),
            response("<answer>done</answer>", "complete reasoning"),
        ])
        llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        thinking, answer = await llm.ask("s", "u", max_tokens=32, retries=2)
        self.assertEqual((thinking, answer), ("complete reasoning", "done"))
        self.assertEqual(completions.budgets, [32, 1024])

    async def test_true_empty_response_is_explicit(self):
        completions = FakeCompletions([response()])
        llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with self.assertRaisesRegex(RuntimeError, "no final answer"):
            await llm.ask("s", "u", retries=1)

    async def test_think_only_content_is_not_an_answer(self):
        completions = FakeCompletions([
            response("<think>unfinished</think>", finish="length"),
            response("<answer>done</answer>"),
        ])
        llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        self.assertEqual((await llm.ask("s", "u", retries=2))[1], "done")

    async def test_json_retries_malformed_output(self):
        old_ask = llm.ask
        llm.ask = AsyncMock(side_effect=[("", "{}"), ("", '<answer>{"ok": true}</answer>')])
        try:
            self.assertEqual(await llm.ask_json("s", "u"), {"ok": True})
        finally:
            llm.ask = old_ask


if __name__ == "__main__":
    unittest.main()
