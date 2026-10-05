"""Ollama chat + native tool calling. Never exec model-generated code."""
import json
import os
import re
import time
from typing import Any, Optional

import ollama

from agent.prompts import PLANNER_PROMPT

# Transport/upstream failures (cloud 5xx, dropped connections, timeouts) are transient and should be
# retried with backoff, not surfaced as an agent failure. This is a reliability mechanism, not a fallback
# action: the same request is re-sent.
TRANSIENT_LLM_RE = re.compile(
    r"internal server error|status code: 5\d\d|\b5\d\d\b|timed out|timeout|connection|temporarily unavailable|reset by peer|eof",
    re.I,
)
LLM_ATTEMPTS = 4
LLM_BACKOFF_SECONDS = 2.0
# Greedy decoding keeps runs reproducible (same trajectory for the same page state), which is what
# makes an eval suite meaningful. Override with OLLAMA_TEMPERATURE if you want sampling.
LLM_OPTIONS = {"temperature": float(os.environ.get("OLLAMA_TEMPERATURE", "0"))}


def _chat_with_retry(**kwargs: Any) -> Any:
    """Retry transient upstream errors with linear backoff; re-raise anything permanent immediately."""
    last: Optional[Exception] = None
    for attempt in range(1, LLM_ATTEMPTS + 1):
        try:
            return ollama.chat(**kwargs)
        except Exception as e:  # ollama raises ResponseError / httpx errors
            last = e
            if attempt >= LLM_ATTEMPTS or not TRANSIENT_LLM_RE.search(str(e)):
                raise
            time.sleep(LLM_BACKOFF_SECONDS * attempt)
    if last:
        raise last

DEFAULT_MODELS = [
    os.environ.get("OLLAMA_MODEL"),
    "gpt-oss:20b",
    "gpt-oss:20b-cloud",
    "llama3.1:8b",
    "qwen2.5-coder:7b",
]


def _resolve_model() -> str:
    """Use the user's override when set; otherwise prefer the documented default and fall back to any installed model."""
    seen = set()
    candidates = []
    for name in DEFAULT_MODELS:
        if not name or name in seen:
            continue
        seen.add(name)
        candidates.append(name)
    try:
        installed = ollama.list().get("models") or []
    except Exception:
        installed = []
    available = []
    for model in installed:
        if isinstance(model, dict):
            name = model.get("name") or model.get("model")
        else:
            name = getattr(model, "name", None) or getattr(model, "model", None)
        if name:
            available.append(name)
    for model in candidates + available:
        if model and model not in seen:
            candidates.append(model)
            seen.add(model)
    for name in candidates:
        if not name:
            continue
        try:
            ollama.show(name)
            return name
        except Exception:
            continue
    return candidates[0] if candidates else "gpt-oss:20b"


MODEL = _resolve_model()


def _msg_dict(resp: Any) -> dict:
    msg = resp.get("message") if isinstance(resp, dict) else resp.message
    if isinstance(msg, dict):
        return msg
    tool_calls = getattr(msg, "tool_calls", None) or []
    normalized = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            normalized.append(tc)
            continue
        fn = getattr(tc, "function", None)
        name = getattr(fn, "name", None) if fn is not None else None
        args = getattr(fn, "arguments", None) if fn is not None else {}
        normalized.append({"function": {"name": name, "arguments": args}})
    return {"role": "assistant", "content": getattr(msg, "content", "") or "", "tool_calls": normalized}


def parse_tool_call(msg: dict) -> Optional[dict]:
    """Return {name, args} for the first tool call, or None."""
    calls = msg.get("tool_calls") or []
    if not calls:
        return None
    call = calls[0]
    fn = call.get("function") if isinstance(call, dict) else None
    if not fn:
        return None
    name = fn.get("name")
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args else {}
        except json.JSONDecodeError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    return {"name": name, "args": args}


def chat(messages: list[dict], tools: Optional[list] = None) -> dict:
    kwargs: dict[str, Any] = {"model": MODEL, "messages": messages, "options": dict(LLM_OPTIONS)}
    if tools:
        kwargs["tools"] = tools
    resp = _chat_with_retry(**kwargs)
    return _msg_dict(resp)


def plan_task(task: str) -> dict:
    """Planner uses format=json as specified. Falls back to a tiny default plan."""
    resp = _chat_with_retry(
        model=MODEL,
        messages=[
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": task},
        ],
        format="json",
        options=dict(LLM_OPTIONS),
    )
    msg = _msg_dict(resp)
    raw = msg.get("content") or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return {
        "goal": data.get("goal") or task,
        "plan": data.get("plan") or ["Log in", "Find the item", "Enter it", "Verify"],
        "risky_steps": data.get("risky_steps") or [],
    }
