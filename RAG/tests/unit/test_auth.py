"""
Auth & Rate Limiter Unit Tests — ISO 42001 A.3/A.9

Tests authentication and rate limiting logic without spinning up FastAPI.
"""
import os
import time
import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials


def _mock_request(ip: str = "192.168.1.100") -> MagicMock:
    req = MagicMock(spec=Request)
    req.headers = {}
    req.client = MagicMock()
    req.client.host = ip
    return req


def _reset_auth_cache():
    """Reset all module-level caches between tests."""
    import rag_system.core.auth as auth_mod
    auth_mod._VALID_KEYS = None
    auth_mod._ALLOW_INTRANET = None
    auth_mod._TRUSTED_PROXIES = None


class TestGetApiKey:
    """Test API key validation logic."""

    def _make_credentials(self, token: str) -> HTTPAuthorizationCredentials:
        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = token
        return creds

    def test_valid_key_returns_key(self, monkeypatch):
        monkeypatch.setenv("API_KEYS", "secret-key-1,secret-key-2")
        _reset_auth_cache()

        from rag_system.core.auth import get_api_key
        result = get_api_key(_mock_request(), self._make_credentials("secret-key-1"))
        assert result == "secret-key-1"

    def test_invalid_key_raises_401(self, monkeypatch):
        monkeypatch.setenv("API_KEYS", "secret-key-1")
        _reset_auth_cache()

        from rag_system.core.auth import get_api_key
        with pytest.raises(HTTPException) as exc:
            get_api_key(_mock_request(), self._make_credentials("wrong-key"))
        assert exc.value.status_code == 401

    def test_missing_credentials_raises_401(self, monkeypatch):
        monkeypatch.setenv("API_KEYS", "secret-key-1")
        _reset_auth_cache()

        from rag_system.core.auth import get_api_key
        with pytest.raises(HTTPException) as exc:
            get_api_key(_mock_request(), None)
        assert exc.value.status_code == 401

    def test_missing_api_keys_without_intranet_mode_raises_503(self, monkeypatch):
        """Fail closed: empty API_KEYS + no ALLOW_INTRANET_MODE must raise 503."""
        monkeypatch.setenv("API_KEYS", "")
        monkeypatch.setenv("ALLOW_INTRANET_MODE", "false")
        _reset_auth_cache()

        from rag_system.core.auth import get_api_key
        with pytest.raises(HTTPException) as exc:
            get_api_key(_mock_request(), None)
        assert exc.value.status_code == 503

    def test_intranet_mode_returns_client_ip(self, monkeypatch):
        """When API_KEYS is empty + ALLOW_INTRANET_MODE=true: client IP used as identity."""
        monkeypatch.setenv("API_KEYS", "")
        monkeypatch.setenv("ALLOW_INTRANET_MODE", "true")
        _reset_auth_cache()

        from rag_system.core.auth import get_api_key
        result = get_api_key(_mock_request("10.0.0.5"), self._make_credentials("anything"))
        assert result == "intranet:10.0.0.5"

    def test_intranet_mode_no_credentials_still_passes(self, monkeypatch):
        """Intranet mode: no Bearer token required."""
        monkeypatch.setenv("API_KEYS", "")
        monkeypatch.setenv("ALLOW_INTRANET_MODE", "true")
        _reset_auth_cache()

        from rag_system.core.auth import get_api_key
        result = get_api_key(_mock_request("172.16.0.1"), None)
        assert result == "intranet:172.16.0.1"

    def test_forwarded_for_trusted_when_peer_is_proxy(self, monkeypatch):
        """X-Forwarded-For honoured only when peer IP is a trusted proxy."""
        monkeypatch.setenv("API_KEYS", "")
        monkeypatch.setenv("ALLOW_INTRANET_MODE", "true")
        monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1")
        _reset_auth_cache()

        req = MagicMock(spec=Request)
        req.headers = {"X-Forwarded-For": "10.1.2.3, 172.16.0.1"}
        req.client = MagicMock()
        req.client.host = "127.0.0.1"  # trusted nginx proxy

        from rag_system.core.auth import get_api_key
        result = get_api_key(req, None)
        assert result == "intranet:10.1.2.3"

    def test_forwarded_for_ignored_when_peer_is_not_proxy(self, monkeypatch):
        """X-Forwarded-For must be ignored from untrusted peers."""
        monkeypatch.setenv("API_KEYS", "")
        monkeypatch.setenv("ALLOW_INTRANET_MODE", "true")
        monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1")
        _reset_auth_cache()

        req = MagicMock(spec=Request)
        req.headers = {"X-Forwarded-For": "1.2.3.4"}
        req.client = MagicMock()
        req.client.host = "10.5.5.5"  # not a trusted proxy

        from rag_system.core.auth import get_api_key
        result = get_api_key(req, None)
        # Must use real peer IP, not the spoofed header
        assert result == "intranet:10.5.5.5"

    def test_forwarded_for_trusted_when_peer_matches_cidr(self, monkeypatch):
        """An explicitly configured CIDR must match proxy peers."""
        monkeypatch.setenv("API_KEYS", "")
        monkeypatch.setenv("ALLOW_INTRANET_MODE", "true")
        monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,172.16.0.0/12")
        _reset_auth_cache()

        req = MagicMock(spec=Request)
        req.headers = {"X-Forwarded-For": "10.20.30.40"}
        req.client = MagicMock()
        req.client.host = "172.19.0.8"

        from rag_system.core.auth import get_api_key
        assert get_api_key(req, None) == "intranet:10.20.30.40"

    def test_key_prefix_truncates(self):
        from rag_system.core.auth import key_prefix
        assert len(key_prefix("intranet:192.168.1.100")) <= 24
        assert key_prefix("short") == "short"


class TestRateLimiter:
    """Test rate limiting logic."""

    def setup_method(self):
        """Reset rate limiter state between tests."""
        import rag_system.core.rate_limiter as rl_mod
        rl_mod._counters.clear()

    def test_under_limit_passes(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
        import rag_system.core.rate_limiter as rl_mod
        rl_mod._LIMIT_PER_MINUTE = 5

        from rag_system.core.rate_limiter import check_rate_limit
        for _ in range(5):
            check_rate_limit("test-key")  # Should not raise

    def test_over_limit_raises_429(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
        import rag_system.core.rate_limiter as rl_mod
        rl_mod._LIMIT_PER_MINUTE = 3

        from rag_system.core.rate_limiter import check_rate_limit
        for _ in range(3):
            check_rate_limit("test-key")

        with pytest.raises(HTTPException) as exc:
            check_rate_limit("test-key")
        assert exc.value.status_code == 429

    def test_different_keys_have_separate_limits(self, monkeypatch):
        import rag_system.core.rate_limiter as rl_mod
        rl_mod._LIMIT_PER_MINUTE = 2

        from rag_system.core.rate_limiter import check_rate_limit
        check_rate_limit("key-a")
        check_rate_limit("key-a")
        check_rate_limit("key-b")  # key-b is independent — should not raise
