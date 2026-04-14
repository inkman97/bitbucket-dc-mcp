"""Tests for the git runner wrapper.

These tests use real git against a local bare repo to avoid mocking
subprocess, which is fragile.
"""

import subprocess
from pathlib import Path

import pytest

from bitbucket_dc_mcp.git_runner import GitError, GitRunner


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a tiny git repo with one commit and return its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
    )
    (repo / "file.txt").write_text("hello")
    subprocess.run(
        ["git", "add", "file.txt"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
    )
    return repo


class TestGitRunner:
    def test_runs_simple_command(self, temp_git_repo: Path):
        runner = GitRunner(token="fake", timeout=30)
        output = runner.run(["status", "--porcelain"], cwd=temp_git_repo)
        assert output == ""

    def test_captures_stdout(self, temp_git_repo: Path):
        runner = GitRunner(token="fake", timeout=30)
        output = runner.run(
            ["log", "--oneline"], cwd=temp_git_repo
        )
        assert "init" in output

    def test_raises_on_failure(self, temp_git_repo: Path):
        runner = GitRunner(token="fake", timeout=30)
        with pytest.raises(GitError, match="git failed"):
            runner.run(
                ["checkout", "nonexistent-branch"], cwd=temp_git_repo
            )

    def test_redacts_token_in_error_output(self, temp_git_repo: Path):
        runner = GitRunner(token="secret-token-xyz", timeout=30)
        with pytest.raises(GitError) as exc_info:
            runner.run(
                ["checkout", "nonexistent-branch-secret-token-xyz"],
                cwd=temp_git_repo,
            )
        assert "secret-token-xyz" not in str(exc_info.value)

    def test_auth_header_args_contain_token(self):
        runner = GitRunner(token="my-token", timeout=30)
        args = runner.auth_header_args()
        joined = " ".join(args)
        assert "Authorization: Bearer my-token" in joined
        # Also verify the canonical -c flag structure
        assert "-c" in args

    def test_auth_header_args_default_disables_lfs(self):
        runner = GitRunner(token="my-token", timeout=30)
        args = runner.auth_header_args()
        joined = " ".join(args)
        assert "filter.lfs.required=false" in joined
        assert "filter.lfs.smudge=cat" in joined
        assert "filter.lfs.clean=cat" in joined
        assert "filter.lfs.process=" in joined
        assert runner.lfs_mode == "disabled"

    def test_auth_header_args_enabled_preserves_lfs(self):
        runner = GitRunner(token="my-token", timeout=30, lfs_mode="enabled")
        args = runner.auth_header_args()
        joined = " ".join(args)
        assert "filter.lfs" not in joined
        assert "credential.helper=" in joined
        assert "Authorization: Bearer my-token" in joined
        assert runner.lfs_mode == "enabled"

    def test_auth_header_args_auto_resolves_on_init(self):
        runner = GitRunner(token="my-token", timeout=30, lfs_mode="auto")
        # auto resolves to one of the concrete modes depending on
        # whether git-lfs is in PATH at test time.
        assert runner.lfs_mode in ("enabled", "disabled")

    def test_invalid_lfs_mode_raises_value_error(self):
        with pytest.raises(ValueError, match="lfs_mode"):
            GitRunner(token="t", timeout=30, lfs_mode="bogus")

    def test_credential_helper_always_disabled(self):
        # Regardless of lfs_mode, credential.helper= must always be
        # in the auth_header_args to prevent hangs on the Windows
        # credential manager.
        for mode in ("disabled", "enabled"):
            runner = GitRunner(
                token="my-token", timeout=30, lfs_mode=mode
            )
            args = runner.auth_header_args()
            joined = " ".join(args)
            assert "credential.helper=" in joined
