"""challenge nonce 記憶體存放：取代 ANILA 的 challenge JWT（單機管理台不需 JWT）。"""
from __future__ import annotations

import secrets
import threading
import time


class ChallengeStore:
    def __init__(self, ttl_sec: int = 120):
        self._ttl = ttl_sec
        self._lock = threading.Lock()
        self._items: dict[str, tuple[str, float]] = {}

    def issue(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(24)
        nonce = secrets.token_hex(16)
        with self._lock:
            now = time.monotonic()
            self._items = {k: v for k, v in self._items.items() if v[1] > now}
            self._items[token] = (nonce, now + self._ttl)
        return token, nonce

    def consume(self, token: str) -> str | None:
        """一次性取出（反 replay）；過期或不存在回 None。"""
        with self._lock:
            item = self._items.pop(token, None)
        if item is None or item[1] <= time.monotonic():
            return None
        return item[0]
