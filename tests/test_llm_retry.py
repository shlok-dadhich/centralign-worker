"""Transient LLM transport errors are retried with backoff; permanent ones are not."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import llm


class TestLlmRetry(unittest.TestCase):
    def setUp(self):
        # Never actually sleep during tests.
        self._sleep = mock.patch.object(llm.time, "sleep", lambda *_: None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()

    def _resp(self):
        return {"message": {"role": "assistant", "content": "hello", "tool_calls": []}}

    def test_transient_error_is_retried_then_succeeds(self):
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("Internal Server Error (status code: 500)")
            return self._resp()

        with mock.patch.object(llm.ollama, "chat", flaky):
            msg = llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(msg["content"], "hello")
        self.assertEqual(calls["n"], 3)

    def test_permanent_error_is_not_retried(self):
        calls = {"n": 0}

        def bad(**kwargs):
            calls["n"] += 1
            raise RuntimeError("model 'nope' not found")

        with mock.patch.object(llm.ollama, "chat", bad):
            with self.assertRaises(RuntimeError):
                llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(calls["n"], 1)

    def test_gives_up_after_max_attempts(self):
        calls = {"n": 0}

        def always_500(**kwargs):
            calls["n"] += 1
            raise RuntimeError("Internal Server Error (status code: 503)")

        with mock.patch.object(llm.ollama, "chat", always_500):
            with self.assertRaises(RuntimeError):
                llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(calls["n"], llm.LLM_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
