"""Rotating JSON operational logs; tokens, headers and request bodies are omitted."""
import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = dict(at=datetime.now(timezone.utc).isoformat(), level=record.levelname,
                       logger=record.name, message=record.getMessage())
        for field in ('command', 'requestId', 'tick', 'status'):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload['errorType'] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def configure(path):
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = JsonFormatter()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logfile = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding='utf-8')
    logfile.setFormatter(formatter)
    root.handlers[:] = [console, logfile]
