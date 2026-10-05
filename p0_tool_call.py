"""P0: one dummy tool call against Ollama. Prints the tool call and exits."""
import json
import os
import sys

import ollama

DEFAULT_MODELS = [
    os.environ.get("OLLAMA_MODEL"),
    "gpt-oss:20b",
    "gpt-oss:20b-cloud",
    "llama3.1:8b",
    "qwen2.5-coder:7b",
]


def resolve_model() -> str:
    seen = set()
    candidates = []
    for name in DEFAULT_MODELS:
        if name and name not in seen:
            seen.add(name)
            candidates.append(name)
    try:
        installed = ollama.list().get("models") or []
    except Exception:
        installed = []
    for model in installed:
        if isinstance(model, dict):
            name = model.get("name") or model.get("model")
        else:
            name = getattr(model, "name", None) or getattr(model, "model", None)
        if name and name not in seen:
            seen.add(name)
            candidates.append(name)
    for name in candidates:
        try:
            ollama.show(name)
            return name
        except Exception:
            continue
    return candidates[0] if candidates else "gpt-oss:20b"


MODEL = resolve_model()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo a short message. Call this tool; do not answer in text.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    }
]


def main() -> None:
    print(f"model={MODEL}", flush=True)
    resp = ollama.chat(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": "Call the echo tool with message p0-ok. Do not reply with plain text.",
            }
        ],
        tools=TOOLS,
    )
    msg = resp.get("message") if isinstance(resp, dict) else resp.message
    tool_calls = None
    if isinstance(msg, dict):
        tool_calls = msg.get("tool_calls")
        content = msg.get("content")
    else:
        tool_calls = getattr(msg, "tool_calls", None)
        content = getattr(msg, "content", None)
    print("content:", content)
    print("tool_calls:", json.dumps(tool_calls, default=str, indent=2))
    if not tool_calls:
        print("FAIL: no tool call", file=sys.stderr)
        sys.exit(1)
    print("P0 GATE PASS")


if __name__ == "__main__":
    main()
