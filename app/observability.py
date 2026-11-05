"""Request-scoped logging context and JSON log formatting."""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_MARK = "_lookout_handler"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                "logger": record.name, "msg": record.getMessage(), "request_id": getattr(record, "request_id", "-")}
        data.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Idempotent: safe to call from create_app() many times (tests do)."""
    root = logging.getLogger()
    root.setLevel(level)
    handler = next((h for h in root.handlers if getattr(h, _MARK, False)), None)
    if handler is None:
        handler = logging.StreamHandler()
        setattr(handler, _MARK, True)
        handler.addFilter(RequestIdFilter())
        root.addHandler(handler)
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
