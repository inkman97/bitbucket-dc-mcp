"""HTTP client for Bitbucket Data Center REST API with SSRF protection."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx


class HttpClientError(RuntimeError):
    """Raised when an HTTP call to Bitbucket fails."""


class SsrfBlocked(HttpClientError):
    """Raised when a URL is rejected by the SSRF allowlist."""


class BitbucketHttpClient:
    """Async HTTP client scoped to a single Bitbucket Data Center instance."""

    def __init__(
        self,
        base_url: str,
        token: str,
        allowed_hosts: frozenset[str],
        timeout: int,
        agent_id: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._allowed_hosts = allowed_hosts
        self._timeout = timeout
        self._agent_id = agent_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"{self._agent_id}/1.0",
        }

    def _check_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise SsrfBlocked("only https URLs are allowed")
        host = (parsed.hostname or "").lower()
        if host not in self._allowed_hosts:
            raise SsrfBlocked(
                f"host '{host}' is not in the configured allowlist"
            )

    async def request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
    ) -> dict:
        url = f"{self._base_url}{path}"
        self._check_url(url)
        async with httpx.AsyncClient(
            timeout=self._timeout,
            verify=True,
            follow_redirects=False,
        ) as client:
            resp = await client.request(
                method, url, headers=self._headers(), json=json_body
            )
            if resp.status_code >= 400:
                raise HttpClientError(
                    f"Bitbucket API returned {resp.status_code}: "
                    f"{resp.text[:500]}"
                )
            return resp.json() if resp.content else {}

    async def get_raw_text(self, path: str, max_bytes: int) -> str:
        url = f"{self._base_url}{path}"
        self._check_url(url)
        async with httpx.AsyncClient(
            timeout=self._timeout,
            verify=True,
            follow_redirects=False,
        ) as client:
            resp = await client.get(url, headers=self._headers())
            if resp.status_code >= 400:
                raise HttpClientError(
                    f"Bitbucket raw endpoint returned {resp.status_code}"
                )
            content = resp.text
            if len(content.encode("utf-8")) > max_bytes:
                truncated = content[:max_bytes]
                return (
                    f"{truncated}\n\n"
                    f"[...truncated at {max_bytes} bytes]"
                )
            return content
