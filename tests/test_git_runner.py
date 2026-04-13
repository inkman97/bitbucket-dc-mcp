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

    def test_redacts_token_in_error_output(
        self, temp_git_repo: Path
    ):
        # If an error message somehow contains the token, it must not leak
        runner = GitRunner(token="secret-token-xyz", timeout=30)
        # Force a failure where git might echo back something
        try:
            runner.run(
                ["checkout", "nonexistent-branch-secret-token-xyz"],
                cwd=temp_git_repo,
            )
        except GitError as e:
            # The error must not contain the literal token
            assert "secret-token-xyz" not in str(e)

    def test_auth_header_args_contain_token(self):
        runner = GitRunner(token="my-token", timeout=30)
        args = runner.auth_header_args()
        assert args[0] == "-c"
        assert "Authorization: Bearer my-token" in args[1]
