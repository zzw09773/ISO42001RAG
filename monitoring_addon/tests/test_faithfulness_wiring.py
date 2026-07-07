"""Tests for real-faithfulness wiring: article resolver + RAGAS report loading.

Covers the intranet fix "真的顯示 faithfulness":
  - article_resolver parses converted_md into per-article text so the judge
    sees real content (not just labels).
  - load_ragas_report + build_payload populate faithfulness on the dashboard.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.article_resolver import load_article_index, resolve_context, _norm_article
from monitoring.baseline_loader import load_ragas_report


_LAW_MD = """法規名稱：測試法
修正日期：民國 113 年 08 月 07 日
   第 一 章 總則
第 1 條
為測試而制定本法。
第 2 條
1   第一項內容。
2   第二項內容。
第 3 條
甲乙丙丁。
"""


def test_norm_article_strips_spaces():
    assert _norm_article("第 14 條") == "第14條"
    assert _norm_article("第14條") == "第14條"


def test_load_article_index_parses_articles(tmp_path):
    d = tmp_path / "converted_md"
    d.mkdir()
    (d / "測試法.md").write_text(_LAW_MD, encoding="utf-8")
    idx = load_article_index(d)
    assert ("測試法", "第1條") in idx
    assert ("測試法", "第2條") in idx
    assert "為測試而制定本法" in idx[("測試法", "第1條")]
    # multi-line article body captured
    assert "第二項內容" in idx[("測試法", "第2條")]


def test_resolve_context_inlines_real_text(tmp_path):
    d = tmp_path / "converted_md"
    d.mkdir()
    (d / "測試法.md").write_text(_LAW_MD, encoding="utf-8")
    idx = load_article_index(d)
    ctx = resolve_context(["測試法.md#第1條", "測試法.md#第2條"], idx)
    assert "為測試而制定本法" in ctx          # real content, not just label
    assert "第二項內容" in ctx
    assert "【測試法 第1條】" in ctx


def test_resolve_context_falls_back_for_missing(tmp_path):
    d = tmp_path / "converted_md"
    d.mkdir()
    (d / "測試法.md").write_text(_LAW_MD, encoding="utf-8")
    idx = load_article_index(d)
    ctx = resolve_context(["測試法.md#第999條"], idx)
    assert "條文未解析" in ctx


def test_load_ragas_report_reads_latest(tmp_path):
    p = tmp_path / "ragas_2026-06-16.json"
    p.write_text(json.dumps({"judge_prompt": "faithfulness_v2_abstention",
                             "aggregate": {"faithfulness": 0.87}}), encoding="utf-8")
    r = load_ragas_report(p)
    assert r["aggregate"]["faithfulness"] == 0.87


def test_load_ragas_report_empty_when_missing(tmp_path):
    assert load_ragas_report(tmp_path / "nope.json") == {}


def test_load_ragas_report_rejects_legacy_schema(tmp_path):
    # 無 judge_prompt 的舊報告（可能含假 0.0/1.0）→ 拒絕（P5 schema gate）
    p = tmp_path / "ragas_2026-01-01.json"
    p.write_text(json.dumps({"aggregate": {"faithfulness": 0.0}}), encoding="utf-8")
    assert load_ragas_report(p) == {}


def test_ragas_report_meta_flags_stale_and_provenance():
    from monitoring.baseline_loader import ragas_report_meta
    m = ragas_report_meta({"generated_at": "2020-01-01T00:00:00+00:00",
                           "judge_model": "gpt-oss-20b"})
    assert m["available"] is True
    assert m["judge_model"] == "gpt-oss-20b"
    assert m["stale"] is True
    assert ragas_report_meta({})["available"] is False
