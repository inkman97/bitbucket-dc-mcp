"""Tests for configuration loading from environment variables."""

import os
from pathlib import Path

import pytest

from bitbucket_dc_mcp.config import ConfigError, load_config


def _clean_env(monkeypatch):
    """Remove any BITBUCKET_* env var that might leak from the host."""
    for key in list(os.environ.keys()):
        if key.startswith("BITBUCKET_"):
            monkeypatch.delenv(key, raising=False)


class TestLoadConfig:
    def test_minimal_valid(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "https://bitbucket.example.com")
        monkeypatch.setenv("BITBUCKET_TOKEN", "fake-token-xyz")
        monkeypatch.setenv("BITBUCKET_USERNAME", "alice")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path / "ws"))

        config = load_config()
        assert config.base_url == "https://bitbucket.example.com"
        assert config.token == "fake-token-xyz"
        assert config.username == "alice"
        assert config.workspace_dir == (tmp_path / "ws").resolve()
        assert "bitbucket.example.com" in config.allowed_hosts

    def test_requires_base_url(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_TOKEN", "t")
        monkeypatch.setenv("BITBUCKET_USERNAME", "u")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        with pytest.raises(ConfigError, match="BITBUCKET_BASE_URL"):
            load_config()

    def test_requires_token(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "https://x.example.com")
        monkeypatch.setenv("BITBUCKET_USERNAME", "u")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        with pytest.raises(ConfigError, match="BITBUCKET_TOKEN"):
            load_config()

    def test_requires_username(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "https://x.example.com")
        monkeypatch.setenv("BITBUCKET_TOKEN", "t")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        with pytest.raises(ConfigError, match="BITBUCKET_USERNAME"):
            load_config()

    def test_rejects_http_base_url(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "http://insecure.example.com")
        monkeypatch.setenv("BITBUCKET_TOKEN", "t")
        monkeypatch.setenv("BITBUCKET_USERNAME", "u")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        with pytest.raises(ConfigError, match="https"):
            load_config()

    def test_allowed_hosts_overrides(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "https://foo.example.com")
        monkeypatch.setenv("BITBUCKET_TOKEN", "t")
        monkeypatch.setenv("BITBUCKET_USERNAME", "u")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        monkeypatch.setenv(
            "BITBUCKET_ALLOWED_HOSTS", "foo.example.com,bar.example.com"
        )
        config = load_config()
        assert "foo.example.com" in config.allowed_hosts
        assert "bar.example.com" in config.allowed_hosts

    def test_base_url_host_must_be_in_allowed(
        self, monkeypatch, tmp_path
    ):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "https://attacker.com")
        monkeypatch.setenv("BITBUCKET_TOKEN", "t")
        monkeypatch.setenv("BITBUCKET_USERNAME", "u")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        monkeypatch.setenv(
            "BITBUCKET_ALLOWED_HOSTS", "bitbucket.example.com"
        )
        with pytest.raises(ConfigError, match="not in allowed"):
            load_config()

    def test_invalid_timeout_rejected(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "https://x.example.com")
        monkeypatch.setenv("BITBUCKET_TOKEN", "t")
        monkeypatch.setenv("BITBUCKET_USERNAME", "u")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("BITBUCKET_GIT_TIMEOUT", "not-a-number")
        with pytest.raises(ConfigError, match="integer"):
            load_config()

    def test_negative_timeout_rejected(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "https://x.example.com")
        monkeypatch.setenv("BITBUCKET_TOKEN", "t")
        monkeypatch.setenv("BITBUCKET_USERNAME", "u")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("BITBUCKET_HTTP_TIMEOUT", "-5")
        with pytest.raises(ConfigError, match="positive"):
            load_config()

    def test_session_id_auto_generated(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "https://x.example.com")
        monkeypatch.setenv("BITBUCKET_TOKEN", "t")
        monkeypatch.setenv("BITBUCKET_USERNAME", "u")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        config = load_config()
        assert config.session_id
        assert len(config.session_id) >= 8

    def test_session_id_from_env(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch)
        monkeypatch.setenv("BITBUCKET_BASE_URL", "https://x.example.com")
        monkeypatch.setenv("BITBUCKET_TOKEN", "t")
        monkeypatch.setenv("BITBUCKET_USERNAME", "u")
        monkeypatch.setenv("BITBUCKET_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("BITBUCKET_SESSION_ID", "custom-session-42")
        config = load_config()
        assert config.session_id == "custom-session-42"
