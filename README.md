# Lookout: AI search assistant (Google ADK + GoogleSearchAgentTool)

Developed by **Gulam Mustafa**.

## Features
- **Grounded answers**: citations come only from Google's grounding data; model-written URLs are discarded.
- **Live progress**: answers stream as server-sent events ("Searching the web: “…”", "Writing your answer…"), with a
  **Stop** button that really cancels the paid LLM run, **Retry**, **Copy**, and **Helpful / Not helpful** feedback.
- **Evidence-calibrated confidence**: the model's self-rating is capped by how many independent sources back the answer.
- **Cost controls**: first-turn answer cache, capped conversation history, optional cheaper model for the search sub-agent.
- **Voice input**: tap the mic to dictate a question (English, Hindi, Urdu). Uses the browser's Web Speech API, so it
  works in Chrome, Edge and Safari over HTTPS or localhost; the mic hides itself where unsupported (e.g. Firefox).
  Note: Chrome sends the audio to Google's speech service to transcribe it.
- Light and dark themes (follows the OS setting).

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # add GOOGLE_API_KEY (Gemini API key from aistudio.google.com)
uvicorn app.main:app --reload   # open http://localhost:8000
pytest                          # offline tests (fake runner + a scripted LLM driving the real ADK pipeline)
(cd tests/ui && npm install && npm test)   # headless UI smoke test (jsdom)
```
Docker: `docker build -t lookout . && docker run -p 8000:8000 --env-file .env lookout`

## Go-live checklist (do this once, with a real key)
1. `curl localhost:8000/ready`: must say `ready`. A `not_ready` with HTTP 404 means a configured model was **retired**
   (`gemini-2.5-flash` had a published shutdown date of 2026-10-16); change `LLM_MODEL` / `SEARCH_MODEL`.
2. Ask a real question in the UI. Confirm: progress lines appear, sources are listed, "Searched for:" shows queries.
3. Ask a follow-up ("and its price?") and check it keeps context.
4. Point your orchestrator's **liveness** probe at `/health` and its **readiness** probe at `/ready`; scrape `/metrics`.

## Architecture
| File | Role |
|---|---|
| `app/search.py` | Builds `GoogleSearchAgentTool`; extracts Google's grounding chunks |
| `app/agent.py` | ADK `LlmAgent` (tool + `output_schema`), sessions, answer cache, concurrency gate, timeout, retries, readiness probe |
| `app/context.py` | History guard: collapses duplicated turns and caps history sent to the model |
| `app/cache.py` | Bounded TTL cache + query normalisation |
| `app/responses.py` | Validates output; **citations only from grounding**; evidence-capped confidence |
| `app/schemas.py` | `LLMAnswer` (model contract), `ChatResponse` / `FeedbackRequest` (API), lenient repair |
| `app/main.py` | FastAPI: validation, rate limiting, SSE streaming, ops endpoints, security headers |
| `app/observability.py`, `app/metrics.py` | Request-id logging (text or JSON), Prometheus metrics |
| `app/static/{index.html,app.css,app.js}` | Chat UI (no inline code, so a strict CSP can block injected scripts) |

## API
| Endpoint | Purpose |
|---|---|
| `POST /api/chat` | One-shot JSON answer (unchanged contract; new optional fields `response_id`, `cached`, `search_queries`) |
| `POST /api/chat/stream` | SSE: `status` events, then one `result` (same JSON as above) or `error` |
| `POST /api/feedback` | `{session_id, response_id, rating: up\|down}` → counter + structured log line |
| `DELETE /api/sessions/{id}` | Forget a conversation |
| `GET /health` · `/ready` · `/metrics` | Liveness · readiness (key + models exist) · Prometheus |

## Configuration (see `.env.example`)
`LLM_MODEL`, `SEARCH_MODEL` (cheaper model for the search sub-agent), `MAX_CONCURRENT_REQUESTS` / `QUEUE_TIMEOUT_S`
(load shedding), `CACHE_TTL_S` (0 disables), `MAX_HISTORY_TURNS`, `LOG_FORMAT=json`, `SESSION_DB_URL`.

## Scaling notes
- **Conversation memory**: in-memory by default. Set `SESSION_DB_URL` (and `pip install -r requirements-db.txt`) for durable
  memory that survives restarts and works across instances (Postgres recommended; SQLite for a single box).
- **Still per-instance**: the rate limiter, answer cache and concurrency gate. For N instances use a Redis-backed limiter,
  and either sticky sessions or a shared lock per conversation.
- **Behind a load balancer**: run uvicorn with `--proxy-headers` and set `FORWARDED_ALLOW_IPS` to your proxy's address
  (the Dockerfile enables `--proxy-headers`); otherwise every user shares one rate-limit bucket.
- Terminate TLS and add HSTS at the proxy. Restrict `/metrics` to your monitoring network (it is unauthenticated).

## Known limitations
- **No authentication**: anyone who can reach the service spends your Gemini quota. Rate limiting and the concurrency gate
  bound the damage but are not access control. Put it behind SSO / an API gateway, or add auth.
- **Retries re-run the whole turn** (including the search) and ADK stores the user message once per attempt. The history
  guard hides the duplicates from the model, but the root-cause fix is SDK-level retry of only the failed call.
- `lookout_agent_tokens_total` counts the **outer agent only**; ADK does not expose the search sub-agent's usage.
- Feedback is counted and logged (ids only, no text); to analyse it you need a store with a retention policy.
