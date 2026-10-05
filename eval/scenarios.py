"""Eval scenarios A–G. Resets DB/toggles per run. Does not patch agent code."""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.hooks import Hooks
from agent.worker import Worker
from mockapps.seed import reset_all

FIN = "http://127.0.0.1:3002"
HAPPY = "Find the latest invoice from Acme Corp and enter it into the finance system."
GLOBEX = "Find the latest invoice from Globex and enter it into the finance system."
AMBIG = "Find the latest Acme invoice and enter it into the finance system."
PAY = "Find the latest invoice from Acme Corp, enter it into the finance system, and pay it."


def _admin_set(**flags: int) -> None:
    q = "&".join(f"{k}={v}" for k, v in flags.items())
    r = httpx.post(f"{FIN}/admin/set?{q}", timeout=10, follow_redirects=True)
    r.raise_for_status()


def _admin_reset() -> None:
    r = httpx.post(f"{FIN}/admin/reset", timeout=10, follow_redirects=True)
    r.raise_for_status()


def _prep(toggles: dict[str, int]) -> None:
    reset_all()
    _admin_reset()
    if toggles:
        _admin_set(**toggles)


def run_scenario(name: str, goal: str, toggles: dict[str, int], answer: str | None, approve: bool | None) -> dict:
    _prep(toggles)

    def ask(q: str) -> str:
        if answer is None:
            raise RuntimeError(f"{name}: unexpected ask_user: {q}")
        return answer

    def appr(action: str, reason: str) -> bool:
        if approve is None:
            raise RuntimeError(f"{name}: unexpected approval: {action}")
        return bool(approve)

    hooks = Hooks(ask=ask, approve=appr, on_event=lambda ev: None)
    t0 = time.time()
    w = Worker(run_id=f"eval-{name}-{uuid.uuid4().hex[:8]}", hooks=hooks)
    try:
        result = w.run(goal)
    except Exception as e:
        seconds = round(time.time() - t0, 1)
        return {
            "scenario": name,
            "pass": "fail",
            "steps": w.state.step,
            "retries": 0,
            "seconds": seconds,
            "status": f"error:{e}",
            "run_dir": str(w.run_dir),
        }
    seconds = round(time.time() - t0, 1)
    passed = result.get("status") == "COMPLETED"
    retries = 0
    if isinstance(result.get("retries"), dict):
        retries = sum(int(v) for v in result.get("retries").values() if isinstance(v, (int, str)) and str(v).strip())
    steps = w.state.step
    return {
        "scenario": name,
        "pass": "pass" if passed else "fail",
        "steps": steps,
        "retries": retries,
        "seconds": seconds,
        "status": result.get("status"),
        "run_dir": result.get("run_dir"),
    }


def main() -> None:
    rows = []
    rows.append(run_scenario("A", HAPPY, {}, None, None))
    rows.append(run_scenario("B", GLOBEX, {}, None, None))
    rows.append(run_scenario("C", HAPPY, {"fail_first_save": 1}, None, None))
    rows.append(run_scenario("D", HAPPY, {"rename_button": 1}, None, None))
    rows.append(run_scenario("E", AMBIG, {}, "Acme Corp", None))
    rows.append(run_scenario("F", PAY, {}, None, True))
    rows.append(run_scenario("G", PAY, {}, None, False))

    print(f"{'scenario':<10} {'pass/fail':<10} {'steps':<8} {'retries':<8} {'seconds':<8}")
    print("-" * 48)
    lines = ["# Eval results", "", "| scenario | pass/fail | steps | retries | seconds |", "|---|---|---|---|---|"]
    for r in rows:
        print(f"{r['scenario']:<10} {r['pass']:<10} {r['steps']:<8} {r['retries']:<8} {r['seconds']:<8}")
        lines.append(f"| {r['scenario']} | {r['pass']} | {r['steps']} | {r['retries']} | {r['seconds']} |")
    out = Path(__file__).resolve().parent / "results.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (Path(__file__).resolve().parent / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
