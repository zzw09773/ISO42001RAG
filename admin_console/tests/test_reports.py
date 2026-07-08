import json
from pathlib import Path

from admincore.reports import flip_compare, list_reports


def _write(dirp: Path, name: str, per_query, hit_rate=0.9, when="2026-07-08T10:00:00"):
    dirp.mkdir(parents=True, exist_ok=True)
    (dirp / name).write_text(json.dumps({
        "generated_at": when, "hit_rate": hit_rate, "per_query": per_query,
    }, ensure_ascii=False), encoding="utf-8")


def _pq(qid, hit):
    return {"id": qid, "query": f"問題{qid}", "hit_rate": 1.0 if hit else 0.0,
            "expected_articles": [], "cited_articles": []}


def test_list_reports_sorted_and_tolerant(tmp_path):
    _write(tmp_path, "vv_report_2026-07-01.json", [_pq("q1", True)], when="2026-07-01T09:00:00")
    _write(tmp_path, "vv_report_2026-07-08.json", [_pq("q1", True)], when="2026-07-08T09:00:00")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    rows = list_reports(tmp_path)
    assert [r["file"] for r in rows] == ["vv_report_2026-07-08.json", "vv_report_2026-07-01.json"]
    assert rows[0]["kind"] == "vv" and rows[0]["n"] == 1 and rows[0]["hit_rate"] == 0.9


def test_flip_compare(tmp_path):
    _write(tmp_path, "base.json", [_pq("q1", True), _pq("q2", True), _pq("q3", False)])
    _write(tmp_path, "cur.json", [_pq("q1", True), _pq("q2", False), _pq("q3", False), _pq("q4", True)])
    out = flip_compare(tmp_path / "base.json", tmp_path / "cur.json")
    assert [x["id"] for x in out["newly_failed"]] == ["q2"]
    assert out["newly_passed"] == []
    assert [x["id"] for x in out["still_failed"]] == ["q3"]
    assert out["base_n"] == 3 and out["cur_n"] == 4
