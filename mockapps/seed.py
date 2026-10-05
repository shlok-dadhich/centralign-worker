"""Create/reset SQLite DBs. Invoice rows are inserted in a shuffled order so
the portal list is NOT date-sorted (the agent must compare dates itself)."""
import random
import sqlite3
from pathlib import Path

from mockapps.paths import DATA, FINANCE_DB, PORTAL_DB

# Amounts are integer rupees. Latest Acme Corp is the only row the PLAN specifies fully.
INVOICES = [
    {"vendor": "Acme Corp", "number": "INV-1040", "date": "2026-09-14", "amount": 61200, "due_date": "2026-09-30", "status": "UNPAID"},
    {"vendor": "Acme Corp", "number": "INV-1042", "date": "2026-10-03", "amount": 84500, "due_date": "2026-10-20", "status": "UNPAID"},
    {"vendor": "Acme Corp", "number": "INV-1031", "date": "2026-08-20", "amount": 22000, "due_date": "2026-09-05", "status": "PAID"},
    {"vendor": "Acme Labs", "number": "INV-AL-19", "date": "2026-09-28", "amount": 15000, "due_date": "2026-10-12", "status": "UNPAID"},
    {"vendor": "Globex", "number": "INV-G-7", "date": "2026-09-01", "amount": 33000, "due_date": "2026-09-15", "status": "UNPAID"},
    {"vendor": "Globex", "number": "INV-G-8", "date": "2026-10-01", "amount": 41000, "due_date": "2026-10-18", "status": "UNPAID"},
    {"vendor": "Umbrella", "number": "INV-U-3", "date": "2026-08-10", "amount": 99000, "due_date": "2026-08-25", "status": "PAID"},
]


def format_inr(n: int) -> str:
    s = str(int(n))
    if len(s) <= 3:
        return "₹" + s
    body = s[-3:]
    s = s[:-3]
    while s:
        body = s[-2:] + "," + body
        s = s[:-2]
    return "₹" + body


def _connect(path: Path) -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def seed_portal(reset: bool = True) -> None:
    conn = _connect(PORTAL_DB)
    if reset:
        conn.execute("DROP TABLE IF EXISTS invoices")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT NOT NULL,
            number TEXT NOT NULL,
            date TEXT NOT NULL,
            amount INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL
        )"""
    )
    if conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0:
        rows = list(INVOICES)
        random.Random(42).shuffle(rows)
        for r in rows:
            conn.execute(
                "INSERT INTO invoices (vendor, number, date, amount, due_date, status) VALUES (?,?,?,?,?,?)",
                (r["vendor"], r["number"], r["date"], r["amount"], r["due_date"], r["status"]),
            )
    conn.commit()
    conn.close()


def seed_finance(reset: bool = True) -> None:
    conn = _connect(FINANCE_DB)
    try:
        if reset:
            conn.execute("DROP TABLE IF EXISTS records")
            conn.execute("DROP TABLE IF EXISTS settings")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor TEXT NOT NULL,
                invoice_number TEXT NOT NULL,
                amount INTEGER NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        defaults = {
            "fail_first_save": "0",
            "rename_button": "0",
            "fail_first_save_used": "0",
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        if reset:
            for k, v in defaults.items():
                conn.execute("UPDATE settings SET value=? WHERE key=?", (v, k))
        _deduplicate_finance_records(conn)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_records_invoice_number ON records(invoice_number)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _deduplicate_finance_records(conn: sqlite3.Connection) -> None:
    duplicates = conn.execute(
        """SELECT invoice_number FROM records
           GROUP BY invoice_number HAVING COUNT(*) > 1"""
    ).fetchall()
    for duplicate in duplicates:
        invoice_number = duplicate["invoice_number"]
        rows = conn.execute(
            """SELECT id, vendor, amount, due_date, status FROM records
               WHERE invoice_number=?
               ORDER BY CASE WHEN status='PAID' THEN 0 ELSE 1 END, id""",
            (invoice_number,),
        ).fetchall()
        details = {(row["vendor"], row["amount"], row["due_date"]) for row in rows}
        if len(details) != 1:
            raise RuntimeError(
                f"Cannot safely merge conflicting finance records for invoice {invoice_number!r}"
            )

        keep_id = rows[0]["id"]
        keep_status = "PAID" if any(row["status"] == "PAID" for row in rows) else rows[0]["status"]
        conn.execute("UPDATE records SET status=? WHERE id=?", (keep_status, keep_id))
        conn.execute(
            "DELETE FROM records WHERE invoice_number=? AND id<>?",
            (invoice_number, keep_id),
        )


def save_finance_record(
    conn: sqlite3.Connection,
    vendor: str,
    invoice_number: str,
    amount: int,
    due_date: str,
) -> int:
    """Create or correct an invoice record without duplicating it on retries."""
    invoice_number = invoice_number.strip()
    if not invoice_number:
        raise ValueError("invoice number is required")

    conn.execute(
        """INSERT INTO records (vendor, invoice_number, amount, due_date, status)
           VALUES (?, ?, ?, ?, 'UNPAID')
           ON CONFLICT(invoice_number) DO UPDATE SET
               vendor=excluded.vendor,
               amount=excluded.amount,
               due_date=excluded.due_date""",
        (vendor.strip(), invoice_number, amount, due_date.strip()),
    )
    row = conn.execute(
        "SELECT id FROM records WHERE invoice_number=?", (invoice_number,)
    ).fetchone()
    return int(row["id"])


def reset_all() -> None:
    seed_portal(reset=True)
    seed_finance(reset=True)


if __name__ == "__main__":
    reset_all()
    print("seeded", PORTAL_DB, "and", FINANCE_DB)
