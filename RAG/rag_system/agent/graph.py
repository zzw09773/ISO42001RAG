"""
Agent Graph — Multi-Node Workflow with Streaming Support

Implements a multi-node LangGraph workflow:

  START → classify → [legal]  → retrieve → generate → verify → [verified] → END
                     [reject] → reject → END          ↑          [needs_retry] ↓
                                                       └──────────────────────┘

Provides both synchronous (run_query) and async streaming (astream_query) APIs.
"""
from __future__ import annotations
import logging
from typing import Any, AsyncIterator, Dict, Optional, List

from langgraph.graph import END, StateGraph
from langchain_openai import ChatOpenAI

from ..core.config import RAGConfig
from ..core.factory import ComponentFactory
from ..core.audit_logger import AuditLogger
from .state import GraphState
from .nodes import (
    create_classify_node,
    create_reject_node,
    create_security_block_node,
    create_passthrough_node,
    create_capability_node,
    create_prc_block_node,
    create_retrieve_node,
    create_generate_node,
    create_verify_node,
)

logger = logging.getLogger(__name__)

# Cache for the compiled workflow
_WORKFLOW_CACHE = {}


def create_llm(config: RAGConfig) -> ChatOpenAI:
    """Create LLM for the Agent (using Factory)."""
    factory = ComponentFactory(config)
    return factory.create_llm(temperature=config.temperature)


def _route_after_classify(state: GraphState) -> str:
    """Route based on classification result."""
    scope = state.get("scope", "")
    if scope == "reject":
        return "reject"
    if scope == "passthrough":
        return "passthrough"
    if scope == "security_block":
        return "security_block"
    if scope == "capability":
        return "capability"
    if scope == "prc_block":
        return "prc_block"
    return "retrieve"


def _route_after_verify(state: GraphState) -> str:
    """Route based on verification result."""
    scope = state.get("scope", "")
    if scope == "needs_retry":
        return "retrieve"
    return END


def create_rag_workflow(
    config: RAGConfig,
    *,
    llm: Optional[ChatOpenAI] = None,
):
    """
    Create and compile the multi-node LangGraph workflow.

    Graph structure:
      classify → retrieve → generate → verify → END
               ↘ reject → END           ↑ needs_retry ↓
                                         └─────────────┘
    """
    use_cache = llm is None
    cache_key = hash(config)

    if use_cache and cache_key in _WORKFLOW_CACHE:
        return _WORKFLOW_CACHE[cache_key]

    config.validate()

    if llm is None:
        llm = create_llm(config)

    # Create nodes
    # v1.3 #5: classify_node now supports LLM-based routing (intent-aware)
    classify = create_classify_node(llm=llm)
    reject = create_reject_node()
    security_block = create_security_block_node(audit=AuditLogger(config.audit_log_dir))
    passthrough = create_passthrough_node(llm)
    capability = create_capability_node()
    prc_block = create_prc_block_node()
    retrieve = create_retrieve_node(config)
    generate = create_generate_node(llm, config)
    # v1.2 experiment (LLM-based verify) tried & reverted:
    #   - Hit Rate persisted at 0.9355 (target met)
    #   - Precision dropped 0.78 → 0.71 with NO Hit Rate gain
    #   - LLM verify added latency + cost without improving quality on this
    #     31-entry Chinese legal dataset; verdict logged in CHANGELOG.
    # The LLM-based verify class is kept in nodes.py — flip to `llm=llm`
    # to re-enable when retrying with larger dataset or different domain.
    verify = create_verify_node(llm=None)

    # Build Graph
    workflow = StateGraph(GraphState)

    workflow.add_node("classify", classify)
    workflow.add_node("reject", reject)
    workflow.add_node("security_block", security_block)
    workflow.add_node("passthrough", passthrough)
    workflow.add_node("capability", capability)
    workflow.add_node("prc_block", prc_block)
    workflow.add_node("retrieve", retrieve)
    workflow.add_node("generate", generate)
    workflow.add_node("verify", verify)

    # Edges
    workflow.set_entry_point("classify")

    workflow.add_conditional_edges(
        "classify",
        _route_after_classify,
        {"retrieve": "retrieve", "reject": "reject", "passthrough": "passthrough", "security_block": "security_block", "capability": "capability", "prc_block": "prc_block"},
    )
    workflow.add_edge("reject", END)
    workflow.add_edge("security_block", END)
    workflow.add_edge("passthrough", END)
    workflow.add_edge("capability", END)
    workflow.add_edge("prc_block", END)
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", "verify")
    workflow.add_conditional_edges(
        "verify",
        _route_after_verify,
        {"retrieve": "retrieve", END: END},
    )

    app = workflow.compile()

    if use_cache:
        _WORKFLOW_CACHE[cache_key] = app
        logger.info(f"Compiled and cached multi-node RAG workflow")

    return app


def _react_mode_enabled() -> bool:
    """Check REACT_MODE env var. Default: classic graph workflow."""
    import os as _os
    return _os.environ.get("REACT_MODE", "").lower() in ("true", "1", "yes")


def run_query(
    question: str,
    config: RAGConfig,
    *,
    llm: Optional[ChatOpenAI] = None,
    messages: Optional[list] = None,
    session_id: str = "",
    client_ip: str = "",
    audit_context: Optional[Dict[str, Any]] = None,
):
    """Execute a single query through the workflow (synchronous).

    Routes to ReAct agent (rag_system.agent.react_workflow) when
    REACT_MODE env var is set. Default routes to the classic StateGraph
    workflow (this file's create_rag_workflow).

    session_id is threaded into the graph state so security_block_node
    can log the alert against the SAME session as the originating query
    (ISO 42001 A.9 — security alerts must be traceable to their request).
    """
    if _react_mode_enabled():
        from .react_workflow import run_react_query
        return run_react_query(
            question,
            config,
            llm=llm,
            messages=messages,
            session_id=session_id,
            client_ip=client_ip,
            audit_context=audit_context,
        )

    workflow = create_rag_workflow(config, llm=llm)

    initial_messages = messages or [("user", question)]
    state = {
        "question": question,
        "generation": "",
        "messages": initial_messages,
        "collection": "",
        "retrieved_docs": [],
        "scope": "",
        "retry_count": 0,
        "session_id": session_id,
        "client_ip": client_ip,
        "audit_context": audit_context or {},
        "actions": [],
    }

    logger.info(f"Running workflow for question: {question}")
    return workflow.invoke(state, config={"recursion_limit": 50})


async def astream_query(
    question: str,
    config: RAGConfig,
    *,
    llm: Optional[ChatOpenAI] = None,
    messages: Optional[list] = None,
    session_id: str = "",
    client_ip: str = "",
    audit_context: Optional[Dict[str, Any]] = None,
    trace: Optional[dict] = None,
) -> AsyncIterator[str]:
    """
    Stream the response asynchronously intercepting LLM stream events.
    Uses LangGraph's astream_events API for true token-level streaming.

    Routes to ReAct agent if REACT_MODE env var is set.
    session_id threaded for security-alert traceability (see run_query).
    """
    if _react_mode_enabled():
        from .react_workflow import astream_react_query
        async for chunk in astream_react_query(
            question,
            config,
            llm=llm,
            messages=messages,
            session_id=session_id,
            client_ip=client_ip,
            audit_context=audit_context,
        ):
            yield chunk
        return

    workflow = create_rag_workflow(config, llm=llm)

    initial_messages = messages or [("user", question)]
    state = {
        "question": question,
        "generation": "",
        "messages": initial_messages,
        "collection": "",
        "retrieved_docs": [],
        "scope": "",
        "retry_count": 0,
        "session_id": session_id,
        "client_ip": client_ip,
        "audit_context": audit_context or {},
        "actions": [],
    }

    logger.info(f"Streaming workflow for question: {question}")

    # Use astream_events to intercept token generation
    #
    # Two kinds of nodes produce output:
    #   1. LLM nodes (generate / passthrough) → token-level on_chat_model_stream
    #   2. Non-LLM nodes (security_block / reject) → fixed string, NO LLM call,
    #      so they emit NO on_chat_model_stream events. Without special handling
    #      the client receives an EMPTY stream (role + finish, no content) and
    #      the user sees nothing — confusing for a blocked/rejected query.
    #      We catch their on_chain_end and yield their `generation` directly.
    _NON_LLM_OUTPUT_NODES = {"security_block", "reject", "capability", "prc_block"}
    _emitted_non_llm = set()
    try:
        generation_count = 0
        async for event in workflow.astream_events(state, config={"recursion_limit": 50}, version="v2"):
            node = event.get("metadata", {}).get("langgraph_node")
            etype = event["event"]

            # If we enter a new 'generate' node execution, increment count
            if etype == "on_chat_model_start" and node == "generate":
                generation_count += 1
                if generation_count > 1:
                    yield f"\n\n> ⚠️ **系統檢測到回答需要補強，正在進行第 {generation_count} 次深度檢索與生成...**\n\n"

            if etype == "on_chat_model_stream":
                # Token streaming from LLM-backed nodes
                if node in ["generate", "reject", "passthrough"]:
                    chunk = event["data"]["chunk"]
                    if getattr(chunk, "content", None):
                        yield chunk.content

            # Non-LLM nodes: emit their fixed message so the client isn't blank.
            # langgraph fires on_chain_end per node with the node name as the
            # event "name"; its output dict carries `generation`.
            if etype == "on_chain_end":
                out = event["data"].get("output") or {}
                # Accumulate the per-node action trail + retrieved sources so the
                # STREAMING audit can record them too (not just a "streamed"
                # marker). `node` (langgraph_node) is set only for real nodes, so
                # the graph-level on_chain_end doesn't double-count.
                if trace is not None and node and isinstance(out, dict):
                    if out.get("actions"):
                        trace.setdefault("actions", []).extend(out["actions"])
                    if out.get("retrieved_sources"):
                        trace["retrieved_sources"] = out["retrieved_sources"]
                ev_name = event.get("name", "")
                if ev_name in _NON_LLM_OUTPUT_NODES and ev_name not in _emitted_non_llm:
                    gen = out.get("generation", "") if isinstance(out, dict) else ""
                    if gen:
                        _emitted_non_llm.add(ev_name)
                        yield gen
    except Exception as e:
        logger.error(f"Streaming failed: {e}")
        yield f"抱歉，串流回覆時發生錯誤：{str(e)}"


__all__ = [
    "create_rag_workflow",
    "run_query",
    "astream_query",
]
