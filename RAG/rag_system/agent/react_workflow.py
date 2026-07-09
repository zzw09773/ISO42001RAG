"""
ReAct Workflow — true Agentic RAG (prototype, opt-in via REACT_MODE env var)

Difference from the classic StateGraph workflow (graph.py):

  classic:   classify → retrieve → generate → verify → END
                       (fixed pipeline, regex/LLM gates)

  react:     LLM decides ──┬─> tool: retrieve_legal_docs ──┐
                            │                               │
                            └─< observation results <───────┘
                            │
                            ▼
                            LLM may call retrieve again (multi-hop)
                            or produce final answer

Key wins of ReAct:
  - LLM decides WHEN to retrieve (zero, one, or multiple hops)
  - Multi-hop for cross-reference queries naturally falls out
  - No artificial classify→retrieve→generate split

Safety nets retained outside the ReAct loop:
  - input sanitize (security): runs BEFORE entering the agent
  - output filter:               runs on the agent's final output

Activation:
    export REACT_MODE=true       # then restart rag-api

Default (REACT_MODE unset/false) uses graph.py — production-safe rollback.
"""
from __future__ import annotations
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    SystemMessage,
    ToolMessage,
    BaseMessage,
)
from langchain_core.tools import BaseTool, tool as tool_decorator
from langchain_openai import ChatOpenAI

from ..core.config import RAGConfig
from ..core.factory import ComponentFactory
from ..core.prompts import (
    AGENT_SYSTEM_PROMPT,
    CLASSIFY_SYSTEM_MSG,
    CLASSIFY_PROMPT_TEMPLATE,
    VERIFY_SYSTEM_MSG,
    VERIFY_PROMPT_TEMPLATE,
    REJECTION_MSG,
    SECURITY_MSG,
)
from ..core.input_sanitizer import sanitize
from ..core.output_filter import filter_output
from ..core.audit_logger import AuditLogger
from ..services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

MAX_REACT_RETRIES = 2


def _classify_pre_check(question: str, llm) -> str:
    """Run LLM-based classify before invoking the ReAct agent.

    Returns 'legal' | 'reject' | 'passthrough'. Defaults to 'legal' on
    any error so we never block a query due to a classify hiccup.
    """
    import json as _json
    import re as _re
    try:
        response = llm.invoke([
            SystemMessage(content=CLASSIFY_SYSTEM_MSG),
            HumanMessage(content=CLASSIFY_PROMPT_TEMPLATE.format(question=question)),
        ])
        text = (response.content or "").strip()
        text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.MULTILINE).strip()
        m = _re.search(r"\{[^{}]*\}", text, _re.DOTALL)
        if not m:
            return "legal"
        verdict = _json.loads(m.group(0))
        scope = str(verdict.get("scope", "")).lower().strip()
        if scope in ("legal", "reject", "passthrough"):
            return scope
        return "legal"
    except Exception as e:
        logger.warning(f"[ReAct] pre-classify failed, default legal: {e}")
        return "legal"


def _verify_post_check(question: str, answer: str, llm) -> dict:
    """Run LLM-based verify on the ReAct agent's final answer.

    Returns {"needs_retry": bool, "reason": str}. Defaults to no-retry
    on any error to avoid livelock.
    """
    import json as _json
    import re as _re
    # Fast paths — skip LLM call when obvious
    if len(answer) < 50 or REJECTION_MSG in answer:
        return {"needs_retry": False, "reason": "non_substantive"}
    if any(p in answer for p in ["尚未收錄", "無法提供", "未發現", "未檢索到", "沒有相關"]):
        return {"needs_retry": False, "reason": "honest_no_info"}

    try:
        response = llm.invoke([
            SystemMessage(content=VERIFY_SYSTEM_MSG),
            HumanMessage(content=VERIFY_PROMPT_TEMPLATE.format(
                question=question, answer=answer[:2000],
            )),
        ])
        text = (response.content or "").strip()
        text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.MULTILINE).strip()
        m = _re.search(r"\{[^{}]*\}", text, _re.DOTALL)
        if not m:
            return {"needs_retry": False, "reason": "no_json"}
        verdict = _json.loads(m.group(0))
        return {
            "needs_retry": bool(verdict.get("needs_retry")),
            "reason": str(verdict.get("reason", ""))[:100],
        }
    except Exception as e:
        logger.warning(f"[ReAct] post-verify failed, default pass: {e}")
        return {"needs_retry": False, "reason": "verify_error"}


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton for retrieval service so the tool closure can
# reach it. Built once per workflow construction.
# ──────────────────────────────────────────────────────────────────────


def _build_react_tool(config: RAGConfig) -> BaseTool:
    """Build the `retrieve_legal_docs` tool the agent will call."""
    rag_service = RetrievalService(config)
    max_tokens = config.max_retrieval_tokens
    chars_per_token = 4

    @tool_decorator("retrieve_legal_docs")
    def retrieve_legal_docs(query: str) -> str:
        """Retrieve relevant Chinese statute articles for the given query.

        Use this tool whenever you need to find specific laws, articles,
        or regulations to answer a question. You may call this tool
        multiple times with different queries to gather information from
        different statutes or angles.

        Args:
            query: A focused search string in Traditional Chinese describing
                   what you want to find (can include article numbers like
                   "第46條" or general concepts like "復審期限").
        """
        logger.info(f"[ReAct] retrieve_legal_docs invoked: {query[:80]}")
        try:
            docs = rag_service.query(query)
        except Exception as e:
            logger.error(f"[ReAct] retrieval failed: {e}")
            return f"檢索失敗：{e}"

        if not docs:
            return "（檢索無結果，知識庫中沒有與此查詢相關的條文。）"

        parts: List[str] = []
        tokens_used = 0
        for i, doc in enumerate(docs, 1):
            source = doc.metadata.get("source", "Unknown")
            article = doc.metadata.get("article_id", "")
            header = f"[{i}] 來源: {source}" + (f" ({article})" if article else "") + "\n"
            entry_tokens = (len(header) + len(doc.page_content)) // chars_per_token
            remaining = max_tokens - tokens_used
            if remaining <= 0:
                break
            content = doc.page_content
            if entry_tokens > remaining:
                max_chars = remaining * chars_per_token - len(header)
                if max_chars > 200:
                    content = content[:max_chars] + "\n... (截斷)"
                else:
                    break
            parts.append(f"{header}{content}")
            tokens_used += (len(header) + len(content)) // chars_per_token
        return "\n\n".join(parts) if parts else "（檢索無結果。）"

    return retrieve_legal_docs


# ──────────────────────────────────────────────────────────────────────
# Build the ReAct agent (uses langgraph.prebuilt.create_react_agent)
# ──────────────────────────────────────────────────────────────────────

_REACT_CACHE: dict = {}


def _create_factory(config: RAGConfig) -> ComponentFactory:
    return ComponentFactory(config)


def create_react_workflow(
    config: RAGConfig,
    *,
    llm: Optional[ChatOpenAI] = None,
):
    """Build (and cache) the LangGraph ReAct agent.

    Returns a callable compatible with the classic workflow's invoke()
    signature so the API layer can swap them via REACT_MODE env var.
    """
    from langgraph.prebuilt import create_react_agent

    cache_key = hash(config)
    if llm is None and cache_key in _REACT_CACHE:
        return _REACT_CACHE[cache_key]

    if llm is None:
        factory = _create_factory(config)
        llm = factory.create_llm(temperature=config.temperature)

    tool = _build_react_tool(config)

    agent = create_react_agent(
        model=llm,
        tools=[tool],
        prompt=AGENT_SYSTEM_PROMPT,
    )

    if llm is not None:
        _REACT_CACHE[cache_key] = agent
    logger.info("Compiled and cached ReAct workflow")
    return agent


# ──────────────────────────────────────────────────────────────────────
# Public entrypoints — mirror graph.py's run_query / astream_query
# ──────────────────────────────────────────────────────────────────────


def run_react_query(
    question: str,
    config: RAGConfig,
    *,
    llm: Optional[ChatOpenAI] = None,
    messages: Optional[list] = None,
    session_id: str = "",
    client_ip: str = "",
    audit_context: Optional[Dict[str, Any]] = None,
    wrapper_mode: bool = False,
) -> dict:
    """Synchronous execution. Returns a state-like dict mirroring graph.py.

    Output keys (so api.py log_query can read uniformly):
      generation, messages, retrieved_sources, citation_count, tokens_used,
      retry_count, scope

    session_id is logged with any security alert so the alert traces back
    to the originating request (ISO 42001 A.9).
    """
    # ── Security check FIRST (never inside the agent) ──────────────────
    # wrapper_mode 由 graph.run_query 傳入，與第二道 sanitizer 及前置 sanitizer 一致。
    san = sanitize(question, is_wrapper=bool(wrapper_mode))
    if san.blocked:
        logger.warning(f"[ReAct] blocked: {san.threat_type}")
        try:
            AuditLogger(config.audit_log_dir).log_security_alert(
                session_id=session_id or "unknown",
                user_query=question,
                threat_type=san.threat_type,
                reason=san.reason,
                stage="input",
                action_taken="blocked",
                user_notified=True,
                detection_method="input_sanitizer",
                client_ip=client_ip,
                **(audit_context or {}),
            )
        except Exception:
            pass
        return {
            "generation": SECURITY_MSG,
            "messages": [AIMessage(content=SECURITY_MSG)],
            "scope": "security_block",
            "retrieved_sources": [],
            "citation_count": 0,
            "tokens_used": 0,
            "retry_count": 0,
        }

    # ── Build LLM + agent (needed for both classify and ReAct invocation) ─
    if llm is None:
        llm = ComponentFactory(config).create_llm(temperature=config.temperature)
    agent = create_react_workflow(config, llm=llm)

    # ── Pre-ReAct classify — avoid spinning ReAct for OOS queries ───────
    scope = _classify_pre_check(question, llm)
    if scope == "reject":
        logger.info(f"[ReAct] pre-classify: REJECT, skip agent")
        return {
            "generation": REJECTION_MSG,
            "messages": [AIMessage(content=REJECTION_MSG)],
            "scope": "reject",
            "retrieved_sources": [],
            "citation_count": 0,
            "tokens_used": 0,
            "retry_count": 0,
        }
    if scope == "passthrough":
        # System task — just let the LLM handle it directly, no retrieval
        logger.info(f"[ReAct] pre-classify: PASSTHROUGH, direct LLM")
        try:
            resp = llm.invoke([HumanMessage(content=question)])
            return {
                "generation": resp.content,
                "messages": [AIMessage(content=resp.content)],
                "scope": "passthrough",
                "retrieved_sources": [],
                "citation_count": 0,
                "tokens_used": int((getattr(resp, "usage_metadata", None) or {}).get("total_tokens", 0) or 0),
                "retry_count": 0,
            }
        except Exception as e:
            logger.error(f"[ReAct] passthrough failed: {e}")
            # fall through to ReAct path

    # ── ReAct main loop with post-verify retry ──────────────────────────
    initial_messages = messages or [HumanMessage(content=question)]
    if not initial_messages or not isinstance(initial_messages[-1], HumanMessage):
        initial_messages = list(initial_messages) + [HumanMessage(content=question)]

    retry_count = 0
    last_feedback = ""
    while True:
        # Inject retry feedback if this is a retry attempt
        if last_feedback:
            initial_messages = list(initial_messages) + [
                SystemMessage(content=(
                    f"【系統強制糾正】上次回答未通過審查：{last_feedback}\n"
                    f"請更精準地回應使用者的核心問題，並確實引用條文。"
                ))
            ]

        logger.info(f"[ReAct] invoking agent (attempt {retry_count + 1}) for: {question[:80]}")
        result = agent.invoke({"messages": initial_messages})
        out_messages: List[BaseMessage] = result.get("messages", [])

        # Final AI message is the answer
        answer = ""
        for m in reversed(out_messages):
            if isinstance(m, AIMessage) and m.content:
                answer = m.content
                break

        # Post-verify
        if retry_count >= MAX_REACT_RETRIES:
            logger.info(f"[ReAct] retry budget exhausted, accepting answer")
            break
        verdict = _verify_post_check(question, answer, llm)
        if not verdict["needs_retry"]:
            logger.info(f"[ReAct] verify passed: {verdict['reason']}")
            break
        logger.info(f"[ReAct] verify says retry: {verdict['reason']}")
        retry_count += 1
        last_feedback = verdict["reason"]

    # Output filter
    filtered = filter_output(answer)
    if filtered.redacted:
        logger.warning(f"[ReAct] output filter redacted: {filtered.findings}")

    # Audit fields — derive from message stream
    retrieved_sources: List[str] = []
    tokens_used = 0
    tool_calls = 0
    import re as _re
    article_re = _re.compile(r"第\s*\d+\s*條")

    for m in out_messages:
        if isinstance(m, ToolMessage):
            tool_calls += 1
            # ToolMessage content has "[N] 來源: 軍人權益事件處理法.md (第3條)\n..."
            # Pull "<filename>.md (第N條)" → "<filename>.md#第N條"
            for match in _re.finditer(r"來源: ([^\s]+\.md)(?:\s*\(([^)]+)\))?", m.content or ""):
                src = match.group(1)
                article = (match.group(2) or "").strip()
                src_id = f"{src}#{article}" if article and article not in ("preamble", "whole_document") else src
                if src_id not in retrieved_sources:
                    retrieved_sources.append(src_id)
        if isinstance(m, AIMessage):
            usage = getattr(m, "usage_metadata", None) or {}
            if isinstance(usage, dict):
                tokens_used += int(usage.get("total_tokens", 0) or 0)

    citation_count = len(article_re.findall(filtered.text))

    logger.info(
        "[ReAct] done: %d tool calls, %d sources, %d tokens, %d citations",
        tool_calls, len(retrieved_sources), tokens_used, citation_count,
    )

    return {
        "generation": filtered.text,
        "messages": [AIMessage(content=filtered.text)],
        "scope": "verified",
        "retrieved_sources": retrieved_sources,
        "citation_count": citation_count,
        "tokens_used": tokens_used,
        # retry_count reflects post-verify retries (NOT tool-call hops)
        "retry_count": retry_count,
        "output_redacted": filtered.redacted,
    }


async def astream_react_query(
    question: str,
    config: RAGConfig,
    *,
    llm: Optional[ChatOpenAI] = None,
    messages: Optional[list] = None,
    session_id: str = "",
    client_ip: str = "",
    audit_context: Optional[Dict[str, Any]] = None,
    wrapper_mode: bool = False,
) -> AsyncIterator[str]:
    """Streaming version (token-level)."""
    # Security check (same as sync path) — log alert with session_id
    # wrapper_mode 由 graph.astream_query 傳入，與同步路徑保持一致。
    san = sanitize(question, is_wrapper=bool(wrapper_mode))
    if san.blocked:
        try:
            AuditLogger(config.audit_log_dir).log_security_alert(
                session_id=session_id or "unknown",
                user_query=question,
                threat_type=san.threat_type,
                reason=san.reason,
                stage="input",
                action_taken="blocked",
                user_notified=True,
                detection_method="input_sanitizer",
                client_ip=client_ip,
                **(audit_context or {}),
            )
        except Exception:
            pass
        yield SECURITY_MSG
        return

    agent = create_react_workflow(config, llm=llm)

    initial_messages = messages or [HumanMessage(content=question)]
    if not initial_messages or not isinstance(initial_messages[-1], HumanMessage):
        initial_messages = list(initial_messages) + [HumanMessage(content=question)]

    try:
        async for event in agent.astream_events(
            {"messages": initial_messages}, version="v2"
        ):
            if event["event"] == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if getattr(chunk, "content", None):
                    yield chunk.content
    except Exception as e:
        logger.error(f"[ReAct] streaming failed: {e}")
        yield f"抱歉，ReAct 流程錯誤：{e}"


__all__ = [
    "create_react_workflow",
    "run_react_query",
    "astream_react_query",
]
