"""ISO 42001 Monitoring Addon — drift detection, extended IR metrics, dashboard.

This package is **fully decoupled** from the RAG/ main system. It only reads
audit logs and the golden dataset, and writes its own outputs to
monitoring_addon/data/reports/. It must NEVER import rag_system.* or write to
the RAG/ tree.
"""

__version__ = "0.1.0"
