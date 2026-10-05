"""Hand-written agent loop. One tool call per turn. Policy in code, not in the prompt.

The loop is a real multi-turn tool-calling conversation: the assistant's tool calls and the
tool results (page observations) are kept in the message history, so the model always sees its
own actions and their outcomes. There is no scripted/hardcoded fallback: every action is chosen
by the model and executed through the browser.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from agent.browser import Browser, compact_observation
from agent.hooks import Hooks
from agent.llm import chat, parse_tool_call, plan_task
from agent.policy import (
    FINANCIAL_RE,
    bump_retry,
    classify_action,
    classify_failure,
    element_label_from_obs,
    guidance_for,
    retries_left,
    retry_signature,
)
from agent.prompts import (
    APPROVAL_RESULT_NOTE,
    FAILURE_NOTE,
    NO_TOOL_NUDGE,
    POLICY_BLOCK_NOTE,
    SYSTEM_PROMPT,
    VERIFY_FEEDBACK,
)
from agent.state import EventLog, WorkerState
from agent.tools import ollama_tools, validate_call
from agent.verifier import portal_vendors, resolve_vendor, verify

MAX_STEPS = 40
MAX_VERIFY = 2
# How many times the model may re-ask a decision the human already made (approval/payment) before
# the loop terminates and reports. Prevents spinning on a settled financial action.
DECISION_STALL_CAP = 2
ROOT = Path(__file__).resolve().parent.parent


def _system() -> str:
    return SYSTEM_PROMPT.format(
        PORTAL_USER=os.environ.get("PORTAL_USER", "ap@demo.test"),
        PORTAL_PASS=os.environ.get("PORTAL_PASS", "demo123"),
        FIN_USER=os.environ.get("FIN_USER", "fin@demo.test"),
        FIN_PASS=os.environ.get("FIN_PASS", "demo123"),
    )


def _approval_note(approved: bool, action: str) -> str:
    # APPROVAL_RESULT_NOTE is copied verbatim; we expand the documented placeholders here.
    status = "APPROVED" if approved else "REJECTED"
    extra = (
        "You may proceed."
        if approved
        else "Do NOT perform it. Call finish explaining what was and was not done."
    )
    return (
        APPROVAL_RESULT_NOTE.replace("{APPROVED|REJECTED}", status)
        .replace("'{action}'", f"'{action}'")
        .replace(
            '{If APPROVED: "You may proceed." If REJECTED: "Do NOT perform it. Call finish explaining what was and was not done."}',
            extra,
        )
    )


def _code_summary(state: WorkerState, v: Optional[dict]) -> str:
    facts = state.facts
    bits = [f"status={state.status}"]
    if facts:
        bits.append("facts=" + json.dumps(facts, default=str))
    if v:
        bits.append("verify_passed=" + str(bool(v.get("passed"))))
        if v.get("diff"):
            bits.append("diff=" + json.dumps(v.get("diff"), default=str))
    return "; ".join(bits)


class Worker:
    def __init__(self, run_id: Optional[str] = None, hooks: Optional[Hooks] = None):
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.run_dir = ROOT / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.hooks = hooks or Hooks()
        self.log = EventLog(self.run_dir / "events.jsonl")
        self.browser = Browser(self.run_dir)
        self.state = WorkerState(goal="")
        self.obs: dict = {}
        self.system_note = ""
        self.trace: Optional[str] = None
        self.ended = False
        # Counts tool calls that keep re-asking a decision the human already made. Bounded so the
        # loop cannot spin on a settled approval; see DECISION_STALL_CAP below.
        self._decision_stalls = 0

    def emit(self, type_: str, **kw: Any) -> dict:
        ev = self.log.emit(type_, **kw)
        self.hooks.on_event(ev)
        return ev

    def persist(self) -> None:
        self.state.save(self.run_dir / "state.json")

    def run(self, goal: str) -> dict:
        self.state = WorkerState(goal=goal)
        self.emit("TASK_CREATED", goal=goal, run_id=self.run_id)
        planned = plan_task(goal)
        self.state.plan = planned.get("plan") or []
        self.emit("PLAN_GENERATED", plan=self.state.plan, goal=planned.get("goal"))
        self.persist()
        if not self._confirm_vendor():
            return self._final()
        self.browser.start()
        try:
            self._loop()
        finally:
            self.trace = self.browser.stop()
            self.persist()
        return self._final()

    def _confirm_vendor(self) -> bool:
        """Require a human-sourced vendor when the goal does not resolve to exactly one portal vendor."""
        _, error = resolve_vendor(self.state.goal, {})
        if not error:
            return True

        vendors = portal_vendors()
        question = (
            "I couldn't identify exactly one vendor from the task. Which vendor should I use? "
            f"Choose one of: {', '.join(vendors)}."
        )
        for _ in range(2):
            self.emit("ASK_USER", result_summary=question)
            answer = self.hooks.ask_user(question).strip()
            matches = [name for name in vendors if name.casefold() in answer.casefold()]
            if len(matches) == 1:
                vendor = matches[0]
                self.state.facts["user_answer"] = answer
                self.state.facts["vendor"] = vendor
                self.state.history.append(
                    {"tool": "ask_user", "question": question, "answer": answer, "resolved_vendor": vendor}
                )
                self.emit("FACT_RECORDED", tool="ask_user", args={"key": "vendor", "value": vendor}, result_summary=vendor)
                self.persist()
                return True
            question = (
                f"Please choose exactly one vendor name from this list: {', '.join(vendors)}. "
                "I will not guess."
            )

        self.state.status = "ESCALATED"
        self.state.summary = "vendor was not confirmed by the user"
        self.emit("TASK_FAILED", reason=self.state.summary)
        self.persist()
        self.ended = True
        return False

    def _loop(self) -> None:
        """A real tool-calling conversation: assistant tool calls + tool results stay in history."""
        tools = ollama_tools()
        messages: list[dict] = [
            {"role": "system", "content": _system()},
            {
                "role": "user",
                "content": (
                    f"TASK: {self.state.goal}\n"
                    f"PLAN (hypothesis, may change): {json.dumps(self.state.plan)}\n"
                    f"FACTS SO FAR: {json.dumps(self.state.facts, default=str)}\n"
                    "Use the tools to complete the task on the real websites. "
                    "Start now with exactly one tool call."
                ),
            },
        ]
        nudged = False
        while self.state.step < MAX_STEPS and not self.ended:
            self.state.step += 1
            try:
                msg = chat(messages, tools=tools)
            except Exception as e:
                # Never leave a run hanging: a permanent model/transport failure ends the run in a
                # recorded terminal state instead of an unhandled crash.
                self.state.status = "FAILED"
                self.state.summary = f"model call failed: {e}"
                self.emit("TASK_FAILED", reason=self.state.summary)
                self.ended = True
                return
            call = parse_tool_call(msg)
            if not call:
                content = (msg.get("content") or "").strip()
                messages.append({"role": "assistant", "content": content})
                if not nudged:
                    nudged = True
                    self.state.failures.append({"step": self.state.step, "error": "no tool call"})
                    messages.append({"role": "user", "content": NO_TOOL_NUDGE})
                    self.persist()
                    continue
                if content:
                    # The model answered in prose instead of calling a tool. Treat the text as a
                    # finish() CLAIM; the deterministic verifier still decides pass/fail, and a
                    # failed check feeds VERIFY_FEEDBACK back so the loop keeps going.
                    messages.append({"role": "user", "content": "Interpreted as finish(); an independent verifier will check the result."})
                    self._execute("finish", {"summary": content})
                    self.persist()
                    if self.ended:
                        return
                    messages.append({"role": "user", "content": self.system_note or "Call finish again when done."})
                    continue
                self.state.status = "FAILED"
                self.state.summary = "model returned no tool call twice"
                self.emit("TASK_FAILED", reason=self.state.summary)
                self.ended = True
                return
            nudged = False
            # Keep the assistant turn (only its first tool call, per the one-call-per-turn rule).
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": (msg.get("tool_calls") or [])[:1],
                }
            )
            err, args = validate_call(call["name"], call.get("args") or {})
            self.emit("TOOL_CALL", tool=call["name"], args=args if not err else call.get("args"), result_summary=err or "")
            if err:
                self.system_note = err
                self.obs = {"url": (self.obs or {}).get("url", ""), "title": "", "visible_text": err, "error_banner": err, "elements": []}
                self.emit("TOOL_FAILURE", tool=call["name"], args=call.get("args"), result_summary=err)
                messages.append({"role": "tool", "content": json.dumps({"ok": False, "error": err})})
                self.persist()
                continue
            tool_result = self._execute(call["name"], args)
            messages.append({"role": "tool", "content": tool_result})
            # Any failure/policy/approval guidance is fed back as a user turn before the next call.
            if self.system_note:
                messages.append({"role": "user", "content": self.system_note})
            if not self.ended and self._decision_stalls >= DECISION_STALL_CAP:
                # The human already made the call on the irreversible action and the model keeps
                # re-asking. Terminate deterministically: the verifier still decides pass/fail.
                self._finish("Stopped: the human decided on the irreversible action; reported without re-asking.")
                if self.system_note and not self.ended:
                    messages.append({"role": "user", "content": self.system_note})
            self.persist()

        if not self.ended:
            self.state.status = "FAILED"
            self.state.summary = "step limit reached"
            self.emit("TASK_FAILED", reason=self.state.summary)

    def _retry_click_by_label(self, label: str, element_id: Any = None) -> Optional[dict]:
        """After a transient failure the page may have re-rendered with new element ids.
        Re-find the same control by the label the model already saw and click it. If several
        controls share that label, pass the original control's tag/role so the right one is used."""
        tag = role = None
        if element_id is not None:
            for el in (self.obs or {}).get("elements") or []:
                if el.get("id") == element_id:
                    tag, role = el.get("tag"), el.get("role")
                    break
        return self.browser.click_by_label(label, tag=tag, role=role)

    def _auto_retry_same_action(self, name: str, args: dict, summary: str, label: str, prev_banner: str = "") -> bool:
        """Deterministic first retry for transient failures (timeout / 5xx banner). It re-issues
        the SAME action through the browser (by label when ids changed), never bypassing it.
        The retry only counts as recovered when the page shows no NEW error banner."""
        low = summary.lower()
        if name != "click":
            return False
        if "temporary server error" not in low and "500" not in low:
            return False
        sig = retry_signature(name, args)
        used = int(self.state.retries.get(sig, 0))
        if used >= 2:
            return False
        retry_result = (
            self._retry_click_by_label(label, args.get("element_id")) if label else self._run_tool(name, args)
        )
        if retry_result is None:
            return False
        self.obs = retry_result.get("observation") or self.obs
        new_banner = (self.obs or {}).get("error_banner") or ""
        recovered = bool(retry_result.get("ok")) and (not new_banner or new_banner == prev_banner)
        self.emit("RETRY", tool=name, args=args, result_summary=f"auto retry after {summary[:80]}")
        if not recovered:
            # Retry produced a new error; let _execute classify and guide normally.
            return False
        self.emit(
            "OBSERVATION",
            tool=name,
            args=args,
            result_summary="ok",
            screenshot=(self.obs or {}).get("screenshot"),
        )
        self.state.history.append({"tool": name, "args": args, "ok": True, "summary": "auto-retry"})
        self.system_note = ""
        return True

    def _execute(self, name: str, args: dict) -> str:
        """Run one validated tool call through the browser/state and return the tool-result text."""
        url = (self.obs or {}).get("url") or ""
        prev_banner = (self.obs or {}).get("error_banner") or ""
        label = element_label_from_obs(self.obs, args.get("element_id"))
        kind = classify_action(name, args, label, url)

        # Approval in disguise: ask_user must never carry a financial/irreversible decision. Those
        # go through request_approval (RULE 8). Detected via the shared FINANCIAL classifier, so this
        # is a general policy, not a scenario-specific guard.
        if name == "ask_user" and FINANCIAL_RE.search(str(args.get("question") or "")):
            question = str(args.get("question") or "")
            if self.state.rejected_actions:
                self._decision_stalls += 1
                self.system_note = (
                    "The human already REJECTED the financial action for this task. You must not ask for "
                    "it again (approvals use request_approval, not ask_user). Call finish now and report "
                    "that it was not done."
                )
            elif self.state.approved_actions:
                self._decision_stalls += 1
                self.system_note = (
                    "The financial action was already APPROVED. Do not ask again - proceed with it and then finish."
                )
            else:
                self.system_note = POLICY_BLOCK_NOTE.replace("{action}", question[:120])
            self.obs = self.browser.observe() if self.browser.page else self.obs
            self.emit(
                "TOOL_FAILURE",
                tool=name,
                args=args,
                result_summary="policy block: approval via ask_user",
                screenshot=(self.obs or {}).get("screenshot"),
            )
            self.state.history.append({"tool": name, "args": args, "ok": False, "note": "policy_block"})
            return json.dumps({"ok": False, "error": "blocked by policy: use request_approval for approvals"})

        if name == "click" and kind == "FINANCIAL" and not self.state.approved_actions:
            action = label or "financial click"
            if self.state.rejected_actions:
                # A financial action was already rejected for this task: do not loop on it.
                self._decision_stalls += 1
                self.system_note = (
                    f"The human REJECTED the financial action for this task, so '{action}' must not be "
                    "performed. Call finish now and report that it was not done."
                )
            else:
                self.system_note = POLICY_BLOCK_NOTE.replace("{action}", action)
            self.obs = self.browser.observe() if self.browser.page else self.obs
            self.emit(
                "TOOL_FAILURE",
                tool=name,
                args=args,
                result_summary="policy block",
                screenshot=(self.obs or {}).get("screenshot"),
            )
            self.state.history.append({"tool": name, "args": args, "ok": False, "note": "policy_block"})
            return json.dumps({"ok": False, "error": "blocked by policy: request_approval first"})

        result = self._run_tool(name, args)
        if result is None:
            # finish / verification gate handled inside _finish.
            return json.dumps({"ok": True, "note": "handled"})

        ok = bool(result.get("ok"))
        self.obs = result.get("observation") or self.obs
        shot = (self.obs or {}).get("screenshot")
        banner = (self.obs or {}).get("error_banner") or ""
        # A banner left over from an earlier action is not a NEW failure of this action.
        new_banner = banner if banner != prev_banner else ""
        failed = (not ok) or bool(new_banner)
        summary = result.get("error") or new_banner or ("ok" if ok else "error")
        self.emit(
            "OBSERVATION" if not failed else "TOOL_FAILURE",
            tool=name,
            args=args,
            result_summary=summary[:300],
            screenshot=shot,
        )
        self.state.history.append({"tool": name, "args": args, "ok": not failed, "summary": summary[:200]})

        if failed:
            cls = classify_failure(result)
            sig = retry_signature(name, args)
            used, exceeded = bump_retry(self.state.retries, sig)
            if exceeded:
                cls = "FATAL"
            if cls == "RETRYABLE" and self._auto_retry_same_action(name, args, summary, label, prev_banner):
                return json.dumps(
                    {"ok": True, "error": "", "note": "retried after transient failure", "observation": compact_observation(self.obs)}
                )
            n_left = retries_left(self.state.retries, sig)
            self.emit("FAILURE_CLASSIFIED", tool=name, args=args, result_summary=cls)
            if cls == "RETRYABLE":
                self.emit("RETRY", tool=name, args=args, result_summary=str(used))
            self.state.failures.append({"tool": name, "class": cls, "detail": summary[:200]})
            guidance = guidance_for(cls, n_left)
            self.system_note = FAILURE_NOTE.format(cls=cls, detail=summary[:200], guidance=guidance, n_left=n_left)
            if cls == "FATAL" and used > 2:
                self.state.status = "ESCALATED"
                self.state.summary = "retry cap exceeded"
                self.emit("TASK_FAILED", reason=self.state.summary)
                self.ended = True
        # On success the note set by the tool itself is preserved (e.g. the user's answer, the
        # approval outcome). _run_tool resets the note to "" at entry, so nothing goes stale here.
        return json.dumps({"ok": ok, "error": result.get("error") or banner, "observation": compact_observation(self.obs)})

    def _run_tool(self, name: str, args: dict) -> Optional[dict]:
        self.system_note = ""
        if name == "open_url":
            return self.browser.open_url(args["url"])
        if name == "read_page":
            return {"ok": True, "error": "", "observation": self.browser.observe()}
        if name == "click":
            return self.browser.click(args["element_id"])
        if name == "type_text":
            return self.browser.type_text(args["element_id"], args["text"])
        if name == "record_fact":
            key = str(args["key"])
            if key == "user_answer":
                return {
                    "ok": False,
                    "error": "user_answer is reserved for the human-input hook",
                    "observation": self.obs,
                }
            self.state.facts[key] = args["value"]
            self.emit("FACT_RECORDED", tool=name, args=args, result_summary=str(args["value"]))
            self.state.history.append({"tool": name, "args": args, "ok": True})
            return {"ok": True, "error": "", "observation": self.obs}
        if name == "ask_user":
            answer = self.hooks.ask_user(args["question"])
            self.system_note = f"User answered: {answer}"
            self.state.facts.setdefault("user_answer", answer)
            self.state.history.append({"tool": name, "args": args, "ok": True, "answer": answer})
            return {"ok": True, "error": "", "observation": self.obs}
        if name == "request_approval":
            action, reason = args["action"], args["reason"]
            if action in self.state.rejected_actions:
                # Never re-ask for an already rejected action; steer the model to finish. Returned as
                # ok=True so the terminal note reaches the model instead of a generic failure note.
                self._decision_stalls += 1
                self.system_note = (
                    f"The human already REJECTED '{action}'. Do not request or perform it again. "
                    "Call finish now, reporting that it was not done."
                )
                self.state.history.append({"tool": name, "args": args, "ok": True, "note": "already_rejected"})
                return {"ok": True, "error": "", "observation": self.obs}
            if action in self.state.approved_actions:
                self._decision_stalls += 1
                self.system_note = f"'{action}' was already APPROVED earlier. You may proceed."
                self.state.history.append({"tool": name, "args": args, "ok": True, "note": "already_approved"})
                return {"ok": True, "error": "", "observation": self.obs}
            self.state.pending_approval = {"action": action, "reason": reason}
            self.emit("APPROVAL_REQUESTED", tool=name, args=args, result_summary=action)
            ok = self.hooks.request_approval(action, reason)
            self.state.pending_approval = None
            self.emit("APPROVAL_RESOLVED", tool=name, args=args, result_summary="approved" if ok else "rejected")
            if ok:
                self.state.approved_actions.append(action)
            else:
                self.state.rejected_actions.append(action)
            self.system_note = _approval_note(ok, action)
            self.state.history.append({"tool": name, "args": args, "ok": True, "approved": ok})
            return {"ok": True, "error": "", "observation": self.obs}
        if name == "finish":
            self._finish(args.get("summary") or "")
            return None
        return {"ok": False, "error": f"unknown tool: {name}", "observation": self.obs}

    def _finish(self, llm_summary: str) -> None:
        self.emit("VERIFICATION_STARTED", result_summary=llm_summary)
        approved = True if self.state.approved_actions else (False if self.state.rejected_actions else None)
        v = verify(self.state.goal, self.state.facts, approved)
        self.state.verification_result = v
        if v.get("passed"):
            self.state.status = "COMPLETED"
            self.emit("VERIFICATION_PASSED", result_summary=json.dumps(v, default=str)[:400])
            self.state.summary = _code_summary(self.state, v)
            self.emit("TASK_COMPLETED", result_summary=self.state.summary)
            self.ended = True
            return
        self.state.verify_attempts += 1
        self.emit("VERIFICATION_FAILED", result_summary=json.dumps(v.get("diff"), default=str)[:400])
        n_left = MAX_VERIFY - self.state.verify_attempts
        if n_left <= 0:
            self.state.status = "FAILED"
            self.state.summary = _code_summary(self.state, v)
            self.emit("TASK_FAILED", reason="verification failed")
            self.ended = True
            return
        self.system_note = VERIFY_FEEDBACK.format(
            expected=v.get("expected"),
            actual=v.get("actual"),
            diff=v.get("diff"),
            n=n_left,
        )

    def _final(self) -> dict:
        v = self.state.verification_result or {}
        actual = v.get("actual") or {}
        shots = list((self.run_dir / "screenshots").glob("*.png"))
        return {
            "status": self.state.status,
            "summary": _code_summary(self.state, v) if v else self.state.summary,
            "facts": self.state.facts,
            "finance_record_id": actual.get("id"),
            "expected-vs-actual": {"expected": v.get("expected"), "actual": v.get("actual"), "diff": v.get("diff")},
            "retries": self.state.retries,
            "#events": len(self.log.events),
            "#screenshots": len(shots),
            "trace": self.trace,
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
        }
