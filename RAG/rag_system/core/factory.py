"""
Component Factory

Responsible for initializing and assembling core infrastructure components:
- LLMs (ChatOpenAI)
- Embeddings (OpenAIEmbeddings)
- VectorStore (PGVector)
- DocStore (LocalFileStore + Encoder)

This isolates resource creation from business logic, facilitating dependency injection and testing.
"""
import logging
from pathlib import Path
import httpx

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_postgres.vectorstores import PGVector
from langchain_classic.storage import LocalFileStore, EncoderBackedStore
from langchain_core.load import dumps, loads

import warnings
from langchain_core._api.beta_decorator import LangChainBetaWarning
warnings.filterwarnings("ignore", category=LangChainBetaWarning)

from .config import RAGConfig

logger = logging.getLogger(__name__)

class ComponentFactory:
    """
    Factory for creating RAG infrastructure components.
    """
    
    def __init__(self, config: RAGConfig):
        self.config = config
        self._http_client = httpx.Client(
            verify=config.verify_ssl,
            timeout=httpx.Timeout(60.0)
        )

    def create_embeddings(self) -> OpenAIEmbeddings:
        """Create configured OpenAI Embeddings client."""
        return OpenAIEmbeddings(
            model=self.config.embed_model,
            openai_api_base=self.config.embed_api_base,
            openai_api_key=self.config.embed_api_key,
            check_embedding_ctx_length=False,
            http_client=self._http_client,
            chunk_size=10
        )

    def create_llm(self, temperature: float = 0) -> ChatOpenAI:
        """Create configured ChatOpenAI client."""
        return ChatOpenAI(
            model=self.config.chat_model,
            openai_api_base=self.config.llm_api_base,
            openai_api_key=self.config.embed_api_key,
            temperature=temperature,
            http_client=self._http_client
        )

    def create_vectorstore(self, embeddings: OpenAIEmbeddings) -> PGVector:
        """Create PGVector instance connected to the database."""
        # Fix DB Connection String (psycopg2 compat)
        conn_str = self.config.conn_string
        if conn_str and "postgresql+psycopg2://" in conn_str:
            conn_str = conn_str.replace("postgresql+psycopg2://", "postgresql://")

        return PGVector(
            embeddings=embeddings,
            collection_name=f"{self.config.default_collection}_vectors",
            connection=conn_str,
            use_jsonb=True,
        )

    def create_docstore(self, path: str = None) -> EncoderBackedStore:
        """Create File-based Document Store with JSON serialization."""
        if path:
            docstore_path = Path(path)
        else:
            docstore_path = self.config.docstore_path

        docstore_path.mkdir(parents=True, exist_ok=True)
        
        raw_store = LocalFileStore(str(docstore_path))
        
        def _dumps(x):
            return dumps(x).encode('utf-8')
            
        def _loads(x):
            return loads(x.decode('utf-8'))

        return EncoderBackedStore(
            store=raw_store,
            key_encoder=lambda x: x,
            value_serializer=_dumps,
            value_deserializer=_loads
        )
