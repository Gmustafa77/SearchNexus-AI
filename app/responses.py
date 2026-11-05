"""Response generation: validates model output and replaces claimed sources with verified ones."""
from __future__ import annotations

import uuid

from .schemas import ChatResponse, Source, parse_llm_output

MAX_SOURCES = 8
MAX_QUERIES = 5


def confidence_cap(distinct_sources: int) -> float:
    """Evidence ceiling for confidence. The model's self-rating is a poor calibrator, so it may never
    exceed what the evidence supports: one source can't corroborate itself."""
    if distinct_sources <= 0:
        return 0.4
    if distinct_sources == 1:
        return 0.6
    if distinct_sources == 2:
        return 0.85
    return 1.0


def build_response(
    raw_text: str,
    grounded: dict[str, str],
    session_id: str,
    latency_ms: int,
    *,
    search_queries: list[str] | tuple[str, ...] = (),
    cached: bool = False,
) -> ChatResponse:
    resp, warnings = parse_llm_output(raw_text)
    claimed = len(resp.sources)

    # Only URLs Google actually returned may appear as citations; model-written URLs are discarded.
    resp.sources = [Source(title=t[:300], url=u) for u, t in list(grounded.items())[:MAX_SOURCES]]
    if claimed and not grounded:
        warnings.append("Source links the model proposed could not be verified and were removed.")

    if grounded:
        resp.search_performed = True
        distinct = len({s.title.casefold() for s in resp.sources})
        resp.confidence_score = min(resp.confidence_score, confidence_cap(distinct))
    elif resp.search_performed:
        # The model says it searched, but no grounding evidence exists: don't trust it.
        resp.search_performed = False
        resp.confidence_score = min(resp.confidence_score, 0.4)
        warnings.append("No search evidence was returned, so this answer is unverified.")

    resp.session_id, resp.latency_ms, resp.warnings = session_id, latency_ms, warnings
    resp.response_id, resp.cached = uuid.uuid4().hex, cached
    resp.search_queries = [q[:200] for q in dict.fromkeys(search_queries)][:MAX_QUERIES]
    return resp
