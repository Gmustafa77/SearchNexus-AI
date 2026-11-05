"""Google Search component: builds the GoogleSearchAgentTool and reads its grounding data."""
from __future__ import annotations

from google.adk.events import Event
from google.adk.models import BaseLlm
from google.adk.tools.google_search_agent_tool import GoogleSearchAgentTool, create_google_search_agent

SEARCH_TOOL_NAME = "google_search_agent"  # name ADK gives the tool; asserted in tests


def build_search_tool(model: str | BaseLlm) -> GoogleSearchAgentTool:
    """Wrap a google_search-only sub-agent as a tool so it can coexist with output_schema."""
    return GoogleSearchAgentTool(create_google_search_agent(model))


def extract_grounded_sources(event: Event) -> dict[str, str]:
    """Return {url: title} for every web chunk Google grounded this event on."""
    gm = getattr(event, "grounding_metadata", None)
    found: dict[str, str] = {}
    for chunk in (getattr(gm, "grounding_chunks", None) or []):
        web = getattr(chunk, "web", None)
        if web and web.uri:
            found[web.uri] = web.title or web.domain or web.uri
    return found
