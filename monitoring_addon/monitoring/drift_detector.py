"""
Drift Detector — three classes of drift against a fixed baseline.

  1. Performance Drift — rejection rate / citation rate / latency
                          aggregated from audit logs.
  2. Data Drift        — query length & article-number distribution
                          (PSI / KL divergence on histograms).
  3. Embedding Drift   — semantic shift of query embeddings against
                          baseline, via centroid cosine distance and
                          PSI on the first PCA component.

Complements (does NOT replace) RAG/rag_system/core/anomaly_detector.py:
  anomaly = short-window outlier alert (in-memory deque)
  drift   = cross-period baseline comparison (this module)

Self-contained: does NOT import rag_system.*. Embedding drift talks to
embed-proxy via plain HTTP. numpy is optional; without it the embedding
drift falls back to character n-gram statistics.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple


# ───────────── Public dataclasses ─────────────


@dataclass
class PerfDrift:
    rejection_rate_baseline: float = 0.0
    rejection_rate_current: float = 0.0
    rejection_rate_delta: float = 0.0
    avg_latency_baseline_ms: Optional[float] = None
    avg_latency_current_ms: Optional[float] = None
    avg_latency_delta_pct: Optional[float] = None
    p95_latency_current_ms: Optional[float] = None
    citation_rate_baseline: float = 0.0
    citation_rate_current: float = 0.0
    citation_rate_delta: float = 0.0
    security_alert_rate_current: float = 0.0
    retry_rate_current: float = 0.0
    queries_observed: int = 0


@dataclass
class DataDrift:
    query_length_psi: float = 0.0
    article_freq_psi: float = 0.0
    char_unigram_kl: float = 0.0
    top_drift_articles: List[Tuple[str, float]] = field(default_factory=list)
    queries_observed: int = 0


@dataclass
class EmbeddingDrift:
    centroid_cosine_distance: float = 0.0
    pca_first_component_psi: float = 0.0
    samples: int = 0
    backend: str = "unavailable"  # "embeddings" | "char_ngram" | "unavailable"


@dataclass
class DriftReport:
    generated_at: str = ""
    baseline_label: str = ""
    window_days: int = 0
    queries_in_window: int = 0
    perf: PerfDrift = field(default_factory=PerfDrift)
    data: DataDrift = field(default_factory=DataDrift)
    embedding: EmbeddingDrift = field(default_factory=EmbeddingDrift)
    severity: str = "normal"
    severity_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "baseline_label": self.baseline_label,
            "window_days": self.window_days,
            "queries_in_window": self.queries_in_window,
            "severity": self.severity,
            "severity_reasons": self.severity_reasons,
            "perf": self.perf.__dict__,
            "data": self.data.__dict__,
            "embedding": self.embedding.__dict__,
        }


# ───────────── 1. Performance drift ─────────────


def compute_perf_drift(audit_events: List[dict], baseline: dict) -> PerfDrift:
    query_events = [e for e in audit_events if e.get("event_type") == "query"]
    rejection_events = [e for e in audit_events if e.get("event_type") == "rejection"]
    security_events = [e for e in audit_events if e.get("event_type") == "security_alert"]
    total_user = len(query_events) + len(rejection_events)

    ans_baseline = (baseline or {}).get("answer_quality_baseline", {})
    citation_baseline = float(ans_baseline.get("avg_article_match", 0.0) or 0.0)
    rejection_baseline = float((baseline or {}).get("rejection_rate_baseline", 0.05))

    rejection_current = (
        len(rejection_events) / total_user if total_user else 0.0
    )

    latencies = [
        e["response_time_ms"]
        for e in query_events
        if isinstance(e.get("response_time_ms"), int)
    ]
    avg_lat = statistics.mean(latencies) if latencies else None
    p95_lat = (
        sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 5 else None
    )
    avg_lat_base = (baseline or {}).get("avg_latency_ms_baseline")
    if isinstance(avg_lat_base, (int, float)) and avg_lat:
        lat_delta_pct = (avg_lat - avg_lat_base) / max(avg_lat_base, 1.0)
    else:
        lat_delta_pct = None

    in_scope = [e for e in query_events if e.get("scope_check") == "in_scope"]
    if in_scope:
        with_cite = sum(1 for e in in_scope if (e.get("citation_count") or 0) > 0)
        citation_current = with_cite / len(in_scope)
    else:
        citation_current = 0.0

    retries = sum(1 for e in query_events if (e.get("retry_count") or 0) > 0)
    retry_rate = retries / len(query_events) if query_events else 0.0

    sec_rate = len(security_events) / total_user if total_user else 0.0

    return PerfDrift(
        rejection_rate_baseline=round(rejection_baseline, 4),
        rejection_rate_current=round(rejection_current, 4),
        rejection_rate_delta=round(rejection_current - rejection_baseline, 4),
        avg_latency_baseline_ms=avg_lat_base,
        avg_latency_current_ms=round(avg_lat, 1) if avg_lat else None,
        avg_latency_delta_pct=round(lat_delta_pct, 3) if lat_delta_pct is not None else None,
        p95_latency_current_ms=p95_lat,
        citation_rate_baseline=round(citation_baseline, 4),
        citation_rate_current=round(citation_current, 4),
        citation_rate_delta=round(citation_current - citation_baseline, 4),
        security_alert_rate_current=round(sec_rate, 4),
        retry_rate_current=round(retry_rate, 4),
        queries_observed=len(query_events),
    )


# ───────────── 2. Data drift ─────────────

_ARTICLE_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百零兩]+)\s*條")


def _query_length_histogram(queries: List[str]) -> Dict[str, int]:
    buckets = {"0-20": 0, "21-50": 0, "51-100": 0, "101-200": 0, "200+": 0}
    for q in queries:
        n = len(q)
        if n <= 20:
            buckets["0-20"] += 1
        elif n <= 50:
            buckets["21-50"] += 1
        elif n <= 100:
            buckets["51-100"] += 1
        elif n <= 200:
            buckets["101-200"] += 1
        else:
            buckets["200+"] += 1
    return buckets


def _article_frequency(queries: List[str]) -> Counter:
    c: Counter = Counter()
    for q in queries:
        for m in _ARTICLE_RE.finditer(q):
            c[m.group(0)] += 1
    return c


def _char_unigram(queries: List[str]) -> Counter:
    c: Counter = Counter()
    for q in queries:
        for ch in q:
            if ch.strip() and not ch.isascii():
                c[ch] += 1
    return c


def psi(baseline: Dict[Any, float], current: Dict[Any, float], eps: float = 1e-4) -> float:
    """Population Stability Index. 0 ≈ identical, >0.2 mild, >0.5 severe.

    PSI = Σ (curr_i - base_i) * ln(curr_i / base_i) over normalised proportions.
    """
    keys = set(baseline) | set(current)
    tb = sum(baseline.values()) or 1.0
    tc = sum(current.values()) or 1.0
    score = 0.0
    for k in keys:
        b = (baseline.get(k, 0) / tb) or eps
        c = (current.get(k, 0) / tc) or eps
        score += (c - b) * math.log(c / b)
    return score


def kl_divergence(p: Dict[Any, float], q: Dict[Any, float], eps: float = 1e-6) -> float:
    """KL(P‖Q) on count dicts."""
    keys = set(p) | set(q)
    tp = sum(p.values()) or 1.0
    tq = sum(q.values()) or 1.0
    score = 0.0
    for k in keys:
        pi = (p.get(k, 0) / tp) or eps
        qi = (q.get(k, 0) / tq) or eps
        score += pi * math.log(pi / qi)
    return score


def compute_data_drift(audit_events: List[dict], baseline_queries: List[str]) -> DataDrift:
    current = [
        e.get("user_query", "")
        for e in audit_events
        if e.get("event_type") in {"query", "rejection"} and e.get("user_query")
    ]
    if not current or not baseline_queries:
        return DataDrift(queries_observed=len(current))

    len_psi = psi(_query_length_histogram(baseline_queries), _query_length_histogram(current))
    base_art = _article_frequency(baseline_queries)
    curr_art = _article_frequency(current)
    art_psi = psi(dict(base_art), dict(curr_art))
    char_kl = kl_divergence(dict(_char_unigram(baseline_queries)), dict(_char_unigram(current)))

    tb = sum(base_art.values()) or 1
    tc = sum(curr_art.values()) or 1
    deltas = [
        (a, round(curr_art.get(a, 0) / tc - base_art.get(a, 0) / tb, 4))
        for a in set(base_art) | set(curr_art)
    ]
    deltas.sort(key=lambda x: abs(x[1]), reverse=True)

    return DataDrift(
        query_length_psi=round(len_psi, 4),
        article_freq_psi=round(art_psi, 4),
        char_unigram_kl=round(char_kl, 4),
        top_drift_articles=deltas[:10],
        queries_observed=len(current),
    )


# ───────────── 3. Embedding drift ─────────────


def _http_embed_fn() -> Optional[Callable[[List[str]], List[List[float]]]]:
    """Build an HTTP-based embedder that calls embed-proxy (OpenAI-compatible).

    Returns None if `requests` is unavailable or essential env vars missing.
    """
    try:
        import requests
    except ImportError:
        return None

    base = os.environ.get("EMBED_API_BASE") or os.environ.get("EMBED_PROXY_URL")
    if not base:
        return None
    url = base.rstrip("/") + "/embeddings"
    model = os.environ.get("EMBED_MODEL_NAME", "nvidia/nv-embed-v2")
    key = os.environ.get("EMBED_API_KEY", "")
    timeout = float(os.environ.get("EMBED_TIMEOUT_SEC", "30"))

    def embed(texts: List[str]) -> List[List[float]]:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        # Batch by 32 to avoid oversized payloads
        out: List[List[float]] = []
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            resp = requests.post(
                url,
                headers=headers,
                json={"input": batch, "model": model},
                timeout=timeout,
                verify=os.environ.get("VERIFY_SSL", "true").lower() != "false",
            )
            resp.raise_for_status()
            payload = resp.json()
            for item in payload.get("data", []):
                out.append(item.get("embedding", []))
        return out

    return embed


def compute_embedding_drift(
    audit_events: List[dict],
    baseline_queries: List[str],
    *,
    embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    max_samples: int = 200,
) -> EmbeddingDrift:
    """Embed current vs baseline queries; report centroid cosine distance
    and PSI on first PCA component. Falls back to char n-gram if numpy
    or embed-proxy is unavailable.
    """
    current = [
        e.get("user_query", "")
        for e in audit_events
        if e.get("event_type") in {"query", "rejection"} and e.get("user_query")
    ][:max_samples]
    base = baseline_queries[:max_samples]
    if not current or not base:
        return EmbeddingDrift(samples=0, backend="unavailable")

    try:
        import numpy as np  # type: ignore
    except ImportError:
        return _embedding_drift_ngram(current, base)

    fn = embed_fn or _http_embed_fn()
    if fn is None:
        return _embedding_drift_ngram(current, base)

    try:
        base_vecs = np.asarray(fn(base), dtype=float)
        curr_vecs = np.asarray(fn(current), dtype=float)
        if base_vecs.size == 0 or curr_vecs.size == 0:
            return _embedding_drift_ngram(current, base)
    except Exception:
        return _embedding_drift_ngram(current, base)

    base_c = base_vecs.mean(axis=0)
    curr_c = curr_vecs.mean(axis=0)
    denom = (np.linalg.norm(base_c) * np.linalg.norm(curr_c)) or 1.0
    cosine_dist = 1.0 - float(np.dot(base_c, curr_c) / denom)

    centered = base_vecs - base_c
    try:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        pc1 = vt[0]
    except np.linalg.LinAlgError:
        pc1 = base_c / (np.linalg.norm(base_c) or 1.0)
    base_proj = (centered @ pc1).tolist()
    curr_proj = ((curr_vecs - base_c) @ pc1).tolist()
    pc1_psi = psi(_bucket(base_proj, 8), _bucket(curr_proj, 8))

    return EmbeddingDrift(
        centroid_cosine_distance=round(cosine_dist, 4),
        pca_first_component_psi=round(pc1_psi, 4),
        samples=len(current),
        backend="embeddings",
    )


def _embedding_drift_ngram(current: List[str], baseline: List[str]) -> EmbeddingDrift:
    def ngrams(qs: List[str]) -> Counter:
        c: Counter = Counter()
        for q in qs:
            for i in range(max(len(q) - 2, 0)):
                c[q[i : i + 3]] += 1
        return c

    score = psi(dict(ngrams(baseline)), dict(ngrams(current)))
    return EmbeddingDrift(
        centroid_cosine_distance=0.0,
        pca_first_component_psi=round(score, 4),
        samples=len(current),
        backend="char_ngram",
    )


def _bucket(values: List[float], bins: int) -> Dict[int, int]:
    if not values:
        return {}
    lo, hi = min(values), max(values)
    if hi == lo:
        return {0: len(values)}
    width = (hi - lo) / bins
    out: Dict[int, int] = {}
    for v in values:
        idx = min(int((v - lo) / width), bins - 1)
        out[idx] = out.get(idx, 0) + 1
    return out


# ───────────── Top-level entry ─────────────


def build_drift_report(
    audit_events: List[dict],
    baseline_vv_report: dict,
    baseline_queries: List[str],
    *,
    embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    window_days: int = 7,
    baseline_label: str = "vv_baseline",
) -> DriftReport:
    """One-stop entry: compute three drift dimensions and assemble the report.
    Severity is filled in by `thresholds.classify_drift_severity()`.
    """
    from .thresholds import classify_drift_severity

    perf = compute_perf_drift(audit_events, baseline_vv_report)
    data = compute_data_drift(audit_events, baseline_queries)
    emb = compute_embedding_drift(audit_events, baseline_queries, embed_fn=embed_fn)

    report = DriftReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        baseline_label=baseline_label,
        window_days=window_days,
        queries_in_window=sum(
            1 for e in audit_events if e.get("event_type") in {"query", "rejection"}
        ),
        perf=perf,
        data=data,
        embedding=emb,
    )
    report.severity, report.severity_reasons = classify_drift_severity(report)
    return report
