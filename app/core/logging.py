"""JSON logs. Custom fields go in `extra=` and are copied only when present,
so a missing field can never raise inside the logging module (the bug that
hid Citebase's original outage). Every handler also masks URL passwords, so a
DATABASE_URL inside a message or traceback never reaches the logs."""
import json
import logging
import re
from datetime import datetime, timezone

# scheme://user:password@ -> scheme://user:***@
_URL_CREDS = re.compile(r"(?P<head>[a-zA-Z][a-zA-Z0-9+.-]*://[^:/@\s]*):[^@\s]*@")


def redact_url(text: object) -> str:
    """Mask the password in any URL inside `text` (a URL, a message, a traceback)."""
    return _URL_CREDS.sub(r"\g<head>:***@", str(text))


class RedactFilter(logging.Filter):
    """Masks URL passwords in the message and traceback of every record it sees."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact_url(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_url(record.exc_text)
        return True


EXTRA = ("event_id", "decision_id", "incident_id", "kind", "action", "degraded", "latency_ms", "rounds")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in EXTRA:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_text or record.exc_info:
            payload["exc"] = record.exc_text or self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(json_logs: bool = True) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
