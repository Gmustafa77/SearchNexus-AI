FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

# Dependencies first so this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Run as an unprivileged user: a compromised process can't modify the image or bind privileged ports.
RUN useradd --system --uid 10001 --no-create-home app
COPY --chown=app:app app ./app
USER app

EXPOSE 8000
# Liveness only (never calls the LLM). Wire /ready into your orchestrator's readiness probe.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# --proxy-headers + FORWARDED_ALLOW_IPS (env) make the per-client rate limiter see real client IPs behind a
# load balancer. Graceful shutdown lets in-flight answers finish during a rolling deploy.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "25"]
