"""Shared monitoring config — business goals and global targets.

Keep this module dependency-free so any other monitoring module can import
it without circular imports.
"""
import os

# Business goal: primary acceptance criterion for the RAG system's retrieval.
# Hit Rate is the gating metric — every other IR metric is informational only.
BUSINESS_GOAL_HIT_RATE = 0.90

# Minimum queries in the window before the PERF dimensions (rejection / latency
# / security) are scored. Below this they are skipped (faithfulness &
# availability are exempt — they don't come from the audit window).
# No longer tied to PSI/JSD (those distribution metrics were removed), so the
# old "small-sample saturation" rationale is gone — this is simply "enough
# traffic for a rate to be meaningful". Override via env MIN_PERF_SAMPLE.
MIN_PERF_SAMPLE = int(os.environ.get("MIN_PERF_SAMPLE", "30"))

# Consecutive worsened windows a GRADUAL-dimension escalation must persist
# before it alerts — suppresses single-window noise. Availability hard-down and
# audit-chain breakage bypass this (they alert immediately via their own loops).
DRIFT_CONFIRM_WINDOWS = int(os.environ.get("DRIFT_CONFIRM_WINDOWS", "2"))

# A RAGAS faithfulness report older than this many days is shown as STALE on the
# dashboard (re-run run_ragas_evaluation.py). faithfulness is a point-in-time
# snapshot, not continuous — so its age must be visible.
RAGAS_FRESHNESS_DAYS = int(os.environ.get("RAGAS_FRESHNESS_DAYS", "30"))

# ── Availability composite probe ──────────────────────────────────────────
# How often the probe loop hits each dependency's health endpoint.
PROBE_INTERVAL_SEC = int(os.environ.get("PROBE_INTERVAL_SEC", "60"))
# Consecutive all-critical-deps-down probes before a hard-down critical alert.
PROBE_FAIL_CONFIRM = int(os.environ.get("PROBE_FAIL_CONFIRM", "3"))
# Non-destructive rotate threshold for availability_log.jsonl (also rotates on
# month change). Archived to availability_log_YYYY-MM.jsonl, never truncated.
AVAIL_LOG_ROTATE_MB = float(os.environ.get("AVAIL_LOG_ROTATE_MB", "5"))
