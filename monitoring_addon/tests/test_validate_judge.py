"""P5-2 — judge 驗證工具的方向判定邏輯（合成資料，免 LLM）。"""
import importlib.util as _u
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_spec = _u.spec_from_file_location(
    "vj", str(Path(__file__).resolve().parent.parent / "scripts" / "validate_judge.py"))
vj = _u.module_from_spec(_spec)
_spec.loader.exec_module(vj)


def test_direction_ok_bands():
    assert vj._direction_ok("high", {"median": 1.0, "abstentions": 0}) is True
    assert vj._direction_ok("high", {"median": 0.5, "abstentions": 0}) is False
    assert vj._direction_ok("low", {"median": 0.0, "abstentions": 0}) is True
    assert vj._direction_ok("low", {"median": 0.9, "abstentions": 0}) is False
    assert vj._direction_ok("mid", {"median": 0.5, "abstentions": 0}) is True


def test_direction_ok_abstain_requires_flag():
    assert vj._direction_ok("abstain", {"median": None, "abstentions": 2}) is True
    assert vj._direction_ok("abstain", {"median": 1.0, "abstentions": 0}) is False


def test_cases_cover_all_bands():
    bands = {c["band"] for c in vj.CASES}
    assert bands == {"high", "low", "mid", "abstain"}
