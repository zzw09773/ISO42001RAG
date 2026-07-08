"""評估報告列表摘要與 per-query flip 比對（對應 monitoring 的比較方法）。"""
from __future__ import annotations

import json
from pathlib import Path


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _kind(name: str) -> str:
    if name.startswith("vv_report"):
        return "vv"
    if "ragas" in name:
        return "ragas"
    return "other"


def list_reports(reports_dir: Path) -> list[dict]:
    reports_dir = Path(reports_dir)
    rows: list[dict] = []
    if not reports_dir.is_dir():
        return rows
    for p in reports_dir.glob("*.json"):
        d = _load(p)
        if d is None:
            continue
        pq = d.get("per_query") or []
        rows.append({
            "file": p.name,
            "kind": _kind(p.name),
            "generated_at": str(d.get("generated_at", "")),
            "hit_rate": d.get("hit_rate"),
            "n": len(pq) if pq else None,
        })
    rows.sort(key=lambda r: r["generated_at"], reverse=True)
    return rows


def _hit(rec: dict) -> bool:
    if "hit_rate" in rec:
        return rec["hit_rate"] == 1.0
    return bool(rec.get("hit"))


def _index(path: Path) -> dict[str, dict]:
    d = _load(path) or {}
    return {str(r.get("id")): r for r in d.get("per_query") or []}


def flip_compare(base_path: Path, cur_path: Path) -> dict:
    base, cur = _index(Path(base_path)), _index(Path(cur_path))
    def brief(r: dict) -> dict:
        return {"id": str(r.get("id")), "query": str(r.get("query", ""))[:80]}
    newly_failed = [brief(cur[q]) for q in sorted(cur) if q in base and _hit(base[q]) and not _hit(cur[q])]
    newly_passed = [brief(cur[q]) for q in sorted(cur) if q in base and not _hit(base[q]) and _hit(cur[q])]
    still_failed = [brief(cur[q]) for q in sorted(cur) if q in base and not _hit(base[q]) and not _hit(cur[q])]
    return {"newly_failed": newly_failed, "newly_passed": newly_passed,
            "still_failed": still_failed, "base_n": len(base), "cur_n": len(cur)}
