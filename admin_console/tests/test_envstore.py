from pathlib import Path

import pytest

from admincore.envstore import EnvStore, SettingError, SETTINGS, WHITELIST, validate

SAMPLE = """# 由舊容器 env 重建
CHAT_MODEL_NAME=gpt-oss-20b
TOP_K=5
LLM_API_KEY=secret-do-not-touch
# 註解要活著
RAG_LOG_LEVEL=INFO
"""


@pytest.fixture()
def store(tmp_path):
    env = tmp_path / ".env"
    env.write_text(SAMPLE, encoding="utf-8")
    return EnvStore(env, tmp_path / "backups"), env


def test_whitelist_has_exactly_thirteen_keys():
    assert len(SETTINGS) == 13 and len(WHITELIST) == 13
    for secret in ("LLM_API_KEY", "EMBED_API_KEY", "API_KEYS"):
        assert secret not in WHITELIST
    for added in ("LLM_API_BASE", "EMBED_API_BASE", "EMBED_MODEL_NAME"):
        assert added in WHITELIST


def test_validate_url_type():
    assert validate("LLM_API_BASE", " http://gw:7000/v1 ") == "http://gw:7000/v1"
    assert validate("EMBED_API_BASE", "https://embed:7100/v1") == "https://embed:7100/v1"
    with pytest.raises(SettingError):
        validate("LLM_API_BASE", "gw:7000/v1")     # 無 scheme
    with pytest.raises(SettingError):
        validate("EMBED_API_BASE", "")             # 空值


def test_read_returns_only_whitelist(store):
    s, _ = store
    d = s.read()
    assert d["TOP_K"] == "5" and d["CHAT_MODEL_NAME"] == "gpt-oss-20b"
    assert "LLM_API_KEY" not in d
    assert d["RERANK_TOP_N"] is None  # .env 沒寫的白名單鍵回 None


def test_apply_preserves_comments_and_foreign_keys(store):
    s, env = store
    changes = s.apply({"TOP_K": "8", "RERANK_TOP_N": "10"})
    text = env.read_text(encoding="utf-8")
    assert ("TOP_K", "5", "8") in changes
    assert ("RERANK_TOP_N", None, "10") in changes
    assert "TOP_K=8" in text and "RERANK_TOP_N=10" in text
    assert "LLM_API_KEY=secret-do-not-touch" in text
    assert "# 註解要活著" in text


def test_apply_writes_in_place_same_inode(store):
    s, env = store
    ino = env.stat().st_ino
    s.apply({"TOP_K": "9"})
    assert env.stat().st_ino == ino  # 單檔 bind mount：換 inode 容器就看不到


def test_apply_creates_timestamped_backup(store):
    s, env = store
    s.apply({"TOP_K": "7"})
    baks = list((env.parent / "backups").glob("env-*.bak"))
    assert len(baks) == 1 and "TOP_K=5" in baks[0].read_text(encoding="utf-8")


def test_apply_noop_writes_nothing(store):
    s, env = store
    assert s.apply({"TOP_K": "5"}) == []
    assert not list((env.parent / "backups").glob("*.bak"))


def test_validate_rejects_bad_values():
    with pytest.raises(SettingError):
        validate("TOP_K", "abc")
    with pytest.raises(SettingError):
        validate("TOP_K", "0")          # min 1
    with pytest.raises(SettingError):
        validate("REASONING_EFFORT", "max")   # 不在 options
    with pytest.raises(SettingError):
        validate("LLM_API_KEY", "x")    # 非白名單
    assert validate("REACT_MODE", "true") == "true"
    assert validate("TOP_K", " 8 ") == "8"
