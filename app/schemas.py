"""Pydantic models: the LLM output contract, the API contract, and lenient parsing."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

log = logging.getLogger(__name__)
SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
# Fields only the server may set; a model-supplied value is always ignored.
_API_ONLY = {"session_id", "warnings", "latency_ms", "cached", "search_queries", "response_id"}


class Source(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^https?://\S+$", v, re.I):
            raise ValueError("url must be http(s)")
        return v


class LLMAnswer(BaseModel):
    """Strict schema handed to the model (no defaults, so every field is requested)."""

    answer: str
    summary: str
    sources: list[Source]
    key_points: list[str]
    confidence_score: float = Field(ge=0, le=1)
    follow_up_questions: list[str]
    search_performed: bool


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)

    @field_validator("message")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty")
        return v


class ChatResponse(BaseModel):
    """API response. Tolerant defaults so a partial model reply still renders."""

    answer: str
    summary: str = ""
    sources: list[Source] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0, le=1)
    follow_up_questions: list[str] = Field(default_factory=list)
    search_performed: bool = False
    session_id: str = ""
    warnings: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    response_id: str = ""  # correlates logs, feedback and support tickets to one answer
    cached: bool = False
    search_queries: list[str] = Field(default_factory=list)  # what the agent actually searched for


class FeedbackRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    response_id: str = Field(pattern=SESSION_ID_PATTERN)
    rating: Literal["up", "down"]


def _strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)


def parse_llm_output(text: str) -> tuple[ChatResponse, list[str]]:
    """Validate model output; if invalid, salvage valid fields. Never raises."""
    warnings: list[str] = []
    body = _strip_fences(text or "")
    try:
        return ChatResponse(**LLMAnswer.model_validate_json(body).model_dump()), warnings
    except ValidationError as exc:
        log.warning("LLM output failed strict validation (%d errors)", exc.error_count())

    try:
        raw: Any = json.loads(body)
        if not isinstance(raw, dict):
            raise ValueError("not an object")
    except ValueError:
        warnings.append("The model returned unstructured text; some fields are unavailable.")
        return ChatResponse(answer=body or "I could not produce an answer."), warnings

    good: dict[str, Any] = {"answer": "x"}  # placeholder so single-field probes validate
    for name, value in raw.items():
        if name not in ChatResponse.model_fields or name in _API_ONLY:
            continue
        if name == "sources" and isinstance(value, list):
            kept = []
            for item in value:
                try:
                    kept.append(Source.model_validate(item))
                except ValidationError:
                    warnings.append("An invalid source entry was dropped.")
            good[name] = kept
            continue
        if name == "confidence_score" and isinstance(value, (int, float)) and not isinstance(value, bool):
            good[name] = min(1.0, max(0.0, float(value)))
            continue
        try:
            good[name] = getattr(ChatResponse(**{**good, name: value}), name)
        except ValidationError:
            warnings.append(f"Field '{name}' was invalid and was dropped.")
    if not isinstance(good.get("answer"), str) or good["answer"] in ("", "x"):
        good["answer"] = "I could not produce a complete answer."
        warnings.append("The model response was missing the answer field.")
    return ChatResponse(**good), warnings
