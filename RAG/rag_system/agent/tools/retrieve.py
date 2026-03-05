"""RAG retrieval tool for the LangGraph agent.

Wraps RetrievalService with token-budget-aware formatting so the agent
never gets more than MAX_RETRIEVAL_TOKENS worth of context injected.
"""
from typing import Type
import logging

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from ...services.retrieval import RetrievalService
from ...core.config import RAGConfig

logger = logging.getLogger(__name__)

# Token estimation: ~4 chars per token for mixed CJK/ASCII
_CHARS_PER_TOKEN = 4


class RetrieveInput(BaseModel):
    query: str = Field(description="The query string to search for in the legal document database.")


class RAGRetrieveTool(BaseTool):
    name: str = "retrieve_legal_docs"
    description: str = (
        "Retrieve relevant legal documents and context based on the user's query. "
        "Use this tool when you need to find laws, regulations, or legal context."
    )
    args_schema: Type[BaseModel] = RetrieveInput
    rag_service: RetrievalService
    max_retrieval_tokens: int = 3000

    def _run(self, query: str) -> str:
        docs = self.rag_service.query(query)
        if not docs:
            return "No relevant documents found."
        
        # Format docs with token budget enforcement
        result = []
        tokens_used = 0

        for i, doc in enumerate(docs, 1):
            source = doc.metadata.get("source", "Unknown")
            content = doc.page_content

            # Estimate tokens for this doc
            header = f"[{i}] Source: {source}\nContent: "
            entry_tokens = (len(header) + len(content)) // _CHARS_PER_TOKEN

            # Check budget
            remaining = self.max_retrieval_tokens - tokens_used
            if remaining <= 0:
                logger.info(f"Token budget exhausted after {i-1} docs")
                break

            # Truncate content if it would exceed budget
            if entry_tokens > remaining:
                max_chars = remaining * _CHARS_PER_TOKEN - len(header)
                if max_chars > 200:  # Only include if meaningful content remains
                    content = content[:max_chars] + "\n... (因 token 預算限制已截斷)"
                    logger.info(f"Doc {i} truncated to fit token budget")
                else:
                    logger.info(f"Skipping doc {i}, not enough remaining budget")
                    break

            entry = f"{header}{content}\n"
            result.append(entry)
            tokens_used += len(entry) // _CHARS_PER_TOKEN

        logger.info(f"Returning {len(result)} docs, ~{tokens_used} tokens used")
        return "\n".join(result)


def create_rag_tool(config: RAGConfig) -> RAGRetrieveTool:
    service = RetrievalService(config)
    return RAGRetrieveTool(
        rag_service=service,
        max_retrieval_tokens=config.max_retrieval_tokens,
    )