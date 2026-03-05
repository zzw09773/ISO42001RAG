# RAG System Optimization Report & Implementation Code

This document contains the complete, optimized source code for the RAG (Retrieval-Augmented Generation) system, specifically focusing on the **Two-Stage Retrieval Optimization** (Summary Retrieval + LLM Reranking).

## 1. Configuration (`rag_system/config.py`)

Adds `summary_top_k` (10) and `detail_top_k` (1) to control the retrieval funnel.

```python
"""
RAG System Configuration

Centralized configuration management for the RAG system.
All hardcoded values should be defined here as constants or configurable parameters.
"""

from typing import Optional
from dataclasses import dataclass
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

    # SSL settings
    verify_ssl: bool = False

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

        return cls(
            conn_string=os.environ.get("PGVECTOR_URL"),
            embed_api_base=embed_base,
            llm_api_base=llm_base,
            embed_api_key=os.environ.get("EMBED_API_KEY"),
            embed_model=os.environ.get("EMBED_MODEL_NAME", DEFAULT_EMBED_MODEL),
            chat_model=os.environ.get("CHAT_MODEL_NAME", DEFAULT_CHAT_MODEL),
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
```

## 2. RAG Service (`rag_system/rag_service.py`)

Implements the core logic:
1.  `summary_top_k=10` retrieval from PGVector.
2.  LLM Reranking with robust prompt and regex parsing.
3.  Fetching Parent Document from `LocalFileStore`.
4.  `clear_index` logic to handle DB tables properly.

```python
"""
RAG Service

A unified service for document indexing and retrieval using LangChain's ParentDocumentRetriever.
Replaces the previous over-engineered hierarchical chunking system.
"""
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
import httpx

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_postgres.vectorstores import PGVector

# LangChain 1.0+ moved these to langchain_classic for backwards compatibility
from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_classic.storage import LocalFileStore, EncoderBackedStore

from langchain_core.load import dumps, loads
import shutil

from .config import RAGConfig

logger = logging.getLogger(__name__)

class RAGService:
    """
    Unified RAG Service handling both Indexing and Retrieval.
    Wraps LangChain's ParentDocumentRetriever.
    """

    def __init__(self, config: RAGConfig):
        self.config = config
        self._init_components()

    def _init_components(self):
        """Initialize LangChain components."""
        
        # 0. Fix DB Connection String (psycopg2 compat)
        # LangChain PGVector uses psycopg3/sqlalchemy, but best to ensure standard postgresql:// format
        conn_str = self.config.conn_string
        if conn_str and "postgresql+psycopg2://" in conn_str:
            conn_str = conn_str.replace("postgresql+psycopg2://", "postgresql://")

        # 1. Embeddings with SSL Bypass
        # Use httpx client to control SSL verification if needed
        http_client = httpx.Client(verify=self.config.verify_ssl)
        
        self.embeddings = OpenAIEmbeddings(
            model=self.config.embed_model,
            openai_api_base=self.config.embed_api_base,
            openai_api_key=self.config.embed_api_key,
            check_embedding_ctx_length=False, # Disable check for custom models
            http_client=http_client,
            chunk_size=10 # Reduce batch size to avoid 504 Timeouts
        )
        
        # 1.1 LLM for Reranking (Optimized Retrieval)
        self.llm = ChatOpenAI(
            model=self.config.chat_model,
            openai_api_base=self.config.llm_api_base,
            openai_api_key=self.config.embed_api_key,
            temperature=0,
            http_client=http_client
        )

        # 2. Vector Store (for Child Chunks)
        # Using standard LangChain PGVector
        self.vectorstore = PGVector(
            embeddings=self.embeddings,
            collection_name=f"{self.config.default_collection}_vectors",
            connection=conn_str,
            use_jsonb=True,
        )

        # 3. Document Store (for Parent Chunks)
        # Using LocalFileStore BACKED by an Encoder (to handle Document objects)
        docstore_path = Path("./data/processed/docstore")
        docstore_path.mkdir(parents=True, exist_ok=True)
        
        # Define the raw byte store
        raw_store = LocalFileStore(str(docstore_path))
        
        # Wrap it with EncoderBackedStore using LangChain's serializer (dumps/loads)
        # This allows us to store Documents as JSON-like bytes
        def _dumps(x):
            return dumps(x).encode('utf-8')
            
        def _loads(x):
            return loads(x.decode('utf-8'))

        self.docstore = EncoderBackedStore(
            store=raw_store,
            key_encoder=lambda x: x, # Use ID as filename directly
            value_serializer=_dumps,
            value_deserializer=_loads
        )

        # 4. Text Splitters
        # Parent: Large chunks (preserve context)
        self.parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000,
            chunk_overlap=200,
            separators=["\n第", "\n\n", "\n", "。", " ", ""]
        ) # Optimized for Chinese Law
        
        # Child: Small chunks (optimized for embedding search)
        self.child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800, # Requested size
            chunk_overlap=100,
        )

        # 5. Retriever
        self.retriever = ParentDocumentRetriever(
            vectorstore=self.vectorstore,
            docstore=self.docstore,
            child_splitter=self.child_splitter,
            parent_splitter=self.parent_splitter,
            search_kwargs={"k": self.config.top_k}
        )

    def index_file(self, file_path: Path) -> int:
        """
        Index a single file.
        Returns number of parent chunks added.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        logger.info(f"Indexing file: {file_path.name}")
        
        try:
            # Read content
            content = file_path.read_text(encoding='utf-8')
            
            # Create generic Document
            doc = Document(
                page_content=content,
                metadata={
                    "source": file_path.name,
                    "file_path": str(file_path)
                }
            )

            # Add to retriever (handles splitting & storage)
            self.retriever.add_documents([doc], ids=None)
            
            logger.info(f"Successfully indexed {file_path.name}")
            return 1

        except Exception as e:
            logger.error(f"Failed to index {file_path.name}: {e}")
            raise

    def index_directory(self, dir_path: Path, pattern: str = "*.*") -> Dict[str, int]:
        """
        Index all files in a directory matching pattern.
        """
        results = {"success": 0, "failed": 0}
        
        files = list(dir_path.glob(pattern))
        logger.info(f"Found {len(files)} files in {dir_path}")

        for f in files:
            if f.is_file() and f.suffix.lower() in ['.txt', '.md', '.py', '.json']: # Basic filter
                try:
                    self.index_file(f)
                    results["success"] += 1
                except Exception as e:
                    logger.error(f"Error indexing {f.name}: {e}")
                    results["failed"] += 1
        
        return results

    def query(self, question: str) -> List[Document]:
        """
        Retrieve relevant documents using Two-Stage Optimization.
        
        Stage 1: Retrieve top_k (default 3) summaries (Child Documents).
        Stage 2: Use LLM to select the most relevant summary.
        Stage 3: Retrieve the full content (Parent Document) for the winner.
        
        Returns list of PARENT documents (usually 1).
        """
        logger.info(f"Querying (Two-Stage): {question}")
        
        # Stage 1: Get Summaries (Child Chunks) directly from VectorStore
        try:
            summaries = self.vectorstore.similarity_search(
                question, 
                k=self.config.summary_top_k
            )
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []
            
        if not summaries:
            return []
            
        # If only 1 summary found, skip LLM
        if len(summaries) == 1:
            doc_id = summaries[0].metadata.get("doc_id")
            if doc_id:
                parent = self.docstore.mget([doc_id])[0]
                return [parent] if parent else []
            return []

        # Stage 2: LLM Selection
        # Construct options string
        options_text = ""
        for i, doc in enumerate(summaries):
            # Truncate for prompt safety
            content_preview = doc.page_content[:300].replace("\n", " ")
            options_text += f"[{i+1}] ...{content_preview}...\n\n"
            
        logger.info(f"LLM Rerank Candidates:\n{options_text}")

        prompt = (
            f"Target Query: {question}\n\n"
            f"Candidates:\n{options_text}\n"
            "Analyze the candidates above. Identify which candidate is most likely to come from the correct document "
            "or context to answer the query, even if the exact answer is not visible in the snippet."
            "Return ONLY the candidate number (e.g., 6). If none are relevant, return 0."
        )
        
        try:
            response = self.llm.invoke([
                SystemMessage(content="You are a precise legal retrieval assistant."),
                HumanMessage(content=prompt)
            ])
            
            selection = response.content.strip()
            logger.info(f"LLM Raw Response: {selection}")

            # Simple parsing
            import re
            match = re.search(r'\\b(\\d+)\\b', selection)
            
            if match:
                raw_idx = int(match.group(1))
                
                # Handle 0 (None found)
                if raw_idx == 0:
                    logger.warning("LLM indicated no relevant documents found (0). Fallback to top 1.")
                    if summaries:
                        best_summary = summaries[0]
                    else:
                         return []
                else:
                    # Handle 1-based index
                    idx = raw_idx - 1
                    if 0 <= idx < len(summaries):
                        best_summary = summaries[idx]
                        logger.info(f"LLM selected candidate #{raw_idx}")
                    else:
                        logger.warning(f"LLM selected out-of-range index: {raw_idx}. Fallback to top 1.")
                        best_summary = summaries[0]

                # Stage 3: Fetch Parent
                doc_id = best_summary.metadata.get("doc_id")
                if doc_id:
                    parents = self.docstore.mget([doc_id])
                    return [p for p in parents if p]
            else:
                logger.warning(f"LLM did not return a valid index: {selection}. Fallback to top 1.")
                # Fallback to top 1
                doc_id = summaries[0].metadata.get("doc_id")
                if doc_id:
                    parents = self.docstore.mget([doc_id])
                    return [p for p in parents if p]
                    
        except Exception as e:
            logger.error(f"Reranking failed: {e}. Fallback to top 1.")
            doc_id = summaries[0].metadata.get("doc_id")
            if doc_id:
                parents = self.docstore.mget([doc_id])
                return [p for p in parents if p]

        return []

    def clear_index(self):
        """
        Clear all data from vectorstore and docstore.
        Warning: Destructive!
        """
        logger.warning("Clearing RAG index...")
        # Clear PGVector
        self.vectorstore.drop_tables() 
        self.vectorstore.create_tables_if_not_exists()
        
        # Clear FileStore
        docstore_path = Path("./data/processed/docstore")
        if docstore_path.exists():
            shutil.rmtree(docstore_path)
            docstore_path.mkdir()
            
        logger.info("Index cleared.")
```

## 3. Indexing Script (`reindex_script.py`)

Ensures a clean slate by clearing index and re-initializing connection.

```python
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
repo_root = Path(__file__).resolve().parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from rag_system.config import RAGConfig
from rag_system.rag_service import RAGService

def main():
    # Load config
    load_dotenv(override=True)
    try:
        config = RAGConfig.from_env()
        config.validate()
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)

    print("Initializing RAG Service (ParentDocumentRetriever)...")
    
    # Initialize unified service
    rag_service = RAGService(config)

    # Define data directory
    data_dir = repo_root / "data/converted_md"
    
    if not data_dir.exists():
        print(f"Error: Data directory not found at {data_dir}")
        sys.exit(1)

    print(f"Scanning for Markdown files in {data_dir}...")
    
    # Clear existing index (Optional - uncomment if you want a fresh start)
    print("Clearing existing index...")
    rag_service.clear_index()
    
    # Re-initialize service to ensure tables are recreated and connections are fresh
    print("Re-initializing service...")
    rag_service = RAGService(config)
    
    # Index directory
    results = rag_service.index_directory(data_dir, pattern="*.md")
    
    print("\nIndexing Summary:")
    print(f"  Success: {results['success']}")
    print(f"  Failed:  {results['failed']}")

if __name__ == "__main__":
    main()
```