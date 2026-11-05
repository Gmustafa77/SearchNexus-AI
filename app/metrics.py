"""Prometheus metrics. Module-level so they register once per process."""
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("lookout_http_requests_total", "HTTP requests", ["path", "method", "status"])
HTTP_LATENCY = Histogram("lookout_http_request_seconds", "HTTP request latency", ["path"],
                         buckets=(0.05, 0.25, 1, 3, 6, 10, 20, 40, 60, 90))
CHAT_OUTCOMES = Counter("lookout_chat_outcomes_total", "Chat turns by outcome", ["outcome"])
CACHE = Counter("lookout_cache_total", "Answer cache lookups", ["result"])  # hit | miss
SEARCHES = Counter("lookout_web_searches_total", "Search-tool calls issued by the agent")
# ADK only surfaces the OUTER agent's usage; the search sub-agent's tokens are not included.
TOKENS = Counter("lookout_agent_tokens_total", "Outer-agent LLM tokens (excludes search sub-agent)", ["kind"])
INFLIGHT = Gauge("lookout_inflight_chats", "Chat turns currently running")
FEEDBACK = Counter("lookout_feedback_total", "User feedback", ["rating"])
