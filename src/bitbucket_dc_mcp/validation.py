"""Strict input validation for all tool parameters.

The validation rules here are the primary defense against prompt injection:
no input from the MCP client can reach git, the filesystem, or an HTTP
request without going through these validators first.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class ValidationError(ValueError):
    """Raised when a tool input fails validation."""


_SAFE_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}$")
_SAFE_BRANCH_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,199}$")
_SAFE_PROJECT_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def validate_repo_slug(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("repo_slug must be a string")
    value = value.strip()
    if not _SAFE_SLUG_RE.match(value):
        raise ValidationError(
            "repo_slug must match pattern [a-zA-Z0-9][a-zA-Z0-9._-]{0,199}"
        )
    return value


def validate_branch_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("branch name must be a string")
    value = value.strip()
    if not _SAFE_BRANCH_RE.match(value):
        raise ValidationError("branch name contains disallowed characters")
    if ".." in value or value.startswith("/") or value.endswith("/"):
        raise ValidationError("branch name has unsafe structure")
    return value


def validate_project_key(value: Any, default: str = "") -> str:
    if value is None or value == "":
        if not default:
            raise ValidationError(
                "project_key is required (no default configured)"
            )
        value = default
    if not isinstance(value, str):
        raise ValidationError("project_key must be a string")
    value = value.strip()
    if not _SAFE_PROJECT_KEY_RE.match(value):
        raise ValidationError(
            "project_key must match pattern [a-zA-Z0-9_-]{1,64}"
        )
    return value


def validate_file_path(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("file_path must be a string")
    # Check control chars on the raw input before any stripping, so that a
    # trailing \n or \r cannot sneak past.
    if any(ord(c) < 32 and c not in ("\t",) for c in value):
        raise ValidationError("file_path contains control characters")
    value = value.strip().replace("\\", "/")
    if not value:
        raise ValidationError("file_path cannot be empty")
    if value.startswith("/"):
        raise ValidationError("file_path must be relative")
    if ".." in value.split("/"):
        raise ValidationError("file_path must not contain ..")
    if len(value) > 1024:
        raise ValidationError("file_path too long")
    return value


def validate_commit_message(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("commit_message must be a string")
    value = value.strip()
    if not value:
        raise ValidationError("commit_message cannot be empty")
    if len(value) > 5000:
        raise ValidationError("commit_message too long (max 5000 chars)")
    clean = "".join(
        c for c in value if c >= " " or c in ("\t", "\n", "\r")
    )
    return clean


def validate_title(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("title must be a string")
    value = value.strip()
    if not value:
        raise ValidationError("title cannot be empty")
    if len(value) > 255:
        raise ValidationError("title too long (max 255 chars)")
    return value


def validate_description(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("description must be a string")
    if len(value) > 32768:
        raise ValidationError("description too long (max 32 KiB)")
    return value


def validate_pull_request_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValidationError("pull_request_id must be an integer")
    if isinstance(value, int):
        if value <= 0:
            raise ValidationError("pull_request_id must be positive")
        return value
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError as e:
            raise ValidationError(
                "pull_request_id must be a positive integer"
            ) from e
        if parsed <= 0:
            raise ValidationError("pull_request_id must be positive")
        return parsed
    raise ValidationError("pull_request_id must be an integer")


def validate_comment_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("comment text must be a string")
    value = value.strip()
    if not value:
        raise ValidationError("comment text cannot be empty")
    if len(value) > 32768:
        raise ValidationError("comment text too long (max 32 KiB)")
    clean = "".join(
        c for c in value if c >= " " or c in ("\t", "\n", "\r")
    )
    return clean


def validate_pull_request_state(value: Any) -> str:
    if value is None or value == "":
        return "OPEN"
    if not isinstance(value, str):
        raise ValidationError("state must be a string")
    upper = value.strip().upper()
    if upper not in ("OPEN", "DECLINED", "MERGED", "ALL"):
        raise ValidationError(
            "state must be one of OPEN, DECLINED, MERGED, ALL"
        )
    return upper


def resolve_repo_path(workspace_dir: Path, repo_slug: str) -> Path:
    """Resolve the repo path and ensure it stays within the workspace."""
    candidate = (workspace_dir / repo_slug).resolve()
    try:
        candidate.relative_to(workspace_dir)
    except ValueError as e:
        raise ValidationError(
            "resolved repo path escapes workspace directory"
        ) from e
    return candidate

def validate_file_content(content: str, max_bytes: int) -> str:
    """Validate content for write/edit operations.

    Ensures the content is a string and does not exceed the max size.
    """
    if not isinstance(content, str):
        raise ValidationError("content must be a string")
    size = len(content.encode("utf-8"))
    if size > max_bytes:
        raise ValidationError(
            f"content is {size} bytes, max is {max_bytes}"
        )
    return content


def resolve_file_in_repo(
        workspace_dir: Path,
        repo_slug: str,
        file_path: str,
) -> Path:
    """Resolve a file path inside a cloned repo, preventing escape.

    Returns an absolute Path that is guaranteed to be inside the repo
    directory. Raises ValidationError if the resolved path would escape.
    """
    repo_path = resolve_repo_path(workspace_dir, repo_slug)
    # Use validate_file_path first to reject obviously bad patterns
    clean_rel = validate_file_path(file_path)
    target = (repo_path / clean_rel).resolve()
    # Ensure target is under the repo path
    try:
        target.relative_to(repo_path.resolve())
    except ValueError:
        raise ValidationError(
            f"file_path '{file_path}' escapes the repository directory"
        )
    return target
