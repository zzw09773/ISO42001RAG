"""
Sliding-window Rate Limiter — ISO 42001 A.9

Per-key rate limiting using minute-bucket counters (no external dependencies).
Each API key is allowed RATE_LIMIT_PER_MINUTE requests per minute (default 60).
"""
import os
import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, status

# Read limit once at import time; can be overridden in tests via env
_LIMIT_PER_MINUTE: int = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))

# {f"{key}:{minute_bucket}": count}
_counters: dict = defaultdict(int)
_lock = Lock()


def _minute_bucket() -> int:
    """Return current minute as an integer (Unix time // 60)."""
    return int(time.monotonic()) // 60


def check_rate_limit(api_key: str) -> None:
    """
    Increment counter for this key in the current minute bucket.
    Raises HTTP 429 if the limit is exceeded.

    Call after authentication succeeds:
        check_rate_limit(api_key)
    """
    bucket_key = f"{api_key}:{_minute_bucket()}"
    with _lock:
        _counters[bucket_key] += 1
        count = _counters[bucket_key]

    if count > _LIMIT_PER_MINUTE:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {_LIMIT_PER_MINUTE} requests/minute",
            headers={"Retry-After": "60"},
        )
