from __future__ import annotations

import logging
import re
from typing import Any


SENSITIVE_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),  # OpenAI-style key
    re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]+)", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[=:]\s*)([^\s,;]+)", re.IGNORECASE),
]


def _redact_text(text: str) -> str:
    output = text
    for pattern in SENSITIVE_PATTERNS:
        if pattern.pattern.startswith("(Bearer"):
            output = pattern.sub(r"\1[REDACTED]", output)
        elif pattern.pattern.startswith("(api"):
            output = pattern.sub(r"\1[REDACTED]", output)
        else:
            output = pattern.sub("[REDACTED]", output)
    return output


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                sanitized: list[Any] = []
                for arg in record.args:
                    if isinstance(arg, str):
                        sanitized.append(_redact_text(arg))
                    else:
                        sanitized.append(arg)
                record.args = tuple(sanitized)
            elif isinstance(record.args, str):
                record.args = _redact_text(record.args)
        return True


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level)
    root.setLevel(level)

    already_present = any(isinstance(f, SecretRedactionFilter) for f in root.filters)
    if not already_present:
        redactor = SecretRedactionFilter()
        root.addFilter(redactor)
        for handler in root.handlers:
            handler.addFilter(redactor)
