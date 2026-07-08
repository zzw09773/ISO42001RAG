"""
Health Severity Thresholds — USER-CONTRIBUTION POINT

This file encodes the *business risk tolerance* of your monitoring policy.
The thresholds below let the addon run, but the audit lead should replace
them with values appropriate to regulatory expectations (ISO 42001 A.6.2.4),
the unit's tolerance for false positives, and observed noise levels.

═══════════════════════════════════════════════════════════════════════
Available signals (live on the HealthReport object):

  report.perf.rejection_rate_delta        float, signed (+ = more rejections)
  report.perf.security_alert_rate_current 0–1 fraction of events
  report.perf.p95_latency_current_ms      float | None (P95 end-to-end ms)
  report.faithfulness_current             0–1 | None (RAGAS; higher=better)
  report.availability                     dict | None {uptime_pct, hard_down, ...}
  report.last_integrity_status            "intact" | "broken" | "unknown"

═══════════════════════════════════════════════════════════════════════
4-level numeric scale (0–100), MAX across the numeric dimensions
(weakest-link), with two BINARY critical overrides:

   0–25  normal   🟢   |  25–50 watch 🔵  |  50–75 warning 🟡  |  75–100 critical 🔴

Numeric dimensions: faithfulness, rejection, latency, availability(uptime%),
security. Binary critical overrides: availability hard-down, audit-chain broken.

Latency anchors (real baseline 2026-06-26: P95≈30s): ≤40s normal, 40–60s
watch/warning, >60s critical. Availability anchors: ≥99% normal, 95–99%
watch/warning, <95% critical.

Below config.MIN_PERF_SAMPLE queries the perf dimensions (rejection / latency
/ security) are skipped; faithfulness & availability are exempt. When NO
dimension is evaluable AND no binary override fires → "insufficient_data".
═══════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .config import MIN_PERF_SAMPLE


LEVEL_BOUNDARIES = [(25, "normal"), (50, "watch"), (75, "warning"), (101, "critical")]


def _score_to_level(score: float) -> str:
    for boundary, level in LEVEL_BOUNDARIES:
        if score < boundary:
            return level
    return "critical"


def _faithfulness_score(faithfulness: Optional[float]) -> Optional[float]:
    """Map current faithfulness (0–1, higher=better) to a 0–100 health score.

    Absolute-quality anchors (signed off 2026-06-22; hallucination is the
    operator's top concern):
      ≥ 0.90 → 0 (normal); 0.90→0.80 → 0–50 (watch); 0.80→0.65 → 50–75
      (warning); < 0.65 → 75–100 (critical). None if not measured this run.
    """
    if faithfulness is None:
        return None
    f = faithfulness
    if f >= 0.90:
        return 0.0
    if f >= 0.80:
        return round((0.90 - f) / 0.10 * 50, 1)
    if f >= 0.65:
        return round(50 + (0.80 - f) / 0.15 * 25, 1)
    return round(min(100.0, 75 + (0.65 - f) / 0.65 * 100), 1)


def _latency_score(p95_ms: Optional[float]) -> Optional[float]:
    """Map P95 latency (ms) to a 0–100 health score.

    Anchors (real baseline 2026-06-26: P95≈30s): ≤40s→0–25 normal,
    40–60s→25–75 watch/warning, >60s→75–100 critical.
    """
    if p95_ms is None:
        return None
    s = p95_ms / 1000.0
    if s <= 40:
        return round(s / 40 * 25, 1)
    if s <= 60:
        return round(25 + (s - 40) / 20 * 50, 1)
    return round(min(100.0, 75 + (s - 60) / 40 * 25), 1)


def _availability_score(uptime_pct: Optional[float]) -> Optional[float]:
    """Map uptime% (0–100, higher=better) to a 0–100 health score.

    Anchors: ≥99%→0–24 normal, 95–99%→24–75 watch/warning, <95%→75–100 critical.
    """
    if uptime_pct is None:
        return None
    u = uptime_pct
    if u >= 99:
        return round((100 - u) * 24, 1)            # 100→0, 99→24
    if u >= 95:
        return round(24 + (99 - u) / 4 * 51, 1)    # 99→24, 95→75
    return round(min(100.0, 75 + (95 - u) / 95 * 100), 1)


def classify_health(report) -> Tuple[str, List[str]]:
    """Classify overall service-health severity (4-level numeric + overrides).

    Numeric dimensions (max = overall_numeric): faithfulness, rejection,
    latency, availability uptime%, security. Perf dims (rejection/latency/
    security) gated by MIN_PERF_SAMPLE; faithfulness & availability exempt.
    BINARY critical overrides: availability hard-down, audit-chain broken.

    side-effect: writes report.dimension_scores and report.overall_score
    (matches the existing build_*_report usage pattern).
    Returns (severity, reasons).
    """
    n = report.queries_in_window
    reasons: List[str] = []
    dim: dict = {}

    # ── Performance: latency only — gated by sample size ──────────────
    # 拒絕率與安全告警率「不」進健康評分：兩者由使用者行為驅動（離題提問 →
    # 系統正確婉拒；資安提問 → 系統正確偵測），不代表系統退化，不應驅動
    # critical。它們仍在 A 區 KPI 顯示，安全攻擊另由 anomaly 告警路徑處理。
    if n >= MIN_PERF_SAMPLE:
        lat = _latency_score(getattr(report.perf, "p95_latency_current_ms", None))
        if lat is not None:
            dim["latency"] = lat
            if lat >= 25:
                reasons.append(f"延遲偏高 score={lat:.0f} (P95={report.perf.p95_latency_current_ms}ms)")
    else:
        reasons.append(f"效能維度：視窗內查詢數 n={n} < 最小樣本數 {MIN_PERF_SAMPLE}，本輪不評延遲。")

    # ── Faithfulness — not sample-gated (from RAGAS, not the window) ───
    faith_score = _faithfulness_score(getattr(report, "faithfulness_current", None))
    if faith_score is not None:
        dim["faithfulness"] = faith_score
        if faith_score >= 25:
            reasons.append(f"幻覺風險上升 score={faith_score:.0f} (Faithfulness={report.faithfulness_current})")

    # ── Availability — not sample-gated (from the probe loop) ─────────
    avail = getattr(report, "availability", None) or {}
    avail_pct = avail.get("recent_ok_pct")
    if avail_pct is None:
        avail_pct = avail.get("uptime_pct")
    avail_score = _availability_score(avail_pct)
    if avail_score is not None:
        dim["availability"] = avail_score
        if avail_score >= 25:
            reasons.append(
                f"近期可用率下降 score={avail_score:.0f} "
                f"(recent={avail_pct}%, 24h={avail.get('uptime_pct')}%)"
            )

    # ── Binary critical overrides ─────────────────────────────────────
    hard_down = bool(avail.get("hard_down"))
    chain_broken = getattr(report, "last_integrity_status", "unknown") == "broken"

    # ── Nothing evaluable AND no override → no verdict ────────────────
    if not dim and not hard_down and not chain_broken:
        try:
            report.dimension_scores = {}
            report.overall_score = 0.0
        except Exception:
            pass
        return "insufficient_data", reasons + [
            "無可評估維度（樣本不足、無 faithfulness、無可用率、鏈狀態未知）；"
            "累積流量、跑 RAGAS 或啟動探測後恢復判定。",
        ]

    overall = max(dim.values()) if dim else 0.0
    severity = _score_to_level(overall)
    if hard_down:
        severity = "critical"
        reasons.append("可用率 hard-down（關鍵依賴連續探測失敗）→ critical")
    if chain_broken:
        severity = "critical"
        reasons.append("audit 鏈損毀 → critical")

    try:
        report.dimension_scores = dim
        report.overall_score = round(overall, 1)
    except Exception:
        pass

    if not any("score=" in r for r in reasons):
        reasons.append("已評估維度在正常波動內（score < 25）。")
    reasons.insert(0, f"整體風險分數 = {overall:.0f}/100 → {severity}（0 低風險，100 嚴重）")
    return severity, reasons
