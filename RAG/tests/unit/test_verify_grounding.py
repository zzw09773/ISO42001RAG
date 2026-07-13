"""Evidence-grounded verify — citation provenance gate.

The verify node must flag an answer that cites an article absent from both the
source identifiers and retrieved text, without second-guessing which articles
retrieval should have returned.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rag_system.agent.nodes import (
    create_verify_node, _article_nums, _retrieved_article_nums,
)
from rag_system.agent.react_workflow import _ungrounded_react_citations


def test_article_num_helpers():
    assert _article_nums("依第6條與第 14 條") == {6, 14}
    assert _article_nums("依第四十六條與第一百零二條") == {46, 102}
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


def test_short_ungrounded_citation_cannot_bypass_provenance():
    out = create_verify_node(llm=None)(
        _state("依據第99條，您必須立即服從。", ["法.md#第46條"])
    )
    assert out["scope"] == "needs_retry"


def test_citation_without_any_retrieved_source_requires_retry():
    gen = "依第四十六條規定應辦理。具體條文與參考資料如下，這段回答刻意超過五十字以驗證沒有來源時不能靠格式快速通過。"
    out = create_verify_node(llm=None)(_state(gen, []))
    assert out["scope"] == "needs_retry"


def test_ungrounded_citation_fails_safe_when_retry_budget_is_exhausted():
    vn = create_verify_node(llm=None)
    gen = ("依據第99條規定，違紀軍人應受懲處，這是一段足夠長的回答內容用以通過"
           "非實質性與無資訊快速通道的檢查，確保進入引用出處核對階段。")

    out = vn(
        _state(
            gen,
            ["陸海空軍懲罰法.md#第6條", "陸海空軍懲罰法.md#第8條"],
            retry=2,
        )
    )

    assert out["scope"] == "verified"
    assert "第99條" not in out["generation"]
    assert "failed_safe" in out["actions"][0]


def test_grounded_citation_passes():
    vn = create_verify_node(llm=None)   # llm=None → regex fallback AFTER the gate
    gen = ("依據第6條規定，違紀軍人應受懲處。具體條文如下所列，參考資料見後，"
           "這段內容足夠長以通過前置快速通道並帶有結構關鍵字。")
    out = vn(_state(gen, ["陸海空軍懲罰法.md#第6條"]))
    assert out["scope"] == "verified"


def test_cross_reference_present_in_retrieved_text_is_grounded():
    generation = (
        "依據第52條，復審決定原則上應於三個月內作成；條文並明確提及"
        "依第23條通知補正時的起算方式。以下為具體條文與程序摘要。"
    )
    out = create_verify_node(llm=None)({
        "generation": generation,
        "question": "52條",
        "retry_count": 0,
        "retrieved_sources": ["軍人權益事件處理法.md#第52條"],
        "retrieved_docs": [
            "來源: 軍人權益事件處理法.md\n"
            "內容: 第52條 復審決定應於三個月內為之。"
            "依第23條規定通知補正者，自補正之次日起算。"
        ],
    })

    assert out["scope"] == "verified"


def test_retrieved_cross_reference_does_not_allow_another_article():
    generation = (
        "依據第99條，復審決定必須立即作成。以下為具體條文與程序摘要，"
        "這段回答刻意超過五十字以進入引用出處核對。"
    )
    out = create_verify_node(llm=None)({
        "generation": generation,
        "question": "52條",
        "retry_count": 0,
        "retrieved_sources": ["軍人權益事件處理法.md#第52條"],
        "retrieved_docs": [
            "來源: 軍人權益事件處理法.md\n"
            "內容: 第52條 復審決定應於三個月內為之。"
            "依第23條規定通知補正者，自補正之次日起算。"
        ],
    })

    assert out["scope"] == "needs_retry"
    assert "第99條" in out["feedback"]


def test_no_sources_without_citations_uses_existing_verifier():
    vn = create_verify_node(llm=None)
    gen = ("目前僅能提供一般處理原則。具體條文尚待確認，參考資料亦未檢索到，"
           "這段內容足夠長以通過前置快速通道，但沒有宣稱任何條號。")
    out = vn(_state(gen, []))
    assert out["scope"] == "verified"


def test_requested_missing_article_is_not_treated_as_fabricated_citation():
    out = create_verify_node(llm=None)({
        "generation": "目前知識庫尚未收錄第99條，無法提供具體條文。",
        "question": "請問第99條的內容？",
        "retry_count": 0,
        "retrieved_sources": ["法.md#第6條"],
    })

    assert out["scope"] == "verified"


def test_no_info_phrase_cannot_hide_a_different_ungrounded_citation():
    out = create_verify_node(llm=None)({
        "generation": (
            "目前知識庫未檢索到第99條。"
            "但是依據第100條，您必須立即服從。"
        ),
        "question": "請問第99條的內容？",
        "retry_count": 0,
        "retrieved_sources": ["法.md#第6條"],
    })

    assert out["scope"] == "needs_retry"
    assert "第100條" in out["feedback"]


def test_no_info_phrase_cannot_hide_a_claim_for_the_same_article():
    out = create_verify_node(llm=None)({
        "generation": (
            "目前知識庫未檢索到第99條，"
            "但是依據第99條，您必須立即服從。"
        ),
        "question": "請問第99條的內容？",
        "retry_count": 0,
        "retrieved_sources": ["法.md#第6條"],
    })

    assert out["scope"] == "needs_retry"
    assert "第99條" in out["feedback"]


def test_answer_cannot_claim_no_information_while_citing_retrieved_evidence():
    generation = (
        "知識庫中尚未收錄相關內容，無法提供具體條文；"
        "但參考資料同時列有甲法第3條。"
    )
    out = create_verify_node(llm=None)({
        "generation": generation,
        "question": "我遇到一種事先完全沒列過的情況",
        "retry_count": 0,
        "retrieved_sources": ["甲法.md#第3條"],
        "actions": ["retrieve(docs=1,sources=1,domain_search=source_scoped)"],
    })

    assert out["scope"] == "needs_retry"
    assert "contradictory_no_info" in out["actions"][0]


def test_react_provenance_rejects_short_and_chinese_ungrounded_citations():
    assert _ungrounded_react_citations(
        "依據第99條，您必須立即服從。", ["法.md#第46條"]
    ) == {99}
    assert _ungrounded_react_citations(
        "依第四十六條辦理。", ["法.md#第46條"]
    ) == set()


def test_react_cross_reference_present_in_tool_evidence_is_grounded():
    evidence = ["來源: 法.md (第52條)\n第52條依第23條規定辦理。"]

    assert _ungrounded_react_citations(
        "依第52條及第23條辦理。", ["法.md#第52條"], evidence
    ) == set()
    assert _ungrounded_react_citations(
        "依第99條辦理。", ["法.md#第52條"], evidence
    ) == {99}
