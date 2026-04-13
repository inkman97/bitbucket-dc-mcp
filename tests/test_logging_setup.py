"""Tests for logging with secret redaction and audit events."""

import json
import logging
from pathlib import Path

import pytest

from bitbucket_dc_mcp.config import ServerConfig
from bitbucket_dc_mcp.logging_setup import (
    AuditLogger,
    SecretRedactingFilter,
    build_operational_logger,
)


def _make_config(tmp_path: Path, token: str = "supersecret-token") -> ServerConfig:
    return ServerConfig(
        base_url="https://bitbucket.example.com",
        token=token,
        username="alice",
        default_project="PROJ",
        workspace_dir=tmp_path,
        allowed_hosts=frozenset({"bitbucket.example.com"}),
        git_timeout=300,
        http_timeout=30,
        max_file_bytes=1024 * 1024,
        session_id="test-session",
        audit_log_path=tmp_path / "audit.log",
    )


class TestSecretRedactingFilter:
    def test_redacts_secret_in_simple_message(self):
        filt = SecretRedactingFilter(["my-secret"])
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="leaked my-secret here", args=(), exc_info=None,
        )
        filt.filter(record)
        assert "my-secret" not in str(record.msg)
        assert "***REDACTED***" in str(record.msg)

    def test_does_not_touch_unrelated_text(self):
        filt = SecretRedactingFilter(["my-secret"])
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="safe content", args=(), exc_info=None,
        )
        filt.filter(record)
        assert str(record.msg) == "safe content"

    def test_handles_empty_secret_list(self):
        filt = SecretRedactingFilter([])
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="anything", args=(), exc_info=None,
        )
        filt.filter(record)
        assert str(record.msg) == "anything"

    def test_redacts_format_args(self):
        filt = SecretRedactingFilter(["topsecret"])
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="token is %s", args=("topsecret",), exc_info=None,
        )
        filt.filter(record)
        assert "topsecret" not in str(record.msg)


class TestAuditLogger:
    def test_emits_valid_json(self, tmp_path: Path):
        config = _make_config(tmp_path)
        audit = AuditLogger(config)
        audit.emit(
            tool_invoked="bitbucket_clone_repo",
            parameters_used={"repo_slug": "my-repo"},
            response_summary="cloned at /tmp/repo",
            outcome="success",
        )
        # Flush file handlers
        for handler in logging.getLogger("bitbucket-dc-mcp.audit").handlers:
            handler.flush()

        content = (tmp_path / "audit.log").read_text()
        lines = [l for l in content.strip().split("\n") if l]
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert record["tool_invoked"] == "bitbucket_clone_repo"
        assert record["parameters_used"] == {"repo_slug": "my-repo"}
        assert record["outcome"] == "success"
        assert record["user_id"] == "alice"
        assert record["session_id"] == "test-session"
        assert record["agent_id"] == "bitbucket-dc-mcp"
        assert "timestamp" in record

    def test_redacts_token_in_audit_record(self, tmp_path: Path):
        config = _make_config(tmp_path, token="leaky-token")
        audit = AuditLogger(config)
        # Simulate a scenario where a token somehow ends up in a response
        audit.emit(
            tool_invoked="bitbucket_clone_repo",
            parameters_used={"repo_slug": "normal"},
            response_summary="something with leaky-token inside",
            outcome="success",
        )
        for handler in logging.getLogger("bitbucket-dc-mcp.audit").handlers:
            handler.flush()

        content = (tmp_path / "audit.log").read_text()
        assert "leaky-token" not in content
        assert "***REDACTED***" in content

    def test_sanitizes_sensitive_parameter_keys(self, tmp_path: Path):
        config = _make_config(tmp_path)
        audit = AuditLogger(config)
        audit.emit(
            tool_invoked="bitbucket_clone_repo",
            parameters_used={
                "repo_slug": "my-repo",
                "token": "should-not-appear",
                "password": "nor-this",
            },
            response_summary="ok",
            outcome="success",
        )
        for handler in logging.getLogger("bitbucket-dc-mcp.audit").handlers:
            handler.flush()

        content = (tmp_path / "audit.log").read_text()
        assert "should-not-appear" not in content
        assert "nor-this" not in content

    def test_truncates_long_response_summary(self, tmp_path: Path):
        config = _make_config(tmp_path)
        audit = AuditLogger(config)
        huge = "x" * 1000
        audit.emit(
            tool_invoked="bitbucket_clone_repo",
            parameters_used={},
            response_summary=huge,
            outcome="success",
        )
        for handler in logging.getLogger("bitbucket-dc-mcp.audit").handlers:
            handler.flush()

        content = (tmp_path / "audit.log").read_text()
        lines = [l for l in content.strip().split("\n") if l]
        record = json.loads(lines[-1])
        assert len(record["response_summary"]) == 500


class TestOperationalLogger:
    def test_builds_without_error(self, tmp_path: Path):
        config = _make_config(tmp_path)
        logger = build_operational_logger(config)
        assert logger.name == "bitbucket-dc-mcp"
        # Logger should have exactly one handler after building
        assert len(logger.handlers) == 1
