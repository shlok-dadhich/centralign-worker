"""Deterministic verifier. Reads portal + finance SQLite. No LLM."""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from mockapps.paths import FINANCE_DB, PORTAL_DB


def _connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def portal_vendors() -> list[str]:
    conn = _connect(PORTAL_DB)
    rows = conn.execute("SELECT DISTINCT vendor FROM invoices").fetchall()
    conn.close()
    return [r["vendor"] for r in rows]


def resolve_vendor(goal: str, facts: dict) -> tuple[Optional[str], Optional[str]]:
    """Longest portal vendor name that appears in the goal. Ambiguous/none -> facts['vendor']."""
    goal_l = (goal or "").lower()
    names = portal_vendors()
    hits = [n for n in names if n.lower() in goal_l]
    hits.sort(key=len, reverse=True)
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        # If one name is a prefix of another and both match, keep the longest only when
        # shorter names are strict substrings of the longest hit's appearance... PLAN:
        # if ambiguous, use facts['vendor'].
        longest = hits[0]
        others = [h for h in hits[1:] if h.lower() not in longest.lower()]
        if not others:
            return longest, None
        facts = facts or {}
        vendor = facts.get("vendor")
        answer = str(facts.get("user_answer") or "")
        if vendor and str(vendor).casefold() in answer.casefold():
            return str(vendor), None
        return None, "ambiguous vendor requires user confirmation"
    facts = facts or {}
    vendor = facts.get("vendor")
    answer = str(facts.get("user_answer") or "")
    if vendor and str(vendor).casefold() in answer.casefold():
        return str(vendor), None
    if vendor:
        return None, "vendor must be confirmed by the user"
    return None, "no vendor in goal or facts"


def desired_order(goal: str) -> str:
    """Map the goal's superlative to a sort direction. Defaults to newest."""
    g = (goal or "").lower()
    if "oldest" in g or "earliest" in g:
        return "ASC"
    return "DESC"


def expected_invoice(vendor: str, order: str = "DESC") -> Optional[dict]:
    direction = "ASC" if order == "ASC" else "DESC"  # whitelist; never string-format user input
    conn = _connect(PORTAL_DB)
    row = conn.execute(
        f"SELECT * FROM invoices WHERE vendor=? ORDER BY date {direction} LIMIT 1",
        (vendor,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "vendor": row["vendor"],
        "invoice_number": row["number"],
        "amount": int(row["amount"]),
        "due_date": row["due_date"],
        "invoice_date": row["date"],
    }


def finance_matches(invoice_number: str) -> list[dict]:
    conn = _connect(FINANCE_DB)
    rows = conn.execute(
        "SELECT * FROM records WHERE invoice_number=?", (invoice_number,)
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "vendor": r["vendor"],
            "invoice_number": r["invoice_number"],
            "amount": int(r["amount"]),
            "due_date": r["due_date"],
            "status": r["status"],
        }
        for r in rows
    ]


def verify(goal: str, facts: dict, approved: bool | None = None) -> dict:
    vendor, err = resolve_vendor(goal, facts)
    if err:
        return {
            "passed": False,
            "expected": None,
            "actual": None,
            "diff": [err],
        }
    expected = expected_invoice(vendor, desired_order(goal))
    if not expected:
        return {
            "passed": False,
            "expected": None,
            "actual": None,
            "diff": [f"no portal invoice for vendor {vendor}"],
        }
    actuals = finance_matches(expected["invoice_number"])
    diff: list[str] = []
    if len(actuals) != 1:
        diff.append(f"expected exactly 1 finance record, found {len(actuals)}")
    actual = actuals[0] if actuals else None
    if actual:
        for k in ("vendor", "invoice_number", "amount", "due_date"):
            if actual.get(k) != expected.get(k):
                diff.append(f"{k}: expected {expected.get(k)!r} actual {actual.get(k)!r}")
        gl = (goal or "").lower()
        if "pay" in gl or "payment" in gl:
            want = "PAID" if approved else "UNPAID"
            if actual.get("status") != want:
                diff.append(f"status: expected {want} actual {actual.get('status')!r}")
    return {
        "passed": not diff,
        "expected": expected,
        "actual": actual,
        "diff": diff,
    }


def approval_was_granted(state_approved: list, state_rejected: list) -> Optional[bool]:
    if state_approved:
        return True
    if state_rejected:
        return False
    return None
