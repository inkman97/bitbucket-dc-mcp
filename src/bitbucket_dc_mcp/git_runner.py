"""Thin wrapper around git subprocess calls with safety guarantees."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git command fails or times out."""


class GitRunner:
    """Runs git commands with a timeout and redacts the token from errors."""

    def __init__(self, token: str, timeout: int) -> None:
        self._token = token
        self._timeout = timeout

    def run(self, args: list[str], cwd: Path | None = None) -> str:
        cmd = ["git"] + args
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as e:
            safe_cmd = " ".join(args[:3])
            raise GitError(
                f"git command timed out after {self._timeout}s: {safe_cmd}"
            ) from e
        if result.returncode != 0:
            stderr = result.stderr.replace(self._token, "***REDACTED***")
            raise GitError(f"git failed: {stderr[:1000]}")
        return result.stdout

    def auth_header_args(self) -> list[str]:
        """Return ephemeral -c args so the token is never persisted."""
        return [
            "-c",
            f"http.extraheader=Authorization: Bearer {self._token}",
        ]
