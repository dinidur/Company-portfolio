"""
Structured (JSON) logging.

Every log line is one JSON object, so it can go straight to Loki / CloudWatch / ELK.
request_id, user and session are put in contextvars by the API middleware, so
every log line inside one request automatically carries them.
"""
import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import datetime, timezone

from config.settings import load_yaml

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_var: ContextVar[str] = ContextVar("user", default="-")
session_var: ContextVar[str] = ContextVar("session", default="-")

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "user": user_var.get(),
            "session": session_var.get(),
        }
        # anything passed with extra={...} goes in as its own field
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                data[key] = value
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        try:
            logging.config.dictConfig(load_yaml("logging.yaml"))
        except Exception:  # never let logging config kill the app
            logging.basicConfig(level=logging.INFO)
        _configured = True
    return logging.getLogger(f"app.{name}")
