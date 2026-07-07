"""
Component Factory

Responsible for initializing and assembling core infrastructure components:
- LLMs (ChatOpenAI)
- Embeddings (NVEmbedEmbeddings — instruction-conditioned wrapper)
- VectorStore (PGVector)
- DocStore (LocalFileStore + Encoder)

This isolates resource creation from business logic, facilitating dependency injection and testing.
"""
import logging
from pathlib import Path
import httpx

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.embeddings import Embeddings
from langchain_postgres.vectorstores import PGVector
from langchain_classic.storage import LocalFileStore, EncoderBackedStore
from langchain_core.load import dumps, loads

import warnings
from langchain_core._api.beta_decorator import LangChainBetaWarning
warnings.filterwarnings("ignore", category=LangChainBetaWarning)

from .config import RAGConfig

logger = logging.getLogger(__name__)


# =============================================================================
# NV-Embed-v2 instruction-conditioned wrapper
# =============================================================================
#
# NV-Embed-v2 is an instruction-tuned embedding model. To unlock its full
# retrieval quality, queries should be prefixed with a task description while
# passages remain unmodified. This is asymmetric encoding by design.
#
# Reference: https://huggingface.co/nvidia/NV-Embed-v2 (model card example)
#
# Verified on this deployment's GPU gateway (2026-05-27):
#   cosine(embed(q), embed(prefix+q)) = 0.6182
#   → gateway honors the instruction prefix
# =============================================================================

NV_EMBED_QUERY_INSTRUCTION = (
    "Instruct: Given a Chinese legal query, retrieve the statute articles "
    "that most directly answer the query.\nQuery: "
)


class NVEmbedEmbeddings(Embeddings):
    """LangChain-compatible Embeddings wrapper that applies the NV-Embed
    instruction prefix to queries while leaving passages (documents) untouched.

    Falls through to a standard OpenAI-compatible /v1/embeddings endpoint
    so it works with both:
      - this deployment's GPU HTTPS gateway, and
      - the local embed-proxy (Triton gRPC bridge).

    Why a custom class instead of OpenAIEmbeddings:
      OpenAIEmbeddings sends the same `input` field for both query and passage
      encoding, which discards NV-Embed's asymmetric design. This class
      overrides embed_query() to prepend the instruction.
    """

    def __init__(
        self,
        model: str,
        api_base: str,
        api_key: str,
        http_client: httpx.Client,
        query_instruction: str = NV_EMBED_QUERY_INSTRUCTION,
        chunk_size: int = 10,
    ):
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.http_client = http_client
        self.query_instruction = query_instruction
        self.chunk_size = chunk_size

    # ── Internal HTTP call ────────────────────────────────────────────────
    def _call(self, texts: list[str]) -> list[list[float]]:
        """Send a batch of texts to /v1/embeddings and return embeddings."""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.chunk_size):
            batch = texts[i : i + self.chunk_size]
            resp = self.http_client.post(
                f"{self.api_base}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": batch, "model": self.model},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for item in data:
                out.append(item.get("embedding", []))
        return out

    # ── LangChain interface ───────────────────────────────────────────────
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode passages — NO instruction prefix (NV-Embed asymmetric)."""
        if not texts:
            return []
        return self._call(texts)

    def embed_query(self, text: str) -> list[float]:
        """Encode query — PREPEND instruction prefix to activate NV-Embed
        task-conditioning. This is the key behavior that distinguishes
        NV-Embed from generic embedding models."""
        if not text:
            return []
        prefixed = self.query_instruction + text
        result = self._call([prefixed])
        return result[0] if result else []

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

    def create_embeddings(self) -> Embeddings:
        """Create embeddings client.

        ──────────────────────────────────────────────────────────────────
         History note (2026-05-27 v1.1 experiment):
         We tried wrapping with NVEmbedEmbeddings to apply NV-Embed-v2's
         instruction prefix on queries. On a 31-entry Chinese legal V&V
         set this caused Hit Rate to drop from 0.9355 → 0.7742, even
         after a full passage-side reindex. Likely causes:
           • English instruction vs Chinese queries — cross-lingual loss
           • Small dataset noise dominating the +2-5% NV-Embed paper gain
         Decision: revert to plain OpenAIEmbeddings. NVEmbedEmbeddings
         class is kept available for future re-evaluation (e.g., with
         Chinese instruction or larger dataset).
        ──────────────────────────────────────────────────────────────────
        """
        return OpenAIEmbeddings(
            model=self.config.embed_model,
            openai_api_base=self.config.embed_api_base,
            openai_api_key=self.config.embed_api_key,
            check_embedding_ctx_length=False,
            http_client=self._http_client,
            chunk_size=10,
        )

    def create_llm(self, temperature: float = 0) -> ChatOpenAI:
        """Create configured ChatOpenAI client.

        gpt-oss-* is a reasoning model: without `reasoning_effort` the model
        spends its whole token budget on internal CoT and returns empty (or
        single-char) `content`, which breaks downstream parsers — rerank
        sees `"0"` and rejects all candidates, HyDE gets empty text, verify
        misjudges. Empirically (rerank a 20-candidate Chinese legal list):
            low    → content "1"      (only first item, parser sees this as
                                       a partial ranking and rejects)
            medium → content "1,2,3"  ✓
            high   → content "1,3,2"  ✓ (slower, no quality gain here)
        We pin `medium` as the default; override with REASONING_EFFORT env.
        """
        model = self.config.chat_model
        extra: dict = {}
        if model.startswith("gpt-oss") or model.startswith("o1") or model.startswith("o3"):
            import os
            effort = os.environ.get("REASONING_EFFORT", "medium")
            extra["reasoning_effort"] = effort
        return ChatOpenAI(
            model=model,
            openai_api_base=self.config.llm_api_base,
            openai_api_key=self.config.llm_api_key,
            temperature=temperature,
            http_client=self._http_client,
            **extra,
        )

    def create_vectorstore(self, embeddings: Embeddings) -> PGVector:
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
