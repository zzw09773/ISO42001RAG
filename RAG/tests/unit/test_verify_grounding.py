"""Evidence-grounded verify — citation provenance gate.

The verify node must flag an answer that cites an article NOT present in
retrieved_sources (a fabricated/ungrounded citation), WITHOUT second-guessing
which articles retrieval should have returned.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rag_system.agent.nodes import (
    create_verify_node, _article_nums, _retrieved_article_nums,
)


def test_article_num_helpers():
    assert _article_nums("依第6條與第 14 條") == {6, 14}
    assert _article_nums("無條號") == set()
    assert _retrieved_article_nums(["陸海空軍懲罰法.md#第6條", "法.md#第8條"]) == {6, 8}


def _state(generation, sources, retry=0):
    return {"generation": generation, "question": "軍人違紀如何處理？",
            "retry_count": retry, "retrieved_sources": sources}


def test_ungrounded_citation_triggers_retry():
    vn = create_verify_node(llm=None)
    gen = ("依據第99條規定，違紀軍人應受懲處，這是一段足夠長的回答內容用以通過"
           "非實質性與無資訊快速通道的檢查，確保進入引用出處核對階段。")
    out = vn(_state(gen, ["陸海空軍懲罰法.md#第6條", "陸海空軍懲罰法.md#第8條"]))
    assert out["scope"] == "needs_retry"
    assert "ungrounded_citation" in out["actions"][0]


def test_grounded_citation_passes():
    vn = create_verify_node(llm=None)   # llm=None → regex fallback AFTER the gate
    gen = ("依據第6條規定，違紀軍人應受懲處。具體條文如下所列，參考資料見後，"
           "這段內容足夠長以通過前置快速通道並帶有結構關鍵字。")
    out = vn(_state(gen, ["陸海空軍懲罰法.md#第6條"]))
    assert out["scope"] == "verified"


def test_no_sources_skips_provenance():
    # 無 retrieved_sources → 不做出處核對（避免誤殺），落到既有 regex 判定
    vn = create_verify_node(llm=None)
    gen = ("依據第99條規定，違紀軍人應受懲處。具體條文如下，參考資料見後，"
           "這段內容足夠長以通過前置快速通道。")
    out = vn(_state(gen, []))
    assert out["scope"] == "verified"
