"""FastAPI UI: POST /tasks, SSE events, approval + answer. Worker runs in a background thread."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.hooks import Hooks
from agent.worker import Worker

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="CentrAlign Worker")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

_lock = threading.Lock()
_tasks: dict[str, dict[str, Any]] = {}


class TaskIn(BaseModel):
    goal: str


class ApprovalIn(BaseModel):
    approved: bool


class AnswerIn(BaseModel):
    answer: str


def _get(tid: str) -> dict:
    with _lock:
        t = _tasks.get(tid)
    if not t:
        raise HTTPException(404, "unknown task")
    return t


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/tasks")
def create_task(body: TaskIn):
    ev_wait = threading.Event()
    state = {
        "events": [],
        "event_cv": threading.Condition(),
        "ask_q": None,
        "ask_a": None,
        "ask_ev": threading.Event(),
        "appr_action": None,
        "appr_reason": None,
        "appr_ev": threading.Event(),
        "appr_result": None,
        "result": None,
        "done": False,
    }

    def on_event(ev: dict) -> None:
        with state["event_cv"]:
            state["events"].append(ev)
            state["event_cv"].notify_all()

    def ask(question: str) -> str:
        state["ask_q"] = question
        state["ask_ev"].clear()
        on_event({"ts": "", "type": "ASK_USER", "result_summary": question})
        state["ask_ev"].wait()
        return state["ask_a"] or ""

    def approve(action: str, reason: str) -> bool:
        state["appr_action"] = action
        state["appr_reason"] = reason
        state["appr_ev"].clear()
        on_event({"ts": "", "type": "APPROVAL_REQUESTED", "args": {"action": action, "reason": reason}})
        state["appr_ev"].wait()
        return bool(state["appr_result"])

    w = Worker(hooks=Hooks(ask=ask, approve=approve, on_event=on_event))
    tid = w.run_id
    with _lock:
        _tasks[tid] = {"worker": w, **state}

    def runner():
        try:
            result = w.run(body.goal)
            with _lock:
                _tasks[tid]["result"] = result
                _tasks[tid]["done"] = True
            on_event({"ts": "", "type": "TASK_DONE", "result_summary": json.dumps(result, default=str)[:500]})
        except Exception as e:
            on_event({"ts": "", "type": "TASK_FAILED", "result_summary": str(e)})
            with _lock:
                _tasks[tid]["done"] = True

    threading.Thread(target=runner, daemon=True).start()
    return {"id": tid}


@app.get("/tasks/{tid}/events")
def events(tid: str):
    t = _get(tid)

    def gen():
        i = 0
        while True:
            with t["event_cv"]:
                while i >= len(t["events"]) and not t.get("done"):
                    t["event_cv"].wait(timeout=1.0)
                batch = t["events"][i:]
                i = len(t["events"])
                done = t.get("done")
            for ev in batch:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            if done and i >= len(t["events"]):
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/approvals/{tid}")
def approvals(tid: str, body: ApprovalIn):
    t = _get(tid)
    t["appr_result"] = body.approved
    t["appr_ev"].set()
    return {"ok": True}


@app.post("/answers/{tid}")
def answers(tid: str, body: AnswerIn):
    t = _get(tid)
    t["ask_a"] = body.answer
    t["ask_ev"].set()
    return {"ok": True}


@app.get("/tasks/{tid}")
def task_status(tid: str):
    t = _get(tid)
    w: Worker = t["worker"]
    return {
        "id": tid,
        "done": t.get("done"),
        "result": t.get("result"),
        "facts": w.state.facts,
        "status": w.state.status,
        "ask": t.get("ask_q") if not t["ask_ev"].is_set() else None,
        "approval": {"action": t.get("appr_action"), "reason": t.get("appr_reason")}
        if t.get("appr_action") and not t["appr_ev"].is_set()
        else None,
    }
