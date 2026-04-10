"""
RAG System Configuration

Centralized configuration management for the RAG system.
All hardcoded values should be defined here as constants or configurable parameters.
"""

from typing import Optional
from dataclasses import dataclass
from pathlib import Path
import os


# ============================================================================
# RETRIEVAL CONFIGURATION
# ============================================================================

# Default number of documents to retrieve
DEFAULT_TOP_K = 5

# Maximum number of documents to retrieve (safety limit)
MAX_TOP_K = 20

# Minimum number of documents to retrieve
MIN_TOP_K = 1

# Default content truncation length for retrieved documents
DEFAULT_CONTENT_MAX_LENGTH = 800

# Maximum content length (safety limit)
MAX_CONTENT_LENGTH = 2000

# Ingestion settings
DEFAULT_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP = 200
DEFAULT_CHILD_CHUNK_SIZE = 800
DEFAULT_DOCSTORE_PATH = Path("./data/processed/docstore")

# Reranking & token budget
DEFAULT_RERANK_TOP_N = 3
DEFAULT_MAX_RETRIEVAL_TOKENS = 3000

# Agent settings
DEFAULT_SUMMARY_THRESHOLD = 10

# Conversation persistence
DEFAULT_CONVERSATION_HISTORY_LIMIT = 50

# Audit logging
DEFAULT_AUDIT_LOG_DIR = Path("./data/audit_logs")


# ============================================================================
# MODEL CONFIGURATION
# ============================================================================

DEFAULT_EMBED_MODEL = "nvidia/nv-embed-v2"
DEFAULT_CHAT_MODEL = "openai/gpt-oss-20b"
DEFAULT_TEMPERATURE = 0  # Deterministic by default


# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================

# Default collection name if not specified
DEFAULT_COLLECTION = "laws"


# ============================================================================
# RAG CONFIGURATION DATACLASS
# ============================================================================

@dataclass
class RAGConfig:
    """
    Configuration container for RAG system.

    This class holds all configurable parameters for the RAG system,
    making it easy to pass configuration around and override defaults.
    """
    # Retrieval settings
    top_k: int = DEFAULT_TOP_K
    content_max_length: int = DEFAULT_CONTENT_MAX_LENGTH
    summary_top_k: int = 10
    detail_top_k: int = 1
    rerank_top_n: int = DEFAULT_RERANK_TOP_N
    max_retrieval_tokens: int = DEFAULT_MAX_RETRIEVAL_TOKENS

    # Ingestion settings
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    child_chunk_size: int = DEFAULT_CHILD_CHUNK_SIZE
    docstore_path: Path = DEFAULT_DOCSTORE_PATH

    # Database settings
    conn_string: Optional[str] = None
    default_collection: str = DEFAULT_COLLECTION

    # Model settings
    embed_model: str = DEFAULT_EMBED_MODEL
    chat_model: str = DEFAULT_CHAT_MODEL
    temperature: float = DEFAULT_TEMPERATURE

    # API settings
    embed_api_base: Optional[str] = None
    llm_api_base: Optional[str] = None  # Separate base for LLM
    embed_api_key: Optional[str] = None

    # SSL settings — default True; set VERIFY_SSL=false to disable (dev only)
    verify_ssl: bool = True

    # Agent settings
    summary_threshold: int = DEFAULT_SUMMARY_THRESHOLD

    # Conversation persistence
    conversation_history_limit: int = DEFAULT_CONVERSATION_HISTORY_LIMIT

    # Audit logging
    audit_log_dir: Path = DEFAULT_AUDIT_LOG_DIR

    def __post_init__(self):
        """Validate configuration values and load from environment if not set."""
        # Validate top_k
        if not MIN_TOP_K <= self.top_k <= MAX_TOP_K:
            raise ValueError(
                f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}, got {self.top_k}"
            )

        # Validate content_max_length
        if not 100 <= self.content_max_length <= MAX_CONTENT_LENGTH:
            raise ValueError(
                f"content_max_length must be between 100 and {MAX_CONTENT_LENGTH}, "
                f"got {self.content_max_length}"
            )

        # Load from environment if not provided
        if not self.conn_string:
            self.conn_string = os.environ.get("PGVECTOR_URL")

        if not self.embed_api_base:
            self.embed_api_base = os.environ.get("EMBED_API_BASE")

        if not self.llm_api_base:
            # Fallback to embed_api_base if llm_api_base is not explicitly set
            self.llm_api_base = os.environ.get("LLM_API_BASE", self.embed_api_base)

        if not self.embed_api_key:
            self.embed_api_key = os.environ.get("EMBED_API_KEY")

    @classmethod
    def from_env(cls) -> "RAGConfig":
        """Create configuration from environment variables."""
        embed_base = os.environ.get("EMBED_API_BASE")
        llm_base = os.environ.get("LLM_API_BASE", embed_base) # Fallback

        # 從環境變數讀取 top_k 和 chunk_size
        top_k = int(os.environ.get("TOP_K", DEFAULT_TOP_K))
        chunk_size = int(os.environ.get("CHUNK_SIZE", DEFAULT_CHUNK_SIZE))

        verify_ssl_env = os.environ.get("VERIFY_SSL", "true").lower()
        verify_ssl = verify_ssl_env not in ("false", "0", "no")

        return cls(
            conn_string=os.environ.get("PGVECTOR_URL"),
            embed_api_base=embed_base,
            llm_api_base=llm_base,
            embed_api_key=os.environ.get("EMBED_API_KEY"),
            embed_model=os.environ.get("EMBED_MODEL_NAME", DEFAULT_EMBED_MODEL),
            chat_model=os.environ.get("CHAT_MODEL_NAME", DEFAULT_CHAT_MODEL),
            top_k=top_k,
            chunk_size=chunk_size,
            rerank_top_n=int(os.environ.get("RERANK_TOP_N", DEFAULT_RERANK_TOP_N)),
            max_retrieval_tokens=int(os.environ.get("MAX_RETRIEVAL_TOKENS", DEFAULT_MAX_RETRIEVAL_TOKENS)),
            verify_ssl=verify_ssl,
        )

    def validate(self) -> None:
        """Validate that required configuration is present."""
        if not self.conn_string:
            raise ValueError("Database connection string is required")
        if not self.embed_api_base:
            raise ValueError("Embedding API base URL is required")
        if not self.llm_api_base:
            raise ValueError("LLM API base URL is required")
        if not self.embed_api_key:
            raise ValueError("API key is required")

    def __hash__(self) -> int:
        """Hash based on key config fields for reliable caching."""
        return hash((
            self.conn_string, self.embed_api_base, self.llm_api_base,
            self.embed_model, self.chat_model, self.temperature,
            self.top_k, self.rerank_top_n,
        ))
