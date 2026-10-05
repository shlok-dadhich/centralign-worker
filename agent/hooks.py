"""Human I/O hooks. CLI uses stdin; eval/server override."""
from typing import Callable, Optional


class Hooks:
    def __init__(
        self,
        ask: Optional[Callable[[str], str]] = None,
        approve: Optional[Callable[[str, str], bool]] = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ):
        self._ask = ask
        self._approve = approve
        self._on_event = on_event

    def ask_user(self, question: str) -> str:
        if self._ask:
            return self._ask(question)
        print(f"\nQUESTION: {question}")
        return input("answer> ").strip()

    def request_approval(self, action: str, reason: str) -> bool:
        if self._approve:
            return bool(self._approve(action, reason))
        print(f"\nAPPROVAL needed for: {action}\nreason: {reason}")
        raw = input("approve? [y/n] ").strip().lower()
        return raw in ("y", "yes", "approve")

    def on_event(self, ev: dict) -> None:
        if self._on_event:
            self._on_event(ev)
        else:
            t = ev.get("type")
            extra = {k: ev[k] for k in ev if k not in ("ts",)}
            print(f"[{t}] {extra}")
