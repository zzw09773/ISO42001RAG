"""
Drift Severity Thresholds — USER-CONTRIBUTION POINT

This file encodes the *business risk tolerance* of your monitoring policy.
The thresholds below are **placeholders** that let the addon run, but they
do NOT reflect the operating unit's actual policy. The audit lead must
replace them with thresholds appropriate to:

  - regulatory expectations (ISO 42001 A.6.2.4 monitoring),
  - the unit's tolerance for false positives vs missed drift,
  - empirical noise levels observed in the first weeks of operation.

═══════════════════════════════════════════════════════════════════════
Available signals (all live on the DriftReport object):

  report.perf.rejection_rate_delta        float, signed
                                          + means more rejections than baseline
  report.perf.citation_rate_delta         float, signed
                                          - means answers cite fewer articles
  report.perf.avg_latency_delta_pct       float | None; +0.5 = 50% slower
  report.perf.security_alert_rate_current 0–1 fraction of events
  report.perf.retry_rate_current          0–1 fraction of queries

  report.data.query_length_psi            PSI on length buckets
  report.data.article_freq_psi            PSI on cited-article frequency
  report.data.char_unigram_kl             KL divergence on character usage

  report.embedding.centroid_cosine_distance  0–2 (0 identical)
  report.embedding.pca_first_component_psi   PSI on PC1 projection

PSI rule-of-thumb (industry-standard, adjust if your policy differs):
  < 0.10       stable
  0.10 – 0.25  mild drift
  > 0.25       severe drift

Cosine-distance rule-of-thumb on normalised embeddings:
  < 0.05       stable
  0.05 – 0.15  mild
  > 0.15       severe

═══════════════════════════════════════════════════════════════════════
Return contract:

  severity ∈ {"normal", "warning", "critical"}
  reasons  : list[str]  ← human-readable explanations (shown in report)

═══════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from typing import List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .drift_detector import DriftReport


def classify_drift_severity(report: "DriftReport") -> Tuple[str, List[str]]:
    """Classify overall drift severity from component-level measurements.

    ┌──────────────────────────────────────────────────────────────────┐
    │  ⬇⬇⬇   USER-CONTRIBUTION POINT — REPLACE BODY BELOW   ⬇⬇⬇        │
    │                                                                  │
    │  The body below is a *placeholder* using only the most obvious   │
    │  PSI thresholds. Your policy probably differs along at least     │
    │  these axes:                                                     │
    │                                                                  │
    │    - "critical" trigger conditions (latency? security? both?)    │
    │    - whether citation rate drop should escalate severity         │
    │    - tolerance for cosine distance on small samples              │
    │    - whether multiple "warning" signals compound to "critical"   │
    │                                                                  │
    │  Replace the ~10 lines below with your unit's policy and         │
    │  keep the return signature unchanged. The dashboard and the      │
    │  drift report markdown both consume this function's output.      │
    └──────────────────────────────────────────────────────────────────┘
    """
    reasons: List[str] = []
    severity = "normal"

    # ─── PLACEHOLDER POLICY (replace with real thresholds) ─────────────
    if report.data.article_freq_psi > 0.25:
        severity = "warning"
        reasons.append(
            f"article_freq_psi={report.data.article_freq_psi} > 0.25 (data drift)"
        )
    if report.embedding.pca_first_component_psi > 0.25:
        severity = "warning"
        reasons.append(
            f"embedding_pc1_psi={report.embedding.pca_first_component_psi} > 0.25"
        )
    if report.perf.security_alert_rate_current > 0.10:
        severity = "critical"
        reasons.append(
            f"security_alert_rate={report.perf.security_alert_rate_current} > 0.10"
        )
    # ─── END PLACEHOLDER ──────────────────────────────────────────────

    if not reasons:
        reasons.append("No threshold breached under current policy.")
    return severity, reasons
