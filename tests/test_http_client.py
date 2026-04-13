"""Tests for HTTP client SSRF protection.

These tests verify the URL allowlist is enforced. No real network calls
are made — we only check that rejection happens before the request.
"""

import pytest

from bitbucket_dc_mcp.http_client import BitbucketHttpClient, SsrfBlocked


def _make_client(allowed: set[str]) -> BitbucketHttpClient:
    return BitbucketHttpClient(
        base_url="https://bitbucket.example.com",
        token="fake-token",
        allowed_hosts=frozenset(allowed),
        timeout=30,
        agent_id="test",
    )


class TestCheckUrl:
    def test_accepts_allowed_host(self):
        client = _make_client({"bitbucket.example.com"})
        client._check_url("https://bitbucket.example.com/rest/api/1.0/whatever")

    def test_rejects_non_https(self):
        client = _make_client({"bitbucket.example.com"})
        with pytest.raises(SsrfBlocked, match="https"):
            client._check_url("http://bitbucket.example.com/path")

    def test_rejects_non_allowed_host(self):
        client = _make_client({"bitbucket.example.com"})
        with pytest.raises(SsrfBlocked, match="not in"):
            client._check_url("https://attacker.com/path")

    def test_rejects_metadata_service(self):
        client = _make_client({"bitbucket.example.com"})
        with pytest.raises(SsrfBlocked):
            client._check_url("https://169.254.169.254/latest/meta-data/")

    def test_rejects_localhost(self):
        client = _make_client({"bitbucket.example.com"})
        with pytest.raises(SsrfBlocked):
            client._check_url("https://localhost/admin")

    def test_case_insensitive_host_match(self):
        client = _make_client({"bitbucket.example.com"})
        # Same host in different case should be accepted
        client._check_url("https://BITBUCKET.EXAMPLE.COM/path")
