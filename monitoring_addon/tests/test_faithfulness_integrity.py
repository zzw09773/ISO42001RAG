"""P1 — faithfulness 數值可信度（三態 / 拒答分類 / counts 重算）。

鎖住內網 faithfulness 缺陷的修正：
  - judge 連不上 / 缺 score → 必須是 None，**不得偽裝成 0.0**（假性 100% 幻覺）
  - 拒答（OOS 防護 / 無條文）→ 排除於 faithfulness，**不得偽裝成 1.0**
  - 分數由 grounded/(grounded+ungrounded) 重算，不採信 judge 自報算術
  - 拒答型態對齊系統真實話術（answer_evaluator.py / api.py 同源字串）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitoring.ragas_metrics as rm


def test_classify_matches_system_phrasings():
    assert rm.classify_rag_answer(
        "本系統僅提供法律文件檢索與解釋服務，無法回答與法律無關的問題。") == "rejection_oos"
    assert rm.classify_rag_answer(
        "目前知識庫中尚未收錄與此問題相關的法規內容，無法提供具體條文。") == "no_coverage"
    assert rm.classify_rag_answer("   ") == "empty"
    assert rm.classify_rag_answer("依第6條，酒駕屬勤務外違紀行為應受懲罰。") == "answer"


def test_valid_unit_score_rejects_missing_and_invalid():
    assert rm._valid_unit_score({"grounded_claims": 1}) is None   # 缺 score 鍵 → None（非 0.0）
    assert rm._valid_unit_score({"score": "x"}) is None           # 非數字
    assert rm._valid_unit_score({"score": True}) is None          # bool 不算分數
    assert rm._valid_unit_score({"score": 1.5}) == 1.0            # 上限夾擠
    assert rm._valid_unit_score({"score": -0.2}) == 0.0           # 下限夾擠
    assert rm._valid_unit_score({"score": 0.83}) == 0.83


def test_faithfulness_recomputes_from_counts(monkeypatch):
    # judge 自報 0.99（錯），但 counts 3/4 → 應重算為 0.75
    monkeypatch.setattr(rm, "_call_llm",
                        lambda *a, **k: '{"grounded_claims":3,"ungrounded_claims":1,"score":0.99}')
    r = rm.score_faithfulness("q", "c", "a")
    assert r["status"] == "ok"
    assert r["score"] == 0.75


def test_no_counts_does_not_trust_self_reported_score(monkeypatch):
    # judge 只回 score、無 grounded/ungrounded → 不採信自報分數 → unavailable。
    # 移除 self-report fallback。
    monkeypatch.setattr(rm, "_call_llm", lambda *a, **k: '{"score": 0.91}')
    r = rm.score_faithfulness("q", "c", "a")
    assert r["status"] == "unavailable"
    assert r["score"] is None


def test_faithfulness_unavailable_is_none_not_zero(monkeypatch):
    monkeypatch.setattr(rm, "_call_llm", lambda *a, **k: "not json at all")
    r = rm.score_faithfulness("q", "c", "a")
    assert r["status"] == "unavailable"
    assert r["score"] is None


def test_faithfulness_abstention_is_none_not_one(monkeypatch):
    monkeypatch.setattr(rm, "_call_llm",
                        lambda *a, **k: '{"is_abstention": true, "grounded_claims":0, "ungrounded_claims":0}')
    r = rm.score_faithfulness("q", "c", "a")
    assert r["status"] == "abstention"
    assert r["score"] is None


def test_aggregate_excludes_refusals_and_outages():
    scores = [
        {"faithfulness": {"status": "ok", "score": 0.8}, "answer_relevancy": 0.9, "context_precision": 0.7},
        {"faithfulness": {"status": "rejection_oos", "score": None}, "answer_relevancy": None, "context_precision": None},
        {"faithfulness": {"status": "no_coverage", "score": None}, "answer_relevancy": None, "context_precision": None},
        {"faithfulness": {"status": "unavailable", "score": None}, "answer_relevancy": None, "context_precision": None},
    ]
    agg = rm.aggregate_ragas_scores(scores).to_dict()
    assert agg["faithfulness"] == 0.8                 # 只計唯一 "ok"
    assert agg["faithfulness_evaluated"] == 1
    assert agg["faithfulness_breakdown"]["rejection_oos"] == 1
    assert agg["faithfulness_breakdown"]["no_coverage"] == 1
    # in-scope 拒答率只含 no_coverage（疑似漏接），不含正確的 OOS 拒答
    assert agg["no_coverage_rate"] == 0.25


def test_aggregate_all_unavailable_is_none_not_zero():
    scores = [{"faithfulness": {"status": "unavailable", "score": None},
               "answer_relevancy": None, "context_precision": None}]
    agg = rm.aggregate_ragas_scores(scores).to_dict()
    assert agg["faithfulness"] is None                # 全失效 → None（非 0.0）
    assert agg["overall"] is None


def test_no_context_is_not_false_zero(monkeypatch):
    # 有實質答案但 context 為空（檢索無結果/未紀錄）→ faithfulness 不可測，
    # 必須是 "no_context"，**不得評成假性 0.0**；relevancy 不需 context 仍可評。
    monkeypatch.setattr(rm, "_call_llm", lambda *a, **k: '{"score": 0.9}')
    out = rm.score_single("某法律問題", "", [], "依第2條，違紀行為之懲罰依本法行之。")
    assert out["faithfulness"]["status"] == "no_context"
    assert out["faithfulness"]["score"] is None
    assert out["answer_relevancy"] == 0.9
