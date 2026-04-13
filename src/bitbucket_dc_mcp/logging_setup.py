"""Operational and audit logging with automatic secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ServerConfig


class SecretRedactingFilter(logging.Filter):
    """Replace known secrets with a placeholder in every log record."""

    def __init__(self, secrets_to_redact: list[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets_to_redact if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            try:
                record.msg = record.msg % record.args
                record.args = ()
            except Exception:
                pass
        message = str(record.msg)
        for secret in self._secrets:
            if secret and secret in message:
                message = message.replace(secret, "***REDACTED***")
        record.msg = message
        return True


def _sanitize_parameters(params: dict[str, Any]) -> dict[str, Any]:
    """Remove or truncate sensitive or oversized fields before logging."""
    SENSITIVE_KEYS = {"token", "password", "secret", "authorization"}
    out: dict[str, Any] = {}
    for key, value in params.items():
        if key.lower() in SENSITIVE_KEYS:
            out[key] = "***REDACTED***"
        elif isinstance(value, str) and len(value) > 500:
            out[key] = value[:500] + "...[truncated]"
        else:
            out[key] = value
    return out


class AuditLogger:
    """Emits structured JSON audit events per the security guidelines."""

    def __init__(self, config: ServerConfig) -> None:
        self._config = config
        self._logger = logging.getLogger("bitbucket-dc-mcp.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        handler = logging.FileHandler(
            config.audit_log_path, mode="a", encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(SecretRedactingFilter([config.token]))
        self._logger.addHandler(handler)

    def emit(
        self,
        tool_invoked: str,
        parameters_used: dict[str, Any],
        response_summary: str,
        outcome: str,
        error_type: str | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": self._config.agent_id,
            "session_id": self._config.session_id,
            "tool_invoked": tool_invoked,
            "parameters_used": _sanitize_parameters(parameters_used),
            "response_summary": response_summary[:500],
            "user_id": self._config.username,
            "outcome": outcome,
            "error_type": error_type,
        }
        self._logger.info(json.dumps(record, ensure_ascii=False))


def build_operational_logger(config: ServerConfig) -> logging.Logger:
    """Configure and return the stderr-backed operational logger."""
    logger = logging.getLogger("bitbucket-dc-mcp")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    handler.addFilter(SecretRedactingFilter([config.token]))

    # Avoid duplicate handlers if called more than once
    logger.handlers.clear()
    logger.addHandler(handler)
    return logger
