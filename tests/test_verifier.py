"""Verifier is deterministic and reads SQLite, not the LLM."""
import unittest
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.verifier as verifier
import mockapps.paths as app_paths
import mockapps.seed as seed
from mockapps.seed import reset_all, seed_finance
from mockapps.paths import FINANCE_DB
import sqlite3

from agent.verifier import resolve_vendor, verify


class TestVerifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db_dir = tempfile.TemporaryDirectory(prefix="centralign-verifier-tests-")
        db_dir = Path(cls._db_dir.name)
        app_paths.DATA = db_dir
        app_paths.PORTAL_DB = db_dir / "portal.db"
        app_paths.FINANCE_DB = db_dir / "finance.db"
        seed.DATA = app_paths.DATA
        seed.PORTAL_DB = app_paths.PORTAL_DB
        seed.FINANCE_DB = app_paths.FINANCE_DB
        verifier.PORTAL_DB = app_paths.PORTAL_DB
        verifier.FINANCE_DB = app_paths.FINANCE_DB
        global FINANCE_DB
        FINANCE_DB = app_paths.FINANCE_DB
        reset_all()

    @classmethod
    def tearDownClass(cls):
        cls._db_dir.cleanup()

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
        conn.execute("DROP INDEX idx_records_invoice_number")
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

    def test_rejected_payment_preserves_existing_paid_record(self):
        conn = sqlite3.connect(FINANCE_DB)
        conn.execute(
            "INSERT INTO records (vendor, invoice_number, amount, due_date, status) VALUES (?,?,?,?,?)",
            ("Acme Corp", "INV-1042", 84500, "2026-10-20", "PAID"),
        )
        conn.commit()
        conn.close()

        result = verify(
            "Find latest Acme Corp invoice, enter it and pay it",
            {},
            approved=False,
        )

        self.assertTrue(result["passed"], result)
        self.assertEqual(result["actual"]["status"], "PAID")

    def test_payment_status_is_not_assumed_without_approval(self):
        conn = sqlite3.connect(FINANCE_DB)
        conn.execute(
            "INSERT INTO records (vendor, invoice_number, amount, due_date, status) VALUES (?,?,?,?,?)",
            ("Acme Corp", "INV-1042", 84500, "2026-10-20", "PAID"),
        )
        conn.commit()
        conn.close()

        result = verify(
            "Find latest Acme Corp invoice, enter it and pay it",
            {},
            approved=None,
        )

        self.assertTrue(result["passed"], result)


if __name__ == "__main__":
    unittest.main()
