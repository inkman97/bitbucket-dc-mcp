"""Tests for write-side tools: write_file, edit_file, apply_patch."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from bitbucket_dc_mcp.config import ServerConfig
from bitbucket_dc_mcp.git_runner import GitError, GitRunner
from bitbucket_dc_mcp.http_client import BitbucketHttpClient
from bitbucket_dc_mcp.logging_setup import AuditLogger
from bitbucket_dc_mcp.server import (
    Context,
    tool_apply_patch,
    tool_edit_file,
    tool_write_file,
)
from bitbucket_dc_mcp.validation import ValidationError


# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def fake_repo(tmp_workspace: Path) -> Path:
    """Create a fake cloned repo with a src/main.py file.

    No real git repo is needed for write_file and edit_file tests;
    just the directory structure the tools expect.
    """
    repo_path = tmp_workspace / "myrepo"
    repo_path.mkdir()
    (repo_path / "src").mkdir()
    (repo_path / "src" / "main.py").write_text(
        "def hello():\n    return 'world'\n",
        encoding="utf-8",
    )
    return repo_path


@pytest.fixture
def real_git_repo(tmp_workspace: Path) -> Path:
    """Create a real git repo with one initial commit.

    Only used by apply_patch tests that need `git apply` to work.
    """
    repo_path = tmp_workspace / "myrepo"
    repo_path.mkdir()
    (repo_path / "src").mkdir()
    (repo_path / "src" / "main.py").write_text(
        "def hello():\n    return 'world'\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "--quiet"], cwd=repo_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_path, check=True,
    )
    subprocess.run(
        ["git", "add", "."], cwd=repo_path, check=True
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "initial"],
        cwd=repo_path, check=True,
    )
    return repo_path


@pytest.fixture
def fake_config(tmp_workspace: Path) -> ServerConfig:
    return ServerConfig(
        base_url="https://bitbucket.example.com",
        token="fake-token",
        username="tester",
        default_project="TEST",
        workspace_dir=tmp_workspace,
        allowed_hosts=frozenset({"bitbucket.example.com"}),
        git_timeout=10,
        http_timeout=10,
        max_file_bytes=1_000_000,
        session_id="test-session",
        audit_log_path=tmp_workspace / "audit.log",
    )


@pytest.fixture
def fake_ctx(fake_config: ServerConfig) -> Context:
    git = GitRunner(token=fake_config.token, timeout=fake_config.git_timeout)
    http = BitbucketHttpClient(
        base_url=fake_config.base_url,
        token=fake_config.token,
        allowed_hosts=fake_config.allowed_hosts,
        timeout=fake_config.http_timeout,
        agent_id=fake_config.agent_id,
    )
    audit = AuditLogger(fake_config)
    return Context(config=fake_config, git=git, http=http, audit=audit)


def run(coro):
    """Helper to run an async coroutine in a sync test."""
    return asyncio.run(coro)


GIT_AVAILABLE = shutil.which("git") is not None
requires_git = pytest.mark.skipif(
    not GIT_AVAILABLE, reason="git binary not available in PATH"
)


# ============================================================
# tool_write_file
# ============================================================


class TestWriteFile:

    def test_creates_new_file(self, fake_ctx, fake_repo):
        result = run(
            tool_write_file(
                fake_ctx,
                {
                    "repo_slug": "myrepo",
                    "file_path": "src/new_module.py",
                    "content": "x = 1\n",
                },
            )
        )
        target = fake_repo / "src" / "new_module.py"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "x = 1\n"
        assert "new_module.py" in result

    def test_overwrites_existing_file(self, fake_ctx, fake_repo):
        run(
            tool_write_file(
                fake_ctx,
                {
                    "repo_slug": "myrepo",
                    "file_path": "src/main.py",
                    "content": "# completely new content\n",
                },
            )
        )
        target = fake_repo / "src" / "main.py"
        assert target.read_text(encoding="utf-8") == "# completely new content\n"

    def test_creates_parent_directories(self, fake_ctx, fake_repo):
        run(
            tool_write_file(
                fake_ctx,
                {
                    "repo_slug": "myrepo",
                    "file_path": "deeply/nested/new/file.txt",
                    "content": "hello\n",
                },
            )
        )
        target = fake_repo / "deeply" / "nested" / "new" / "file.txt"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello\n"

    def test_rejects_content_over_max_bytes(
            self, tmp_workspace, fake_repo
    ):
        # Build a shrunken config just for this test
        small_config = ServerConfig(
            base_url="https://bitbucket.example.com",
            token="fake-token",
            username="tester",
            default_project="TEST",
            workspace_dir=tmp_workspace,
            allowed_hosts=frozenset({"bitbucket.example.com"}),
            git_timeout=10,
            http_timeout=10,
            max_file_bytes=10,
            session_id="test-session",
            audit_log_path=tmp_workspace / "audit.log",
        )
        git = GitRunner(token=small_config.token, timeout=small_config.git_timeout)
        http = BitbucketHttpClient(
            base_url=small_config.base_url,
            token=small_config.token,
            allowed_hosts=small_config.allowed_hosts,
            timeout=small_config.http_timeout,
            agent_id=small_config.agent_id,
        )
        audit = AuditLogger(small_config)
        small_ctx = Context(
            config=small_config, git=git, http=http, audit=audit
        )

        with pytest.raises(ValidationError, match="bytes"):
            run(
                tool_write_file(
                    small_ctx,
                    {
                        "repo_slug": "myrepo",
                        "file_path": "src/big.py",
                        "content": "x" * 100,
                    },
                )
            )

    def test_rejects_path_traversal(self, fake_ctx, fake_repo):
        with pytest.raises(ValidationError):
            run(
                tool_write_file(
                    fake_ctx,
                    {
                        "repo_slug": "myrepo",
                        "file_path": "../escaped.txt",
                        "content": "nope",
                    },
                )
            )

    def test_rejects_absolute_path(self, fake_ctx, fake_repo):
        with pytest.raises(ValidationError):
            run(
                tool_write_file(
                    fake_ctx,
                    {
                        "repo_slug": "myrepo",
                        "file_path": "/etc/passwd",
                        "content": "nope",
                    },
                )
            )

    def test_rejects_nonexistent_repo(self, fake_ctx, tmp_workspace):
        with pytest.raises(ValidationError, match="not cloned"):
            run(
                tool_write_file(
                    fake_ctx,
                    {
                        "repo_slug": "nonexistent",
                        "file_path": "foo.txt",
                        "content": "x",
                    },
                )
            )


# ============================================================
# tool_edit_file
# ============================================================


class TestEditFile:

    def test_replaces_unique_occurrence(self, fake_ctx, fake_repo):
        run(
            tool_edit_file(
                fake_ctx,
                {
                    "repo_slug": "myrepo",
                    "file_path": "src/main.py",
                    "old_str": "return 'world'",
                    "new_str": "return 'universe'",
                },
            )
        )
        content = (fake_repo / "src" / "main.py").read_text(encoding="utf-8")
        assert "return 'universe'" in content
        assert "return 'world'" not in content

    def test_rejects_not_found(self, fake_ctx, fake_repo):
        with pytest.raises(ValidationError, match="not found"):
            run(
                tool_edit_file(
                    fake_ctx,
                    {
                        "repo_slug": "myrepo",
                        "file_path": "src/main.py",
                        "old_str": "this text is not in the file",
                        "new_str": "whatever",
                    },
                )
            )

    def test_rejects_multiple_occurrences(self, fake_ctx, fake_repo):
        (fake_repo / "src" / "main.py").write_text(
            "x = 1\nx = 1\n", encoding="utf-8"
        )
        with pytest.raises(ValidationError, match="2 times"):
            run(
                tool_edit_file(
                    fake_ctx,
                    {
                        "repo_slug": "myrepo",
                        "file_path": "src/main.py",
                        "old_str": "x = 1",
                        "new_str": "x = 2",
                    },
                )
            )

    def test_rejects_empty_old_str(self, fake_ctx, fake_repo):
        with pytest.raises(ValidationError, match="empty"):
            run(
                tool_edit_file(
                    fake_ctx,
                    {
                        "repo_slug": "myrepo",
                        "file_path": "src/main.py",
                        "old_str": "",
                        "new_str": "something",
                    },
                )
            )

    def test_rejects_nonexistent_file(self, fake_ctx, fake_repo):
        with pytest.raises(ValidationError, match="does not exist"):
            run(
                tool_edit_file(
                    fake_ctx,
                    {
                        "repo_slug": "myrepo",
                        "file_path": "src/missing.py",
                        "old_str": "foo",
                        "new_str": "bar",
                    },
                )
            )

    def test_rejects_path_traversal(self, fake_ctx, fake_repo):
        with pytest.raises(ValidationError):
            run(
                tool_edit_file(
                    fake_ctx,
                    {
                        "repo_slug": "myrepo",
                        "file_path": "../escaped.txt",
                        "old_str": "x",
                        "new_str": "y",
                    },
                )
            )

    def test_rejects_nonexistent_repo(self, fake_ctx):
        with pytest.raises(ValidationError, match="not cloned"):
            run(
                tool_edit_file(
                    fake_ctx,
                    {
                        "repo_slug": "nonexistent",
                        "file_path": "x.py",
                        "old_str": "a",
                        "new_str": "b",
                    },
                )
            )


# ============================================================
# tool_apply_patch
# ============================================================


@requires_git
class TestApplyPatch:

    def test_applies_simple_patch(self, fake_ctx, real_git_repo):
        (real_git_repo / "src" / "main.py").write_text(
            "def hello():\n    return 'universe'\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["git", "diff"],
            cwd=real_git_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        patch_content = result.stdout
        subprocess.run(
            ["git", "checkout", "--", "src/main.py"],
            cwd=real_git_repo,
            check=True,
        )
        assert "world" in (
                real_git_repo / "src" / "main.py"
        ).read_text(encoding="utf-8")

        run(
            tool_apply_patch(
                fake_ctx,
                {
                    "repo_slug": "myrepo",
                    "patch_content": patch_content,
                },
            )
        )

        content = (real_git_repo / "src" / "main.py").read_text(encoding="utf-8")
        assert "universe" in content
        assert "world" not in content

    def test_rejects_invalid_patch(self, fake_ctx, real_git_repo):
        with pytest.raises(GitError):
            run(
                tool_apply_patch(
                    fake_ctx,
                    {
                        "repo_slug": "myrepo",
                        "patch_content": "this is not a valid diff",
                    },
                )
            )

    def test_rejects_oversized_patch(self, tmp_workspace, real_git_repo):
        small_config = ServerConfig(
            base_url="https://bitbucket.example.com",
            token="fake-token",
            username="tester",
            default_project="TEST",
            workspace_dir=tmp_workspace,
            allowed_hosts=frozenset({"bitbucket.example.com"}),
            git_timeout=10,
            http_timeout=10,
            max_file_bytes=100,
            session_id="test-session",
            audit_log_path=tmp_workspace / "audit.log",
        )
        git = GitRunner(token=small_config.token, timeout=small_config.git_timeout)
        http = BitbucketHttpClient(
            base_url=small_config.base_url,
            token=small_config.token,
            allowed_hosts=small_config.allowed_hosts,
            timeout=small_config.http_timeout,
            agent_id=small_config.agent_id,
        )
        audit = AuditLogger(small_config)
        small_ctx = Context(
            config=small_config, git=git, http=http, audit=audit
        )

        with pytest.raises(ValidationError, match="max size"):
            run(
                tool_apply_patch(
                    small_ctx,
                    {
                        "repo_slug": "myrepo",
                        "patch_content": "x" * 1000,
                    },
                )
            )

    def test_rejects_nonexistent_repo(self, fake_ctx):
        with pytest.raises(ValidationError, match="not cloned"):
            run(
                tool_apply_patch(
                    fake_ctx,
                    {
                        "repo_slug": "nonexistent",
                        "patch_content": "--- a/foo\n+++ b/foo\n",
                    },
                )
            )
