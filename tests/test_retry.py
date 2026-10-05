"""Retry cap is enforced in code (2 retries per tool+args signature)."""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.policy import RETRY_CAP, bump_retry, retries_left, retry_signature


class TestRetry(unittest.TestCase):
    def test_signature_stable(self):
        a = retry_signature("click", {"element_id": 1})
        b = retry_signature("click", {"element_id": 1})
        c = retry_signature("click", {"element_id": 2})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_cap_exceeded_on_third_failure(self):
        retries = {}
        sig = retry_signature("open_url", {"url": "http://x"})
        _, ex1 = bump_retry(retries, sig)
        _, ex2 = bump_retry(retries, sig)
        used3, ex3 = bump_retry(retries, sig)
        self.assertFalse(ex1)
        self.assertFalse(ex2)
        self.assertTrue(ex3)
        self.assertEqual(used3, RETRY_CAP + 1)
        self.assertEqual(retries_left(retries, sig), 0)

    def test_separate_signatures(self):
        retries = {}
        s1 = retry_signature("click", {"element_id": 1})
        s2 = retry_signature("click", {"element_id": 9})
        bump_retry(retries, s1)
        bump_retry(retries, s1)
        bump_retry(retries, s1)
        used, exceeded = bump_retry(retries, s2)
        self.assertEqual(used, 1)
        self.assertFalse(exceeded)


if __name__ == "__main__":
    unittest.main()
