"""Unit tests for classify_action / classify_failure."""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.policy import classify_action, classify_failure


class TestClassifyAction(unittest.TestCase):
    def test_read_open_url(self):
        self.assertEqual(classify_action("open_url", {"url": "http://localhost:3001"}, "", ""), "READ")

    def test_write_type(self):
        self.assertEqual(classify_action("type_text", {"element_id": 1, "text": "x"}, "Vendor", ""), "WRITE")

    def test_write_submit_click(self):
        self.assertEqual(
            classify_action("click", {"element_id": 4}, "Submit Invoice", "http://localhost:3002/records/new"),
            "WRITE",
        )

    def test_financial_pay_button(self):
        self.assertEqual(
            classify_action("click", {"element_id": 2}, "Approve Payment", "http://localhost:3002/records/1/pay"),
            "FINANCIAL",
        )

    def test_financial_url(self):
        self.assertEqual(
            classify_action("click", {"element_id": 2}, "Continue", "http://localhost:3002/records/1/pay"),
            "FINANCIAL",
        )

    def test_unknown_tool(self):
        self.assertEqual(classify_action("nope", {}, "", ""), "UNKNOWN")


class TestClassifyFailure(unittest.TestCase):
    def test_timeout(self):
        self.assertEqual(classify_failure({"ok": False, "error": "timeout: wait", "observation": {}}), "RETRYABLE")

    def test_temp_500_banner(self):
        self.assertEqual(
            classify_failure(
                {"ok": True, "error": "", "observation": {"error_banner": "Temporary server error (500)"}}
            ),
            "RETRYABLE",
        )

    def test_stale(self):
        self.assertEqual(
            classify_failure({"ok": False, "error": "element not found: stale id 3", "observation": {}}),
            "RECOVERABLE",
        )

    def test_login(self):
        self.assertEqual(
            classify_failure(
                {"ok": False, "error": "", "observation": {"error_banner": "Invalid email or password."}}
            ),
            "FATAL",
        )

    def test_invalid_data(self):
        self.assertEqual(
            classify_failure({"ok": True, "error": "", "observation": {"error_banner": "Invalid data: amount"}}),
            "FATAL",
        )


if __name__ == "__main__":
    unittest.main()
