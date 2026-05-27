"""
Ingestion Service

Handles the processing, splitting, and indexing of documents into the vector store and document store.
Uses LangChain's ParentDocumentRetriever mechanism for hierarchical storage.

For Chinese law documents, an article-aware splitter (`_split_law_by_article`)
forces one-parent-per-article so retrieval can pinpoint the exact statute.
"""
import logging
import re
import uuid
from pathlib import Path
from typing import Dict, List, Optional
import shutil
import hashlib

from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from ..core.config import RAGConfig
from ..core.factory import ComponentFactory

logger = logging.getLogger(__name__)

# Match a line that *is* an article header. Captures the article number
# in normalised form (whitespace removed). Examples that match:
#   "第 4 條"   →  group(1) = "4"
#   "第46條"    →  group(1) = "46"
#   "第 一二三 條"  →  group(1) = "一二三"
_ARTICLE_HEADER_RE = re.compile(
    r'^\s*第\s*([0-9一二三四五六七八九十百零兩]+)\s*條\s*$',
    re.MULTILINE,
)

class RAGIndexingError(Exception):
    """Raised when document indexing fails."""
    pass

class IngestionService:
    """
    Service responsible for ingesting documents.
    """

    def __init__(self, config: RAGConfig):
        self.config = config
        self.factory = ComponentFactory(config)
        self._init_retriever()

    def _init_retriever(self):
        """Initialize the underlying ParentDocumentRetriever."""
        # Create components using factory
        embeddings = self.factory.create_embeddings()
        self.vectorstore = self.factory.create_vectorstore(embeddings)
        self.docstore = self.factory.create_docstore()

        # Configure Text Splitters
        # Parent: Large chunks (preserve context)
        # Kept as fallback for non-law documents; law docs use
        # _split_law_by_article() instead — see index_file().
        self.parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            separators=["\n第", "\n\n", "\n", "。", " ", ""]
        ) # Optimized for Chinese Law

        # Child: Small chunks (optimized for embedding search)
        self.child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.child_chunk_size,
            chunk_overlap=100,
        )

        # Initialize Retriever (used here for its .add_documents capability
        # on non-law documents only — law documents bypass this via index_file)
        self.retriever = ParentDocumentRetriever(
            vectorstore=self.vectorstore,
            docstore=self.docstore,
            child_splitter=self.child_splitter,
            parent_splitter=self.parent_splitter,
            # Search kwargs irrelevant for ingestion, but required by init
            search_kwargs={"k": self.config.top_k}
        )

    def _split_law_by_article(
        self, content: str, source: str, file_hash: str
    ) -> List[Document]:
        """Split Chinese law text into one Document per article.

        Recognises lines matching `第N條` (arabic or Chinese numerals) as
        article boundaries. Each resulting Document spans exactly one
        article — never merges adjacent short articles like
        RecursiveCharacterTextSplitter would.

        Returns at least one Document. Preamble (法規名稱、修正日期、章節
        標題) before the first article is collected into a "preamble"
        Document so it remains searchable.
        """
        matches = list(_ARTICLE_HEADER_RE.finditer(content))
        if not matches:
            # Not a recognised law document — fall back to single Document
            return [
                Document(
                    page_content=content,
                    metadata={
                        "source": source,
                        "file_path": str(source),
                        "hash": file_hash,
                        "article_id": "whole_document",
                    },
                )
            ]

        docs: List[Document] = []

        # Preamble: everything before the first article header
        preamble = content[: matches[0].start()].strip()
        if preamble:
            docs.append(
                Document(
                    page_content=preamble,
                    metadata={
                        "source": source,
                        "hash": file_hash,
                        "article_id": "preamble",
                    },
                )
            )

        law_name = source[:-3] if source.endswith(".md") else source

        # One Document per article — keep content pristine (NO prefix injection).
        # Adding identifier prefixes uniformly to every chunk reduces vector
        # space discriminability (tried in earlier iteration; Hit Rate fell from
        # 0.871 → 0.806). Article identity lives in metadata only.
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            article_text = content[start:end].strip()
            article_num = m.group(1).strip()
            article_id = f"第{article_num}條"

            docs.append(
                Document(
                    page_content=article_text,
                    metadata={
                        "source": source,
                        "hash": file_hash,
                        "article_id": article_id,
                        "law_name": law_name,
                    },
                )
            )

        return docs
        
    def _compute_sha256(self, content: str) -> str:
        """Compute SHA-256 hash of the content string."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def index_file(self, file_path: Path) -> int:
        """
        Index a single file.

        For Chinese law documents (those containing "第N條" lines), each article
        becomes an independent parent Document, indexed with `article_id` in
        metadata. This bypasses RecursiveCharacterTextSplitter's merge
        behaviour which previously collapsed short adjacent articles into the
        same parent, causing Hit Rate misses.

        Returns:
            Number of parent documents added (1+ per file)

        Raises:
            RAGIndexingError: If indexing fails
        """
        if not file_path.exists():
            raise RAGIndexingError(f"File not found: {file_path}")

        logger.info(f"Indexing file: {file_path.name}")

        try:
            content = file_path.read_text(encoding='utf-8')
            file_hash = self._compute_sha256(content)
            source = file_path.name

            # Split into per-article parent Documents (or single Doc if not a law)
            article_docs = self._split_law_by_article(content, source, file_hash)
            # Tag each with file_path for downstream consumers (delete, audit)
            for d in article_docs:
                d.metadata.setdefault("file_path", str(file_path))

            # Assign deterministic parent IDs; persist to docstore directly,
            # bypassing parent_splitter which would merge short articles.
            parent_ids = [str(uuid.uuid4()) for _ in article_docs]
            self.docstore.mset(list(zip(parent_ids, article_docs)))

            # Build child chunks for vector search; link each back to its parent
            child_docs: List[Document] = []
            for art_doc, pid in zip(article_docs, parent_ids):
                # Inject doc_id into metadata before splitting so children inherit it
                art_with_id = Document(
                    page_content=art_doc.page_content,
                    metadata={**art_doc.metadata, "doc_id": pid},
                )
                pieces = self.child_splitter.split_documents([art_with_id])
                # Ensure every child carries the parent link & article_id
                for piece in pieces:
                    piece.metadata["doc_id"] = pid
                    piece.metadata["source"] = source
                    piece.metadata["hash"] = file_hash
                    piece.metadata.setdefault(
                        "article_id", art_doc.metadata.get("article_id", "")
                    )
                child_docs.extend(pieces)

            if child_docs:
                self.vectorstore.add_documents(child_docs)

            logger.info(
                "Successfully indexed %s: %d articles → %d child chunks (Hash: %s)",
                source, len(article_docs), len(child_docs), file_hash[:8],
            )
            return len(article_docs)

        except Exception as e:
            logger.error(f"Failed to index {file_path.name}: {e}")
            raise RAGIndexingError(f"Failed to index {file_path.name}: {e}") from e

    def index_directory(self, dir_path: Path, pattern: str = "*.*") -> Dict[str, int]:
        """
        Index all files in a directory matching pattern.
        """
        results = {"success": 0, "failed": 0}
        
        if not dir_path.exists():
            logger.error(f"Directory not found: {dir_path}")
            return results

        files = list(dir_path.glob(pattern))
        logger.info(f"Found {len(files)} files in {dir_path}")

        for f in files:
            if f.is_file() and f.suffix.lower() in ['.txt', '.md', '.py', '.json']:
                try:
                    self.index_file(f)
                    results["success"] += 1
                except Exception as e:
                    logger.error(f"Error indexing {f.name}: {e}")
                    results["failed"] += 1
        
        return results
        
    def delete_document(self, filename: str) -> int:
        """
        Deletes a document from the vector store and docstore by matching metadata source.
        Returns the number of child chunks deleted.
        """
        # Note: In standard PGVector there isn't a direct "delete by metadata" convenience function, 
        # so we will use the underlying pgvector client execute method.
        try:
            # Connect using psycopg through the engine
            from sqlalchemy import text
            with self.vectorstore._make_session() as session:
                # Find the documents to delete
                res = session.execute(
                    text("SELECT id, cmetadata FROM langchain_pg_embedding WHERE cmetadata->>'source' = :src"),
                    {"src": filename}
                ).fetchall()
                
                if not res:
                    logger.info(f"Delete requested but no vectors found for {filename}")
                    return 0
                    
                row_ids = [str(r[0]) for r in res]
                doc_ids = list(set([r[1].get('doc_id') for r in res if isinstance(r[1], dict) and r[1].get('doc_id')]))
                
                # Delete from vector store
                session.execute(
                    text("DELETE FROM langchain_pg_embedding WHERE id = ANY(:ids)"),
                    {"ids": row_ids}
                )
                session.commit()
                
                # Delete from document store (LocalFileStore / default underlying Langchain storage)
                if doc_ids and hasattr(self.docstore, "mdelete"):
                    try:
                        self.docstore.mdelete(doc_ids)
                    except Exception as e:
                        # Depending on the implementation of docstore mdelete
                        logger.warning(f"Could not delete from docstore {e}")

                logger.info(f"Deleted {len(row_ids)} vector chunks and {len(doc_ids)} parent documents for {filename}")
                return len(row_ids)

        except Exception as e:
            logger.error(f"Error deleting document {filename}: {e}")
            raise RAGIndexingError(f"Failed to delete {filename}: {e}") from e

    def clear_index(self):
        """
        Clear all data from vectorstore and docstore.
        Warning: Destructive!
        """
        logger.warning("Clearing RAG index...")
        
        # Clear PGVector tables
        try:
            self.vectorstore.drop_tables() 
            self.vectorstore.create_tables_if_not_exists()
        except Exception as e:
            logger.error(f"Error clearing vectorstore: {e}")

        # Clear FileStore
        docstore_path = self.config.docstore_path
        if docstore_path.exists():
            shutil.rmtree(docstore_path)
            docstore_path.mkdir(parents=True, exist_ok=True)
            
        logger.info("Index cleared.")
