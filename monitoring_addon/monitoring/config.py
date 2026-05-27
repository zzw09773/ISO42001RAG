"""Shared monitoring config — business goals and global targets.

Keep this module dependency-free so any other monitoring module can import
it without circular imports.
"""

# Business goal: primary acceptance criterion for the RAG system's retrieval.
# Hit Rate is the gating metric — every other IR metric is informational only.
BUSINESS_GOAL_HIT_RATE = 0.90
