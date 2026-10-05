"""Regression tests for idempotent finance-record saves and duplicate migration."""
import sqlite3
import unittest

from mockapps.seed import _deduplicate_finance_records, save_finance_record


class TestFinanceRecords(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor TEXT NOT NULL,
                invoice_number TEXT NOT NULL,
                amount INTEGER NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX idx_records_invoice_number ON records(invoice_number)"
        )

    def tearDown(self):
        self.conn.close()

    def test_repeated_save_updates_instead_of_duplicating(self):
        first_id = save_finance_record(
            self.conn, "Globex", "INV-G-8", 41000, "2026-10-18"
        )
        second_id = save_finance_record(
            self.conn, "Globex", "INV-G-8", 42000, "2026-10-19"
        )
        self.conn.commit()

        rows = self.conn.execute(
            "SELECT * FROM records WHERE invoice_number='INV-G-8'"
        ).fetchall()
        self.assertEqual(first_id, second_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 42000)
        self.assertEqual(rows[0]["due_date"], "2026-10-19")

    def test_retry_does_not_reset_paid_status(self):
        self.conn.execute(
            """INSERT INTO records (vendor, invoice_number, amount, due_date, status)
               VALUES ('Globex', 'INV-G-8', 41000, '2026-10-18', 'PAID')"""
        )
        record_id = save_finance_record(
            self.conn, "Globex", "INV-G-8", 41000, "2026-10-18"
        )
        row = self.conn.execute(
            "SELECT id, status FROM records WHERE invoice_number='INV-G-8'"
        ).fetchone()
        self.assertEqual(record_id, row["id"])
        self.assertEqual(row["status"], "PAID")

    def test_duplicate_migration_keeps_one_row_and_preserves_paid(self):
        self.conn.execute("DROP INDEX idx_records_invoice_number")
        for status in ("UNPAID", "PAID", "UNPAID"):
            self.conn.execute(
                """INSERT INTO records (vendor, invoice_number, amount, due_date, status)
                   VALUES ('Globex', 'INV-G-8', 41000, '2026-10-18', ?)""",
                (status,),
            )

        _deduplicate_finance_records(self.conn)
        self.conn.execute(
            "CREATE UNIQUE INDEX idx_records_invoice_number ON records(invoice_number)"
        )
        row = self.conn.execute(
            "SELECT * FROM records WHERE invoice_number='INV-G-8'"
        ).fetchone()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1
        )
        self.assertEqual(row["status"], "PAID")

    def test_migration_refuses_to_discard_conflicting_invoice_data(self):
        self.conn.execute("DROP INDEX idx_records_invoice_number")
        for amount in (41000, 42000):
            self.conn.execute(
                """INSERT INTO records (vendor, invoice_number, amount, due_date, status)
                   VALUES ('Globex', 'INV-G-8', ?, '2026-10-18', 'UNPAID')""",
                (amount,),
            )

        with self.assertRaisesRegex(RuntimeError, "Cannot safely merge"):
            _deduplicate_finance_records(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2
        )

    def test_invoice_number_is_required_for_idempotent_save(self):
        with self.assertRaisesRegex(ValueError, "invoice number is required"):
            save_finance_record(self.conn, "Globex", "  ", 41000, "2026-10-18")


if __name__ == "__main__":
    unittest.main()
