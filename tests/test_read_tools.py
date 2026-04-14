"""Tests for read-side tools: all tools that only call ctx.http."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bitbucket_dc_mcp.config import ServerConfig
from bitbucket_dc_mcp.git_runner import GitRunner
from bitbucket_dc_mcp.logging_setup import AuditLogger
from bitbucket_dc_mcp.server import (
    Context,
    tool_get_file_content,
    tool_get_pull_request,
    tool_get_pull_request_comments,
    tool_get_pull_request_diff,
    tool_get_repo_info,
    tool_list_branches,
    tool_list_files,
    tool_list_pull_requests,
)
from bitbucket_dc_mcp.validation import ValidationError


# ============================================================
# FIXTURES AND FAKE HTTP CLIENT
# ============================================================


class FakeHttpClient:
    """In-memory replacement for BitbucketHttpClient.

    Tests configure `responses` as a dict mapping (method, path) -> dict
    or `raw_responses` as a dict mapping path -> str for get_raw_text.
    Match is on the path **before** any '?' query string, and must be
    exact (no prefix match) to avoid accidentally matching a shorter
    path that is a prefix of a longer one.
    """

    def __init__(
            self,
            responses: dict[tuple[str, str], Any] | None = None,
            raw_responses: dict[str, str] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._raw_responses = raw_responses or {}
        self.calls: list[tuple[str, str, Any]] = []

    @staticmethod
    def _strip_query(path: str) -> str:
        idx = path.find("?")
        return path if idx < 0 else path[:idx]

    def _check_url(self, url: str) -> None:
        pass

    async def request(
            self, method: str, path: str, json_body: dict | None = None
    ) -> dict:
        self.calls.append((method, path, json_body))
        path_no_query = self._strip_query(path)
        key = (method, path_no_query)
        if key in self._responses:
            return self._responses[key]
        raise RuntimeError(
            f"Unexpected HTTP call: {method} {path}. "
            f"Stripped path: {path_no_query}. "
            f"Configured: {list(self._responses.keys())}"
        )

    async def get_raw_text(self, path: str, max_bytes: int) -> str:
        self.calls.append(("GET_RAW", path, None))
        path_no_query = self._strip_query(path)
        if path_no_query in self._raw_responses:
            return self._raw_responses[path_no_query]
        raise RuntimeError(
            f"Unexpected raw HTTP call: {path}. "
            f"Stripped path: {path_no_query}. "
            f"Configured: {list(self._raw_responses.keys())}"
        )


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


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


def build_ctx(fake_config: ServerConfig, http: FakeHttpClient) -> Context:
    git = GitRunner(token=fake_config.token, timeout=fake_config.git_timeout)
    audit = AuditLogger(fake_config)
    return Context(config=fake_config, git=git, http=http, audit=audit)


def run(coro):
    return asyncio.run(coro)


# ============================================================
# tool_get_repo_info
# ============================================================


class TestGetRepoInfo:

    def test_returns_formatted_info(self, fake_config):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo"): {
                    "name": "myrepo",
                    "slug": "myrepo",
                    "project": {"key": "TEST"},
                    "description": "A test repo",
                },
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo/default-branch"): {
                    "id": "refs/heads/main",
                    "displayId": "main",
                    "type": "BRANCH",
                },
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_get_repo_info(
                ctx, {"repo_slug": "myrepo"}
            )
        )
        assert "myrepo" in result
        assert "main" in result
        assert "A test repo" in result

    def test_falls_back_when_default_branch_lookup_fails(
            self, fake_config
    ):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo"): {
                    "name": "myrepo",
                    "slug": "myrepo",
                    "project": {"key": "TEST"},
                    "description": "",
                    "defaultBranch": "master",
                },
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_get_repo_info(
                ctx, {"repo_slug": "myrepo"}
            )
        )
        # The dedicated default-branch call is unconfigured and raises,
        # but the tool's try/except falls back to the defaultBranch
        # field from the base response.
        assert "master" in result


# ============================================================
# tool_list_branches
# ============================================================


class TestListBranches:

    def test_lists_branches_and_marks_default(self, fake_config):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo/branches"): {
                    "values": [
                        {"displayId": "main", "isDefault": True},
                        {"displayId": "develop", "isDefault": False},
                        {"displayId": "feature/foo", "isDefault": False},
                    ]
                }
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_list_branches(ctx, {"repo_slug": "myrepo"})
        )
        assert "main" in result
        assert "(default)" in result
        assert "develop" in result
        assert "feature/foo" in result
        assert "3 total" in result


# ============================================================
# tool_list_files
# ============================================================


class TestListFiles:

    def test_lists_files_single_page(self, fake_config):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo/files"): {
                    "values": ["pom.xml", "README.md", "src/main.py"],
                    "isLastPage": True,
                }
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_list_files(ctx, {"repo_slug": "myrepo"})
        )
        assert "pom.xml" in result
        assert "README.md" in result
        assert "src/main.py" in result
        assert "3 entries" in result


# ============================================================
# tool_get_file_content
# ============================================================


class TestGetFileContent:

    def test_reassembles_single_page(self, fake_config):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo/browse/src/main.py"): {
                    "lines": [
                        {"text": "def hello():"},
                        {"text": "    return 'world'"},
                    ],
                    "isLastPage": True,
                }
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_get_file_content(
                ctx,
                {"repo_slug": "myrepo", "file_path": "src/main.py"},
            )
        )
        assert "def hello():" in result
        assert "return 'world'" in result


# ============================================================
# tool_list_pull_requests
# ============================================================


class TestListPullRequests:

    def test_lists_open_prs_by_default(self, fake_config):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo/pull-requests"): {
                    "values": [
                        {
                            "id": 42,
                            "title": "Fix login bug",
                            "state": "OPEN",
                            "author": {"user": {"name": "alice"}},
                            "fromRef": {"displayId": "feature/login"},
                            "toRef": {"displayId": "main"},
                        }
                    ]
                }
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_list_pull_requests(ctx, {"repo_slug": "myrepo"})
        )
        assert "#42" in result
        assert "Fix login bug" in result
        assert "alice" in result
        assert "feature/login" in result

    def test_empty_list_returns_message(self, fake_config):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo/pull-requests"): {
                    "values": []
                }
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_list_pull_requests(ctx, {"repo_slug": "myrepo"})
        )
        assert "No pull requests" in result

    def test_rejects_invalid_state(self, fake_config):
        http = FakeHttpClient()
        ctx = build_ctx(fake_config, http)
        with pytest.raises(ValidationError):
            run(
                tool_list_pull_requests(
                    ctx,
                    {"repo_slug": "myrepo", "state": "INVALID"},
                )
            )


# ============================================================
# tool_get_pull_request
# ============================================================


class TestGetPullRequest:

    def test_returns_details(self, fake_config):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo/pull-requests/42"): {
                    "title": "Fix login",
                    "description": "Resolves LOGIN-1.",
                    "state": "OPEN",
                    "author": {"user": {"name": "alice"}},
                    "fromRef": {"displayId": "feature/login"},
                    "toRef": {"displayId": "main"},
                    "reviewers": [
                        {"user": {"name": "bob"}, "approved": True},
                        {"user": {"name": "carol"}, "approved": False},
                    ],
                    "links": {
                        "self": [
                            {"href": "https://bitbucket.example.com/pr/42"}
                        ]
                    },
                }
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_get_pull_request(
                ctx,
                {"repo_slug": "myrepo", "pull_request_id": 42},
            )
        )
        assert "Fix login" in result
        assert "alice" in result
        assert "bob" in result
        assert "carol" in result
        assert "1/2" in result  # approvals
        assert "https://bitbucket.example.com/pr/42" in result


# ============================================================
# tool_get_pull_request_diff
# ============================================================


class TestGetPullRequestDiff:

    def test_returns_raw_diff(self, fake_config):
        diff_text = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-x = 1\n"
            "+x = 2\n"
        )
        http = FakeHttpClient(
            raw_responses={
                "/rest/api/1.0/projects/TEST/repos/myrepo/pull-requests/42/diff":
                    diff_text
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_get_pull_request_diff(
                ctx,
                {"repo_slug": "myrepo", "pull_request_id": 42},
            )
        )
        assert "x = 1" in result
        assert "x = 2" in result
        assert "#42" in result


# ============================================================
# tool_get_pull_request_comments
# ============================================================


class TestGetPullRequestComments:

    def test_extracts_comments_from_activities(self, fake_config):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo/pull-requests/42/activities"): {
                    "values": [
                        {
                            "action": "COMMENTED",
                            "comment": {
                                "id": 1,
                                "author": {"name": "alice"},
                                "text": "Looks good",
                            },
                            "createdDate": 1700000000000,
                        },
                        {
                            "action": "APPROVED",
                            # Not a comment, should be ignored
                        },
                        {
                            "action": "COMMENTED",
                            "comment": {
                                "id": 2,
                                "author": {"name": "bob"},
                                "text": "One nitpick",
                            },
                            "createdDate": 1700000001000,
                        },
                    ],
                    "isLastPage": True,
                }
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_get_pull_request_comments(
                ctx,
                {"repo_slug": "myrepo", "pull_request_id": 42},
            )
        )
        assert "Looks good" in result
        assert "One nitpick" in result
        assert "alice" in result
        assert "bob" in result
        assert "2 total" in result

    def test_no_comments_returns_message(self, fake_config):
        http = FakeHttpClient(
            responses={
                ("GET", "/rest/api/1.0/projects/TEST/repos/myrepo/pull-requests/42/activities"): {
                    "values": [],
                    "isLastPage": True,
                }
            }
        )
        ctx = build_ctx(fake_config, http)
        result = run(
            tool_get_pull_request_comments(
                ctx,
                {"repo_slug": "myrepo", "pull_request_id": 42},
            )
        )
        assert "No comments" in result
