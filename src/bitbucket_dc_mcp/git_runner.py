"""Thin wrapper around git subprocess calls with safety guarantees."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git command fails or times out."""


class GitRunner:
    """Runs git commands with a timeout and redacts the token from errors.

    On Windows we route stdout/stderr through real temporary files rather
    than anonymous pipes. This is a workaround for a Git for Windows
    subprocess hang that can occur when git's standard handles are pipes
    created by Python: git blocks during early process init before
    loading any networking DLL, waiting on a handle operation that never
    completes. Writing to real files avoids this entirely.

    On Linux and macOS we use the standard subprocess.Popen + communicate
    pattern with PIPE, which is reliable and avoids unnecessary disk I/O.
    """

    def __init__(self, token: str, timeout: int) -> None:
        self._token = token
        self._timeout = timeout

    def run(self, args: list[str], cwd: Path | None = None) -> str:
        cmd = ["git"] + args
        if sys.platform == "win32":
            return self._run_windows(cmd, args, cwd)
        return self._run_posix(cmd, args, cwd)

    def _run_posix(
            self,
            cmd: list[str],
            args: list[str],
            cwd: Path | None,
    ) -> str:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                stdout, stderr = proc.communicate(timeout=self._timeout)
            except subprocess.TimeoutExpired as e:
                proc.kill()
                proc.communicate()
                safe_cmd = " ".join(args[:3])
                raise GitError(
                    f"git command timed out after {self._timeout}s: {safe_cmd}"
                ) from e
        except FileNotFoundError as e:
            raise GitError(
                f"git executable not found in PATH: {e}"
            ) from e

        return self._handle_result(proc.returncode, stdout, stderr)

    def _run_windows(
            self,
            cmd: list[str],
            args: list[str],
            cwd: Path | None,
    ) -> str:
        stdout_file = tempfile.NamedTemporaryFile(
            mode="w+", delete=False, encoding="utf-8", errors="replace",
            suffix=".git_stdout",
        )
        stderr_file = tempfile.NamedTemporaryFile(
            mode="w+", delete=False, encoding="utf-8", errors="replace",
            suffix=".git_stderr",
        )
        stdout_path = stdout_file.name
        stderr_path = stderr_file.name
        stdout_file.close()
        stderr_file.close()

        try:
            with open(stdout_path, "w", encoding="utf-8") as out_f, \
                    open(stderr_path, "w", encoding="utf-8") as err_f:
                try:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=cwd,
                        stdout=out_f,
                        stderr=err_f,
                        stdin=subprocess.DEVNULL,
                    )
                except FileNotFoundError as e:
                    raise GitError(
                        f"git executable not found in PATH: {e}"
                    ) from e
                try:
                    proc.wait(timeout=self._timeout)
                except subprocess.TimeoutExpired as e:
                    proc.kill()
                    proc.wait()
                    safe_cmd = " ".join(args[:3])
                    raise GitError(
                        f"git command timed out after {self._timeout}s: {safe_cmd}"
                    ) from e

            with open(stdout_path, "r", encoding="utf-8", errors="replace") as f:
                stdout = f.read()
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
                stderr = f.read()
        finally:
            try:
                os.unlink(stdout_path)
            except OSError:
                pass
            try:
                os.unlink(stderr_path)
            except OSError:
                pass

        return self._handle_result(proc.returncode, stdout, stderr)

    def _handle_result(
            self,
            returncode: int,
            stdout: str,
            stderr: str,
    ) -> str:
        if returncode != 0:
            stderr_safe = (stderr or "").replace(self._token, "***REDACTED***")
            stdout_safe = (stdout or "").replace(self._token, "***REDACTED***")
            detail = f"exit code {returncode}"
            if stderr_safe.strip():
                detail += f": {stderr_safe.strip()[:1000]}"
            elif stdout_safe.strip():
                detail += f" (stdout: {stdout_safe.strip()[:500]})"
            raise GitError(f"git failed: {detail}")
        return stdout or ""

    def auth_header_args(self) -> list[str]:
        """Return ephemeral -c args so the token is never persisted,
        no credential helper is consulted, and LFS filters are bypassed.
        """
        return [
            "-c",
            "credential.helper=",
            "-c",
            "filter.lfs.required=false",
            "-c",
            "filter.lfs.smudge=cat",
            "-c",
            "filter.lfs.clean=cat",
            "-c",
            "filter.lfs.process=",
            "-c",
            f"http.extraheader=Authorization: Bearer {self._token}",
        ]
