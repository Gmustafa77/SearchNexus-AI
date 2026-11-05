"""FastAPI entrypoint: validation, rate limiting, streaming, observability, error mapping, static UI."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.datastructures import MutableHeaders

from . import metrics
from .agent import SearchAssistant
from .config import Settings, get_settings
from .errors import AssistantError
from .observability import configure_logging, request_id_var
from .schemas import SESSION_ID_PATTERN, ChatRequest, ChatResponse, FeedbackRequest

log = logging.getLogger("app")

# No inline script/style: the UI is split into app.js / app.css so a strict CSP can block injected code.
# Google Fonts is the only third-party origin. Microphone is allowed for same-origin voice input only.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
    ),
}
_RID = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")
_KNOWN_PATHS = {"/api/chat", "/api/chat/stream", "/api/feedback", "/health", "/ready", "/metrics"}
SSE_HEARTBEAT_S = 15


def _path_label(path: str) -> str:
    """Bounded label cardinality for metrics."""
    if path in _KNOWN_PATHS:
        return path
    return "/api/sessions/{id}" if path.startswith("/api/sessions/") else "static"


class RequestContextMiddleware:
    """Pure-ASGI (safe for streaming): request id, security headers, HTTP metrics."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        inbound = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = inbound if _RID.match(inbound) else uuid.uuid4().hex
        token, path, started, status = request_id_var.set(rid), scope["path"], time.perf_counter(), 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = rid
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if path.startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            label = _path_label(path)
            metrics.HTTP_REQUESTS.labels(label, scope["method"], str(status)).inc()
            metrics.HTTP_LATENCY.labels(label).observe(time.perf_counter() - started)
            request_id_var.reset(token)


class RateLimiter:
    """Per-client sliding window. Swap for Redis when running more than one instance.

    Behind a proxy/load balancer every user shares the proxy's IP unless uvicorn is told which proxies to
    trust (--proxy-headers + FORWARDED_ALLOW_IPS); see README.
    """

    def __init__(self, per_minute: int):
        self.per_minute, self.hits, self._last_gc = per_minute, defaultdict(deque), time.monotonic()

    def _gc(self, now: float) -> None:
        """Amortised cleanup (at most once a minute) instead of an O(n) rebuild on every request."""
        self._last_gc = now
        for key in [k for k, q in self.hits.items() if not q or now - q[-1] > 60]:
            del self.hits[key]

    def check(self, key: str) -> None:
        now = time.monotonic()
        if now - self._last_gc > 60:
            self._gc(now)
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            raise HTTPException(429, "Too many requests. Please slow down.")
        q.append(now)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(settings: Settings | None = None, assistant: SearchAssistant | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    limiter = RateLimiter(settings.rate_limit_per_min)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if assistant is None and not settings.google_api_key:
            log.warning("GOOGLE_API_KEY is not set; /api/chat will fail until it is configured.")
        app.state.assistant = assistant or SearchAssistant(settings)
        yield

    app = FastAPI(title="AI Search Assistant", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(AssistantError)
    async def _assistant_error(_: Request, exc: AssistantError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException):
        return JSONResponse({"error": {"code": str(exc.status_code), "message": exc.detail}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"error": {"code": "internal", "message": "Something went wrong. Please try again."}}, status_code=500)

    def rate_limit(request: Request) -> None:
        limiter.check(request.client.host if request.client else "unknown")

    def check_length(req: ChatRequest) -> None:
        if len(req.message) > settings.max_message_chars:
            raise HTTPException(422, f"Message too long (max {settings.max_message_chars} characters).")

    # ---- ops endpoints ----
    @app.get("/health")
    async def health():
        """Liveness: the process is up. Never touches the LLM."""
        return {"status": "ok", "search_configured": bool(settings.google_api_key)}

    @app.get("/ready")
    async def ready(request: Request):
        """Readiness: key present and configured models exist. Use for orchestrator probes and alerting."""
        ok, detail = await request.app.state.assistant.ready()
        return JSONResponse({"status": "ready" if ok else "not_ready", "detail": detail}, status_code=200 if ok else 503)

    @app.get("/metrics")
    async def prom_metrics():
        # Restrict at the network/proxy layer in production; it is unauthenticated here.
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ---- chat ----
    @app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(rate_limit)])
    async def chat(req: ChatRequest, request: Request):
        check_length(req)
        return await request.app.state.assistant.chat(req.message, req.session_id)

    @app.post("/api/chat/stream", dependencies=[Depends(rate_limit)])
    async def chat_stream(req: ChatRequest, request: Request):
        """Server-sent events: `status` events while the agent works, then one `result` (or `error`)."""
        check_length(req)
        assistant_ = request.app.state.assistant
        queue: asyncio.Queue = asyncio.Queue()

        async def worker():
            try:
                result = await assistant_.chat(req.message, req.session_id, lambda ev: queue.put_nowait(("status", ev)))
                await queue.put(("result", result.model_dump()))
            except AssistantError as exc:
                await queue.put(("error", {"code": exc.code, "message": exc.message, "status": exc.status}))
            except Exception:
                log.exception("stream worker failed")
                await queue.put(("error", {"code": "internal", "message": "Something went wrong. Please try again.", "status": 500}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(worker())  # copies the request-id context

        async def events():
            try:
                yield _sse("status", {"stage": "start"})
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), SSE_HEARTBEAT_S)
                    except TimeoutError:
                        yield ": ping\n\n"  # keeps proxies from closing an idle connection
                        continue
                    if item is None:
                        return
                    yield _sse(*item)
            finally:
                if not task.done():  # client left or pressed Stop: stop paying for the LLM run
                    task.cancel()

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/feedback", status_code=202, dependencies=[Depends(rate_limit)])
    async def feedback(fb: FeedbackRequest):
        metrics.FEEDBACK.labels(fb.rating).inc()
        log.info("feedback rating=%s response_id=%s session_id=%s", fb.rating, fb.response_id, fb.session_id,
                 extra={"fields": {"event": "feedback", "rating": fb.rating, "response_id": fb.response_id, "session_id": fb.session_id}})
        return {"status": "recorded"}

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def reset(session_id: str, request: Request):
        if not re.match(SESSION_ID_PATTERN, session_id):
            raise HTTPException(422, "Invalid session id.")
        await request.app.state.assistant.reset(session_id)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse({"error": {"code": "422", "message": "Please enter a question (valid text, not empty)."}}, status_code=422)

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
    return app


app = create_app()
