"""Verifier is deterministic and reads SQLite, not the LLM."""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mockapps.seed import reset_all, seed_finance
from mockapps.paths import FINANCE_DB
import sqlite3

from agent.verifier import resolve_vendor, verify


class TestVerifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_all()

    def setUp(self):
        seed_finance(reset=True)

    def test_acme_corp_from_goal(self):
        v, err = resolve_vendor("Find the latest invoice from Acme Corp and enter it", {})
        self.assertIsNone(err)
        self.assertEqual(v, "Acme Corp")

    def test_ambiguous_acme_requires_user_confirmed_fact(self):
        v, err = resolve_vendor("latest Acme invoice", {})
        self.assertIsNone(v)
        self.assertIsNotNone(err)
        v2, err2 = resolve_vendor("latest Acme invoice", {"vendor": "Acme Corp"})
        self.assertIsNone(v2)
        self.assertIsNotNone(err2)
        v3, err3 = resolve_vendor(
            "latest Acme invoice",
            {"vendor": "Acme Corp", "user_answer": "Acme Corp"},
        )
        self.assertEqual(v3, "Acme Corp")
        self.assertIsNone(err3)

    def test_vendor_fact_must_match_user_answer(self):
        v, err = resolve_vendor(
            "latest invoice",
            {"vendor": "Acme Corp", "user_answer": "Globex"},
        )
        self.assertIsNone(v)
        self.assertIsNotNone(err)

    def test_pass_when_record_matches(self):
        conn = sqlite3.connect(FINANCE_DB)
        conn.execute(
            "INSERT INTO records (vendor, invoice_number, amount, due_date, status) VALUES (?,?,?,?,?)",
            ("Acme Corp", "INV-1042", 84500, "2026-10-20", "UNPAID"),
        )
        conn.commit()
        conn.close()
        r = verify("Find the latest invoice from Acme Corp and enter it into finance", {})
        self.assertTrue(r["passed"], r)
        self.assertEqual(r["expected"]["invoice_number"], "INV-1042")
        self.assertEqual(r["expected"]["amount"], 84500)

    def test_fail_wrong_amount(self):
        conn = sqlite3.connect(FINANCE_DB)
        conn.execute(
            "INSERT INTO records (vendor, invoice_number, amount, due_date, status) VALUES (?,?,?,?,?)",
            ("Acme Corp", "INV-1042", 1, "2026-10-20", "UNPAID"),
        )
        conn.commit()
        conn.close()
        r = verify("Find the latest invoice from Acme Corp", {})
        self.assertFalse(r["passed"])
        self.assertTrue(any("amount" in d for d in r["diff"]))

    def test_fail_duplicates(self):
        conn = sqlite3.connect(FINANCE_DB)
        for _ in range(2):
            conn.execute(
                "INSERT INTO records (vendor, invoice_number, amount, due_date, status) VALUES (?,?,?,?,?)",
                ("Acme Corp", "INV-1042", 84500, "2026-10-20", "UNPAID"),
            )
        conn.commit()
        conn.close()
        r = verify("Find the latest invoice from Acme Corp", {})
        self.assertFalse(r["passed"])
        self.assertTrue(any("exactly 1" in d for d in r["diff"]))

    def test_pay_status(self):
        conn = sqlite3.connect(FINANCE_DB)
        conn.execute(
            "INSERT INTO records (vendor, invoice_number, amount, due_date, status) VALUES (?,?,?,?,?)",
            ("Acme Corp", "INV-1042", 84500, "2026-10-20", "UNPAID"),
        )
        conn.commit()
        conn.close()
        r = verify("Find latest Acme Corp invoice, enter it and pay it", {}, approved=True)
        self.assertFalse(r["passed"])
        r2 = verify("Find latest Acme Corp invoice, enter it and pay it", {}, approved=False)
        self.assertTrue(r2["passed"], r2)


if __name__ == "__main__":
    unittest.main()
