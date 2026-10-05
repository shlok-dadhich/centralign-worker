"""Pure policy functions. No I/O. Retry cap is enforced by callers using retry_signature."""
import json
import re
from typing import Any

FINANCIAL_RE = re.compile(r"pay|payment|approve payment|transfer|delete", re.I)
RETRYABLE_RE = re.compile(r"timeout|temporary server error|\b5\d\d\b", re.I)
RECOVERABLE_RE = re.compile(r"element not found|stale id|unknown element", re.I)
FATAL_RE = re.compile(r"login failure|invalid email or password|invalid data", re.I)

RETRY_CAP = 2


def classify_action(tool: str, args: dict, element_label: str, current_url: str) -> str:
    blob = " ".join(
        [
            tool or "",
            json.dumps(args or {}, default=str),
            element_label or "",
            current_url or "",
        ]
    )
    if FINANCIAL_RE.search(blob):
        return "FINANCIAL"
    if tool in ("type_text", "record_fact", "finish"):
        return "WRITE"
    if tool == "click":
        label = (element_label or "").lower()
        if any(w in label for w in ("submit", "save", "log in", "login")):
            return "WRITE"
        return "READ"
    if tool in ("open_url", "read_page"):
        return "READ"
    if tool in ("ask_user", "request_approval"):
        return "READ"
    return "UNKNOWN"


def classify_failure(result: dict) -> str:
    if not result:
        return "RECOVERABLE"
    obs = result.get("observation") or {}
    banner = obs.get("error_banner") or ""
    err = result.get("error") or ""
    text = f"{err} {banner}"
    if FATAL_RE.search(text):
        return "FATAL"
    if RETRYABLE_RE.search(text):
        return "RETRYABLE"
    if RECOVERABLE_RE.search(text):
        return "RECOVERABLE"
    if result.get("ok") is False:
        return "RECOVERABLE"
    if banner:
        if RETRYABLE_RE.search(banner):
            return "RETRYABLE"
        return "RECOVERABLE"
    return "RECOVERABLE"


def retry_signature(tool: str, args: dict) -> str:
    return f"{tool}:{json.dumps(args or {}, sort_keys=True, default=str)}"


def retries_left(retries: dict[str, int], sig: str) -> int:
    used = int(retries.get(sig, 0))
    return max(0, RETRY_CAP - used)


def bump_retry(retries: dict[str, int], sig: str) -> tuple[int, bool]:
    """Increment failure count for sig. Returns (used, exceeded). exceeded => treat as FATAL."""
    used = int(retries.get(sig, 0)) + 1
    retries[sig] = used
    return used, used > RETRY_CAP


def guidance_for(cls: str, n_left: int) -> str:
    if cls == "RETRYABLE":
        return f"Safe to repeat the same action ({n_left} retries left)."
    if cls == "RECOVERABLE":
        return "Call read_page and choose a different element."
    return "Do not continue blindly. Call ask_user and explain."


def element_label_from_obs(obs: dict, element_id: Any) -> str:
    try:
        eid = int(element_id)
    except (TypeError, ValueError):
        return ""
    for el in (obs or {}).get("elements") or []:
        if el.get("id") == eid:
            return str(el.get("label") or "")
    return ""
