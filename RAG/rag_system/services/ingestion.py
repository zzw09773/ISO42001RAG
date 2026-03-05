"""
Ingestion Service

Handles the processing, splitting, and indexing of documents into the vector store and document store.
Uses LangChain's ParentDocumentRetriever mechanism for hierarchical storage.
"""
import logging
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

        # Initialize Retriever (used here for its .add_documents capability)
        self.retriever = ParentDocumentRetriever(
            vectorstore=self.vectorstore,
            docstore=self.docstore,
            child_splitter=self.child_splitter,
            parent_splitter=self.parent_splitter,
            # Search kwargs irrelevant for ingestion, but required by init
            search_kwargs={"k": self.config.top_k}
        )
        
    def _compute_sha256(self, content: str) -> str:
        """Compute SHA-256 hash of the content string."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def index_file(self, file_path: Path) -> int:
        """
        Index a single file.

        Returns:
            Number of parent chunks added (always 1 for success)

        Raises:
            RAGIndexingError: If indexing fails
        """
        if not file_path.exists():
            raise RAGIndexingError(f"File not found: {file_path}")

        logger.info(f"Indexing file: {file_path.name}")

        try:
            # Read content
            content = file_path.read_text(encoding='utf-8')
            
            # Compute Hash
            file_hash = self._compute_sha256(content)

            # Create generic Document
            doc = Document(
                page_content=content,
                metadata={
                    "source": file_path.name,
                    "file_path": str(file_path),
                    "hash": file_hash
                }
            )

            # Add to retriever (handles splitting & storage)
            self.retriever.add_documents([doc], ids=None)

            logger.info(f"Successfully indexed {file_path.name} (Hash: {file_hash[:8]})")
            return 1

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
