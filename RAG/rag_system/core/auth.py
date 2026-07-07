"""
API Key Authentication — ISO 42001 A.3/A.9

Three modes:
  1. Key mode (API_KEYS set): Bearer token required; 401 on failure.
  2. Intranet mode (API_KEYS empty + ALLOW_INTRANET_MODE=true): all requests
     accepted; client IP logged as identity for audit trail.
  3. Misconfiguration (API_KEYS empty, ALLOW_INTRANET_MODE not true): 503
     to fail closed — never silently expose unprotected endpoints.

X-Forwarded-For is only trusted when the immediate TCP peer is listed in
TRUSTED_PROXIES (env var, default: 127.0.0.1). Clients that can reach the
app directly cannot inject arbitrary source IPs into audit trails or evade
per-key rate limits by rotating fake addresses.
"""
import os
from typing import Optional

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)

_VALID_KEYS: Optional[set] = None
_ALLOW_INTRANET: Optional[bool] = None
_TRUSTED_PROXIES: Optional[set] = None


def _get_valid_keys() -> set:
    global _VALID_KEYS
    if _VALID_KEYS is None:
        raw = os.environ.get("API_KEYS", "")
        _VALID_KEYS = {k.strip() for k in raw.split(",") if k.strip()}
    return _VALID_KEYS


def _intranet_mode_allowed() -> bool:
    global _ALLOW_INTRANET
    if _ALLOW_INTRANET is None:
        _ALLOW_INTRANET = os.environ.get("ALLOW_INTRANET_MODE", "").lower() in ("true", "1", "yes")
    return _ALLOW_INTRANET


def _get_trusted_proxies() -> set:
    global _TRUSTED_PROXIES
    if _TRUSTED_PROXIES is None:
        raw = os.environ.get("TRUSTED_PROXIES", "127.0.0.1")
        _TRUSTED_PROXIES = {ip.strip() for ip in raw.split(",") if ip.strip()}
    return _TRUSTED_PROXIES


def get_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> str:
    """
    FastAPI dependency: validate Bearer token OR allow intranet access.

    Key mode (API_KEYS set):
      - Require valid Bearer token; raise 401 otherwise.

    Intranet mode (API_KEYS empty + ALLOW_INTRANET_MODE=true):
      - Accept all requests regardless of Authorization header.
      - Return "intranet:<client_ip>" so audit logs show verified source IP.
      - X-Forwarded-For only trusted from TRUSTED_PROXIES peers.

    Misconfiguration (API_KEYS empty, ALLOW_INTRANET_MODE not set):
      - Raise 503 — fail closed to prevent silent endpoint exposure.
    """
    valid_keys = _get_valid_keys()

    if not valid_keys:
        if not _intranet_mode_allowed():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Server misconfiguration: set API_KEYS or ALLOW_INTRANET_MODE=true",
            )
        # Intranet mode explicitly enabled — log verified client IP for audit trail
        client_ip = _get_client_ip(request)
        return f"intranet:{client_ip}"

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    if token not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


def key_prefix(key: str) -> str:
    """Return first 24 chars for safe logging (IP identity or key prefix)."""
    return key[:24]


def get_client_ip(request: Request) -> str:
    """Public wrapper — extract the real client IP (spoof-guarded).

    Used by api.py to stamp every audit event with the originating IP
    (ISO 27001 A.8.15 network address). Honours X-Forwarded-For only from
    TRUSTED_PROXIES peers, so the logged IP can't be forged by direct
    clients.
    """
    return _get_client_ip(request)


def _get_client_ip(request: Request) -> str:
    """
    Extract real client IP, guarding against header spoofing.

    X-Forwarded-For is only honoured when the immediate TCP peer is a known
    trusted proxy (TRUSTED_PROXIES env var, default: 127.0.0.1 for nginx
    running on the same host). Direct clients cannot set their own audit
    identity by forging the header.
    """
    peer_ip = request.client.host if request.client else None
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for and peer_ip in _get_trusted_proxies():
        return forwarded_for.split(",")[0].strip()
    if peer_ip:
        return peer_ip
    return "unknown"
