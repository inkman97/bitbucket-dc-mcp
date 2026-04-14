"""Configuration loaded from environment variables at startup."""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class ServerConfig:
    base_url: str
    token: str
    username: str
    default_project: str
    workspace_dir: Path
    allowed_hosts: frozenset[str]
    git_timeout: int
    http_timeout: int
    max_file_bytes: int
    session_id: str
    audit_log_path: Path
    agent_id: str = "bitbucket-dc-mcp"
    lfs_mode: str = "disabled"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"required environment variable {name} is not set"
        )
    return value


def _optional_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ConfigError(
            f"environment variable {name} must be an integer"
        ) from e
    if value <= 0:
        raise ConfigError(
            f"environment variable {name} must be a positive integer"
        )
    return value


def _parse_allowed_hosts(raw: str, base_url: str) -> frozenset[str]:
    """Parse the allowlist.

    If the user explicitly sets BITBUCKET_ALLOWED_HOSTS, trust that value
    alone and do NOT silently add the base_url host: an explicit allowlist
    must be respected exactly. The startup host check in load_config() will
    still catch mismatches between base_url and the allowlist.

    If the user does not set it, fall back to the base_url host so that the
    server has a sane default for a single-tenant deployment.
    """
    if raw.strip():
        hosts: set[str] = set()
        for item in raw.split(","):
            item = item.strip().lower()
            if item:
                hosts.add(item)
        if not hosts:
            raise ConfigError(
                "BITBUCKET_ALLOWED_HOSTS is set but contains no valid host"
            )
        return frozenset(hosts)

    base_host = urlparse(base_url).hostname
    if not base_host:
        raise ConfigError(
            "could not determine allowed hosts; set BITBUCKET_ALLOWED_HOSTS"
        )
    return frozenset({base_host.lower()})


def load_config() -> ServerConfig:
    """Load configuration from environment. Raises ConfigError on failure."""
    base_url = _require_env("BITBUCKET_BASE_URL").rstrip("/")
    token = _require_env("BITBUCKET_TOKEN")
    username = _require_env("BITBUCKET_USERNAME")
    default_project = os.environ.get("BITBUCKET_DEFAULT_PROJECT", "").strip()
    lfs_mode_raw = os.environ.get("BITBUCKET_LFS_MODE", "disabled").strip().lower()
    if lfs_mode_raw not in ("disabled", "enabled", "auto"):
        raise ConfigError(
            f"BITBUCKET_LFS_MODE must be 'disabled', 'enabled', or 'auto', "
            f"got '{lfs_mode_raw}'"
        )
    lfs_mode = lfs_mode_raw
    workspace_dir = Path(
        os.environ.get(
            "BITBUCKET_WORKSPACE", str(Path.home() / "mcp-workspace")
        )
    ).resolve()

    git_timeout = _optional_int("BITBUCKET_GIT_TIMEOUT", 300)
    http_timeout = _optional_int("BITBUCKET_HTTP_TIMEOUT", 30)
    max_file_bytes = _optional_int("BITBUCKET_MAX_FILE_BYTES", 1024 * 1024)

    session_id = (
            os.environ.get("BITBUCKET_SESSION_ID", "").strip()
            or secrets.token_hex(8)
    )

    allowed_hosts = _parse_allowed_hosts(
        os.environ.get("BITBUCKET_ALLOWED_HOSTS", ""),
        base_url,
    )

    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise ConfigError("BITBUCKET_BASE_URL must use https scheme")
    host = (parsed.hostname or "").lower()
    if host not in allowed_hosts:
        raise ConfigError(
            f"BITBUCKET_BASE_URL host '{host}' is not in allowed hosts"
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    audit_log_path = Path(
        os.environ.get(
            "BITBUCKET_AUDIT_LOG_PATH", str(workspace_dir / "audit.log")
        )
    ).resolve()
    audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    return ServerConfig(
        base_url=base_url,
        token=token,
        username=username,
        default_project=default_project,
        workspace_dir=workspace_dir,
        allowed_hosts=allowed_hosts,
        git_timeout=git_timeout,
        http_timeout=http_timeout,
        max_file_bytes=max_file_bytes,
        session_id=session_id,
        audit_log_path=audit_log_path,
        lfs_mode=lfs_mode
    )
