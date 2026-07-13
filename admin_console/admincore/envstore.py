"""管理台設定儲存層：白名單鍵的 .env 讀改寫。

值域治理走 ISO 稽核 Excel 表單（外部流程）；本層只做型別/範圍防呆，
金鑰與連線 URL 類一律不在白名單。
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

SETTINGS: list[dict] = [
    {"key": "CHAT_MODEL_NAME", "type": "str", "label": "聊天模型名稱", "restart": True},
    {"key": "TOP_K", "type": "int", "min": 1, "max": 50, "label": "檢索 Top-K", "restart": True},
    {"key": "RERANK_TOP_N", "type": "int", "min": 1, "max": 100, "label": "重排 Top-N", "restart": True},
    {"key": "REASONING_EFFORT", "type": "enum", "options": ["low", "medium", "high"],
     "label": "推理力度", "restart": True},
    {"key": "REACT_MODE", "type": "enum", "options": ["false", "true"],
     "label": "ReAct 代理模式", "restart": True},
    {"key": "CHUNK_SIZE", "type": "int", "min": 100, "max": 4000,
     "label": "切塊大小", "restart": True, "reindex": True},
    {"key": "MAX_RETRIEVAL_TOKENS", "type": "int", "min": 500, "max": 32000,
     "label": "檢索 token 上限", "restart": True},
    {"key": "RATE_LIMIT_PER_MINUTE", "type": "int", "min": 1, "max": 10000,
     "label": "每分鐘速率上限", "restart": True},
    {"key": "RAG_LOG_LEVEL", "type": "enum", "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
     "label": "日誌等級", "restart": True},
    {"key": "RAG_LOG_VERBOSE", "type": "enum", "options": ["0", "1"],
     "label": "詳細日誌", "restart": True},
    {"key": "LLM_API_BASE", "type": "url", "label": "LLM API Base（推論 gateway）", "restart": True},
    {"key": "EMBED_API_BASE", "type": "url", "label": "Embedding API Base", "restart": True},
    {"key": "EMBED_MODEL_NAME", "type": "str", "label": "嵌入模型名稱", "restart": True},
]
WHITELIST: set[str] = {s["key"] for s in SETTINGS}
_BY_KEY = {s["key"]: s for s in SETTINGS}


class SettingError(ValueError):
    """設定鍵不在白名單，或值不符型別/範圍。"""


def validate(key: str, value: str) -> str:
    spec = _BY_KEY.get(key)
    if spec is None:
        raise SettingError(f"{key} 不在可管理的白名單內")
    raw = str(value)
    if "\n" in raw or "\r" in raw:
        raise SettingError(f"{key} 不可包含換行字元")
    v = raw.strip()
    if spec["type"] == "int":
        if not re.fullmatch(r"-?\d+", v):
            raise SettingError(f"{key} 需為整數，收到 {value!r}")
        n = int(v)
        if not (spec["min"] <= n <= spec["max"]):
            raise SettingError(f"{key} 需介於 {spec['min']}–{spec['max']}，收到 {n}")
        return str(n)
    if spec["type"] == "enum":
        if v not in spec["options"]:
            raise SettingError(f"{key} 只能是 {spec['options']}，收到 {value!r}")
        return v
    if spec["type"] == "url":
        if not re.fullmatch(r"https?://\S+", v):
            raise SettingError(f"{key} 需為 http(s):// 開頭的網址，收到 {value!r}")
        return v
    if not v:
        raise SettingError(f"{key} 不可為空")
    return v


_LINE_RE = re.compile(r"^([A-Z_0-9]+)=(.*)$")


def _write_private_atomic(path: Path, content: str) -> None:
    """Atomically replace *path* without ever creating a world-readable file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp_path.unlink(missing_ok=True)
        raise


class EnvStore:
    def __init__(self, env_path: Path, backup_dir: Path,
                 runtime_env_path: Path | None = None):
        self.env_path = Path(env_path)
        self.backup_dir = Path(backup_dir)
        self.runtime_env_path = Path(runtime_env_path) if runtime_env_path else None

    def _lines(self) -> list[str]:
        return self.env_path.read_text(encoding="utf-8").splitlines()

    def read(self) -> dict[str, str | None]:
        found: dict[str, str | None] = {k: None for k in WHITELIST}
        for line in self._lines():
            m = _LINE_RE.match(line)
            if m and m.group(1) in WHITELIST:
                found[m.group(1)] = m.group(2)
        return found

    def _sync_runtime_env(self) -> None:
        """Write only admin-managed, non-secret keys for rag-api reloads."""
        if self.runtime_env_path is None:
            return
        values = self.read()
        content = "".join(
            f"{key}={values[key]}\n"
            for key in sorted(WHITELIST)
            if values[key] is not None
        )
        _write_private_atomic(self.runtime_env_path, content)

    def apply(self, updates: dict[str, str]) -> list[tuple[str, str | None, str]]:
        current = self.read()
        todo: dict[str, str] = {}
        for k, v in updates.items():
            nv = validate(k, v)
            if current.get(k) != nv:
                todo[k] = nv
        if not todo:
            return []

        original = self.env_path.read_text(encoding="utf-8")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.chmod(0o700)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        _write_private_atomic(self.backup_dir / f"env-{stamp}.bak", original)

        lines = original.splitlines()
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            m = _LINE_RE.match(line)
            if m and m.group(1) in todo:
                out.append(f"{m.group(1)}={todo[m.group(1)]}")
                seen.add(m.group(1))
            else:
                out.append(line)
        for k in sorted(set(todo) - seen):
            out.append(f"{k}={todo[k]}")
        # 原地覆寫（同 inode）：.env 是單檔 bind mount，rename 會讓容器看不到新值
        self.env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        self._sync_runtime_env()
        return [(k, current.get(k), v) for k, v in sorted(todo.items())]
