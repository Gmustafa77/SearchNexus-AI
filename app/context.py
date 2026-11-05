"""Context management: sanitise and bound the conversation history sent to the model on every turn.

1. Collapse duplicated user turns. ADK appends the user's message to the session on every attempt, so a
   retried request leaves the same question in history several times (verified against the real Runner).
2. Cap history to the last N user turns. Without a cap, each turn re-sends the entire history, including the
   bulky search-tool results of earlier turns, so cost and latency grow until the context window is exhausted.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _starts_user_turn(content: Any) -> bool:
    """True for a real user message (text), not a tool/function response that also has role 'user'."""
    if getattr(content, "role", None) != "user":
        return False
    parts = getattr(content, "parts", None) or []
    return any(getattr(p, "text", None) for p in parts) and not any(getattr(p, "function_response", None) for p in parts)


def _text(content: Any) -> str:
    return "".join(getattr(p, "text", None) or "" for p in (content.parts or []))


def collapse_duplicate_user_turns(contents: list[Any]) -> list[Any]:
    kept: list[Any] = []
    for c in contents:
        if kept and _starts_user_turn(c) and _starts_user_turn(kept[-1]) and _text(c) == _text(kept[-1]):
            continue
        kept.append(c)
    return kept


def make_history_guard(max_turns: int) -> Callable[[Any, Any], None]:
    """ADK before_model_callback. max_turns <= 0 disables the cap but still de-duplicates.

    The cut is made only at user-turn boundaries so function_call/function_response pairs are never split.
    """

    def _guard(callback_context: Any, llm_request: Any) -> None:
        contents = collapse_duplicate_user_turns(list(llm_request.contents or []))
        if max_turns > 0:
            starts = [i for i, c in enumerate(contents) if _starts_user_turn(c)]
            if len(starts) > max_turns:
                contents = contents[starts[-max_turns]:]
        llm_request.contents = contents
        return None  # None = proceed with the (sanitised) request

    return _guard
