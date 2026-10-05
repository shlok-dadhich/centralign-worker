"""JSON-schema tools + argument validation. Unknown/bad args become error observations."""
from typing import Any, Optional

TOOL_DESCRIPTIONS = {
    "open_url": "Navigate the browser to a URL (http://localhost:3001 or :3002) and return the page observation.",
    "read_page": "Re-read the current page without acting. Use when unsure or ids may be stale.",
    "click": "Click an interactive element by its id from the latest observation.",
    "type_text": "Clear the input with this id and type the text into it.",
    "record_fact": "Save a verified fact (key, value) to working memory. Use after reading values from a page.",
    "ask_user": "Ask the human a clarification question when ambiguous or blocked. Waits for the answer.",
    "request_approval": "Ask the human to approve an irreversible/financial action. Waits for approve/reject.",
    "finish": "Declare the task complete with a short summary. An independent verifier will check the result.",
}

_SCHEMAS = {
    "open_url": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
    "read_page": {"type": "object", "properties": {}},
    "click": {
        "type": "object",
        "properties": {"element_id": {"type": "integer"}},
        "required": ["element_id"],
    },
    "type_text": {
        "type": "object",
        "properties": {
            "element_id": {"type": "integer"},
            "text": {"type": "string"},
        },
        "required": ["element_id", "text"],
    },
    "record_fact": {
        "type": "object",
        "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
        "required": ["key", "value"],
    },
    "ask_user": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
    "request_approval": {
        "type": "object",
        "properties": {"action": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["action", "reason"],
    },
    "finish": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
}


def ollama_tools() -> list[dict]:
    out = []
    for name, schema in _SCHEMAS.items():
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": TOOL_DESCRIPTIONS[name],
                    "parameters": schema,
                },
            }
        )
    return out


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def validate_call(name: Optional[str], args: dict) -> tuple[Optional[str], dict]:
    """Return (error, cleaned_args). error is None if ok."""
    if not name or name not in _SCHEMAS:
        return f"unknown tool: {name}", {}
    args = args or {}
    if not isinstance(args, dict):
        return "bad args: expected object", {}
    required = _SCHEMAS[name].get("required") or []
    cleaned = dict(args)
    if name in ("click", "type_text") and "element_id" in cleaned:
        n = _as_int(cleaned["element_id"])
        if n is None:
            return "bad args: element_id must be an integer", {}
        cleaned["element_id"] = n
    missing = [k for k in required if k not in cleaned or cleaned[k] in (None, "")]
    if missing:
        return f"bad args: missing {missing}", {}
    return None, cleaned
