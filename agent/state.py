"""WorkerState is the only memory. Persist after every step so a crash is inspectable."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkerState:
    goal: str
    status: str = "RUNNING"
    plan: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    retries: dict[str, int] = field(default_factory=dict)
    pending_approval: Optional[dict] = None
    verification_result: Optional[dict] = None
    approved_actions: list[str] = field(default_factory=list)
    rejected_actions: list[str] = field(default_factory=list)
    summary: str = ""
    step: int = 0
    verify_attempts: int = 0

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "WorkerState":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One run = one log. The run dir can be reused (e.g. eval ids), so start empty.
        self.path.write_text("", encoding="utf-8")
        self.events: list[dict] = []

    def emit(self, type_: str, **kwargs: Any) -> dict:
        ev = {"ts": utc_ts(), "type": type_}
        ev.update(kwargs)
        self.events.append(ev)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, default=str) + "\n")
        return ev
