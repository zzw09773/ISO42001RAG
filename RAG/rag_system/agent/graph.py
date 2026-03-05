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
from typing import AsyncIterator, Optional, List

from langgraph.graph import END, StateGraph
from langchain_openai import ChatOpenAI

from ..core.config import RAGConfig
from ..core.factory import ComponentFactory
from .state import GraphState
from .nodes import (
    create_classify_node,
    create_reject_node,
    create_passthrough_node,
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
    classify = create_classify_node()
    reject = create_reject_node()
    passthrough = create_passthrough_node(llm)
    retrieve = create_retrieve_node(config)
    generate = create_generate_node(llm, config)
    verify = create_verify_node()

    # Build Graph
    workflow = StateGraph(GraphState)

    workflow.add_node("classify", classify)
    workflow.add_node("reject", reject)
    workflow.add_node("passthrough", passthrough)
    workflow.add_node("retrieve", retrieve)
    workflow.add_node("generate", generate)
    workflow.add_node("verify", verify)

    # Edges
    workflow.set_entry_point("classify")

    workflow.add_conditional_edges(
        "classify",
        _route_after_classify,
        {"retrieve": "retrieve", "reject": "reject", "passthrough": "passthrough"},
    )
    workflow.add_edge("reject", END)
    workflow.add_edge("passthrough", END)
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


def run_query(
    question: str,
    config: RAGConfig,
    *,
    llm: Optional[ChatOpenAI] = None,
    messages: Optional[list] = None,
):
    """Execute a single query through the workflow (synchronous)."""
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
    }

    logger.info(f"Running workflow for question: {question}")
    return workflow.invoke(state, config={"recursion_limit": 50})


async def astream_query(
    question: str,
    config: RAGConfig,
    *,
    llm: Optional[ChatOpenAI] = None,
    messages: Optional[list] = None,
) -> AsyncIterator[str]:
    """
    Stream the response asynchronously intercepting LLM stream events.
    Uses LangGraph's astream_events API for true token-level streaming.
    """
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
    }

    logger.info(f"Streaming workflow for question: {question}")

    # Use astream_events to intercept token generation
    try:
        generation_count = 0
        async for event in workflow.astream_events(state, config={"recursion_limit": 50}, version="v2"):
            node = event.get("metadata", {}).get("langgraph_node")
            
            # If we enter a new 'generate' node execution, increment count
            if event["event"] == "on_chat_model_start" and node == "generate":
                generation_count += 1
                if generation_count > 1:
                    yield f"\n\n> ⚠️ **系統檢測到回答需要補強，正在進行第 {generation_count} 次深度檢索與生成...**\n\n"
                    
            if event["event"] == "on_chat_model_stream":
                # Ensure we only stream from the generate or reject nodes, not tool calls
                if node in ["generate", "reject", "passthrough"]:
                    chunk = event["data"]["chunk"]
                    if getattr(chunk, "content", None):
                        yield chunk.content
    except Exception as e:
        logger.error(f"Streaming failed: {e}")
        yield f"抱歉，串流回覆時發生錯誤：{str(e)}"


__all__ = [
    "create_rag_workflow",
    "run_query",
    "astream_query",
]
