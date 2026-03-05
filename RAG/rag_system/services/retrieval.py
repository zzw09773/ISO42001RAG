"""
Retrieval Service

Handles the searching and ranking of documents.
Implements Hybrid Retrieval: BM25 (keyword) + Vector (semantic) search,
followed by LLM reranking for top-N selection, then parent document fetch.

Special case: When the query contains article numbers (e.g., "第46條"),
BM25 exact-match hits are returned directly WITHOUT LLM reranking to
ensure precise article retrieval.
"""
import logging
import re
import os
import glob
from typing import List, Optional, Set

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_community.retrievers import BM25Retriever
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..core.config import RAGConfig
from ..core.factory import ComponentFactory
from ..core.prompts import RERANK_SYSTEM_MSG, RERANK_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

# Maximum BM25 candidate pool size for article-number fast-path search.
# Set higher than summary_top_k so we can scan more chunks for exact article hits.
MAX_BM25_K = 30

class RAGRetrievalError(Exception):
    """Raised when document retrieval fails."""
    pass

class RetrievalService:
    """
    Hybrid retrieval service: BM25 (keyword) + Vector (semantic) search.
    """

    def __init__(self, config: RAGConfig):
        self.config = config
        self.factory = ComponentFactory(config)
        self._init_components()

    def _init_components(self):
        """Initialize retrieval components."""
        # LLM for Reranking
        self.llm = self.factory.create_llm(temperature=0)

        # Vector Store (Search)
        embeddings = self.factory.create_embeddings()
        self.vectorstore = self.factory.create_vectorstore(embeddings)

        # Doc Store (Fetch Parent)
        self.docstore = self.factory.create_docstore()

        # BM25 Retriever — built from source markdown files
        self.bm25_retriever = self._build_bm25_index()

    def _build_bm25_index(self) -> Optional[BM25Retriever]:
        """Build a BM25 index from all converted markdown files."""
        md_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "data", "converted_md"
        )

        md_files = glob.glob(os.path.join(md_dir, "*.md"))
        if not md_files:
            logger.warning(f"No markdown files found in {md_dir}, BM25 disabled")
            return None

        # Split each file into per-article chunks for BM25
        all_chunks = []
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n第 ", "\n\n", "\n", "。", " "],
        )

        for filepath in md_files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()

                source = os.path.basename(filepath)
                doc = Document(page_content=content, metadata={"source": source})
                chunks = splitter.split_documents([doc])
                all_chunks.extend(chunks)
            except Exception as e:
                logger.error(f"Error reading {filepath} for BM25: {e}")

        if not all_chunks:
            logger.warning("No chunks created for BM25")
            return None

        bm25 = BM25Retriever.from_documents(all_chunks, k=self.config.summary_top_k)
        logger.info(f"BM25 index built: {len(all_chunks)} chunks from {len(md_files)} files")
        return bm25

    # ─── Chinese numeral mapping ───────────────────────────────────────────
    _CN_NUM = {
        "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
        "十": 10, "百": 100,
    }

    def _detect_article_numbers(self, question: str) -> Set[int]:
        """
        Extract article numbers from the query.

        Supports:
        - Arabic numerals:  第46條、第 46 條、46條
        - Chinese numerals: 第四十六條、第百條

        Returns a set of integer article numbers found.
        """
        found: Set[int] = set()

        # Pattern 1: Arabic numerals — e.g. "第46條", "第 46 條", "46條"
        for m in re.finditer(r'(?:第\s*)?(\d+)\s*條', question):
            found.add(int(m.group(1)))

        # Pattern 2: Chinese ordinal — e.g. "第四十六條"
        for m in re.finditer(r'第([零一二三四五六七八九十百]+)條', question):
            cn = m.group(1)
            num = 0
            tmp = 0
            for ch in cn:
                v = self._CN_NUM.get(ch, 0)
                if v == 10:
                    tmp = tmp * 10 if tmp else 10
                elif v == 100:
                    tmp = tmp * 100 if tmp else 100
                else:
                    tmp += v
            num = tmp
            if num > 0:
                found.add(num)

        if found:
            logger.info(f"Article numbers detected in query: {sorted(found)}")
        return found

    def _bm25_article_hits(
        self, question: str, article_numbers: Set[int]
    ) -> List[Document]:
        """
        Run BM25 search and return ONLY chunks that contain at least one
        of the detected article numbers as a matching article header.
        """
        if not self.bm25_retriever or not article_numbers:
            return []

        try:
            # Use higher k to widen the BM25 candidate pool
            original_k = self.bm25_retriever.k
            self.bm25_retriever.k = MAX_BM25_K
            candidates = self.bm25_retriever.invoke(question)
            self.bm25_retriever.k = original_k
        except Exception as e:
            logger.error(f"BM25 article search failed: {e}")
            return []

        hits = []
        for doc in candidates:
            content = doc.page_content
            for art_num in article_numbers:
                # Match "第 46 條" or "第46條" as a section header
                pattern = rf'第\s*{art_num}\s*條'
                if re.search(pattern, content):
                    hits.append(doc)
                    logger.info(
                        f"BM25 article hit: article {art_num} in "
                        f"{doc.metadata.get('source', '?')}"
                    )
                    break  # already matched, no need to check other numbers
        return hits

    def query(self, question: str) -> List[Document]:
        """
        Retrieve relevant documents using Hybrid Search + LLM reranking.

        Stage 0 (article-number fast path):
            If the query explicitly contains article numbers (e.g. "第46條"),
            BM25 exact-match hits are returned directly and prepended to the
            final result WITHOUT going through the LLM reranker, guaranteeing
            that the targeted article is always present in the answer context.

        Stage 1: Hybrid search — merge BM25 + Vector results (deduplicated).
        Stage 2: Use LLM to select the top-N most relevant summaries.
        Stage 3: Retrieve the full content (Parent Documents) for the winners.

        Returns list of PARENT documents (deduplicated by doc_id).
        """
        logger.info(f"Querying (Hybrid): {question}")

        try:
            # ── Stage 0: Article-number fast path ────────────────────────────
            article_numbers = self._detect_article_numbers(question)
            pinned_docs: List[Document] = []

            if article_numbers:
                bm25_hits = self._bm25_article_hits(question, article_numbers)
                if bm25_hits:
                    # Fetch parent documents for the BM25 hits (preferred)
                    pinned_docs = self._fetch_multiple_parent_docs(bm25_hits)
                    if not pinned_docs:
                        # Fallback: use the raw BM25 chunks directly
                        pinned_docs = bm25_hits
                    logger.info(
                        f"Article fast-path: pinned {len(pinned_docs)} doc(s) "
                        f"for article(s) {sorted(article_numbers)} — skipping LLM rerank"
                    )

            # ── Stage 1: Hybrid search — BM25 + Vector ───────────────────────
            summaries = self._hybrid_search(question)
            if not summaries and not pinned_docs:
                logger.warning("No results from hybrid search")
                return []

            # ── Stage 2: Rerank with LLM (skipped when fast-path succeeded) ──
            background_docs: List[Document] = []
            if summaries:
                if pinned_docs:
                    # Fast-path succeeded: still fetch top-(N-1) background docs
                    # so the LLM has broader context, but we don't need reranking
                    # for the pinned article itself.
                    top_n = max(1, self.config.rerank_top_n - len(pinned_docs))
                    best_summaries = self._rerank_top_n_with_llm(
                        question, summaries, top_n
                    )
                    background_docs = self._fetch_multiple_parent_docs(best_summaries)
                    if not background_docs:
                        background_docs = best_summaries
                else:
                    # Normal path: full LLM rerank
                    top_n = self.config.rerank_top_n
                    best_summaries = self._rerank_top_n_with_llm(
                        question, summaries, top_n
                    )
                    if not best_summaries:
                        logger.warning("Reranking returned no valid summaries")
                        return []

                    # ── Stage 3: Fetch parent documents ──────────────────────
                    parent_docs = self._fetch_multiple_parent_docs(best_summaries)
                    return parent_docs if parent_docs else best_summaries

            # ── Combine: pinned first, then background (dedup by content) ────
            seen_snippets: Set[str] = set()
            result: List[Document] = []
            for doc in pinned_docs + background_docs:
                key = doc.page_content[:80].strip()
                if key not in seen_snippets:
                    seen_snippets.add(key)
                    result.append(doc)

            return result

        except Exception as e:
            logger.error(f"Unexpected error in query: {e}")
            raise RAGRetrievalError(f"Query failed: {e}") from e

    def _hybrid_search(self, question: str) -> List[Document]:
        """
        Stage 1: Merge results from BM25 and Vector search.
        Deduplicates by content overlap.
        """
        # Vector search
        vector_results = []
        try:
            vector_results = self.vectorstore.similarity_search(
                question,
                k=self.config.summary_top_k
            )
            logger.info(f"Vector search: {len(vector_results)} results")
        except Exception as e:
            logger.error(f"Vector search failed: {e}")

        # BM25 search
        bm25_results = []
        if self.bm25_retriever:
            try:
                bm25_results = self.bm25_retriever.invoke(question)
                logger.info(f"BM25 search: {len(bm25_results)} results")
            except Exception as e:
                logger.error(f"BM25 search failed: {e}")

        # Merge and deduplicate
        merged = self._merge_results(vector_results, bm25_results)
        logger.info(f"Hybrid search merged: {len(merged)} unique results")
        return merged

    def _merge_results(
        self, vector_docs: List[Document], bm25_docs: List[Document]
    ) -> List[Document]:
        """
        Merge two document lists with SOURCE DIVERSITY enforcement.
        Groups results by source and interleaves round-robin.
        """
        seen_snippets = set()
        source_buckets = {}

        for doc in vector_docs + bm25_docs:
            snippet = doc.page_content[:100].strip()
            if snippet in seen_snippets:
                continue
            seen_snippets.add(snippet)

            source = doc.metadata.get("source", "unknown")
            if source not in source_buckets:
                source_buckets[source] = []
            source_buckets[source].append(doc)

        # Round-robin interleave across sources
        merged = []
        buckets = list(source_buckets.values())
        max_len = max((len(b) for b in buckets), default=0)

        for i in range(max_len):
            for bucket in buckets:
                if i < len(bucket):
                    merged.append(bucket[i])

        logger.info(f"Source diversity: {', '.join(f'{k}={len(v)}' for k, v in source_buckets.items())}")
        return merged


    def _rerank_top_n_with_llm(
        self, question: str, summaries: List[Document], top_n: int
    ) -> List[Document]:
        """Stage 2: Use LLM to rank and select the top-N most relevant summaries."""
        # If fewer candidates than top_n, return all
        if len(summaries) <= top_n:
            logger.info(f"Only {len(summaries)} candidates, skipping LLM rerank")
            return summaries

        # Build rerank prompt using centralized template
        options_text = self._build_rerank_options(summaries)
        prompt = RERANK_PROMPT_TEMPLATE.format(
            question=question,
            options_text=options_text,
            top_n=top_n,
        )

        logger.info(f"LLM Rerank: {len(summaries)} candidates, selecting top {top_n}")

        try:
            response = self.llm.invoke([
                SystemMessage(content=RERANK_SYSTEM_MSG),
                HumanMessage(content=prompt)
            ])

            selection = response.content.strip()
            logger.info(f"LLM Raw Response: {selection}")

            # Parse ranked list
            selected_indices = self._parse_llm_ranking(selection, len(summaries), top_n)

            if selected_indices:
                if selected_indices == [-1]:
                    logger.info("LLM explicitly rejected all candidates (returned 0)")
                    return []
                result = [summaries[i] for i in selected_indices]
                logger.info(f"LLM selected {len(result)} candidates: {[i+1 for i in selected_indices]}")
                return result
            else:
                # Fallback: return top N by original order
                logger.warning(f"LLM returned invalid ranking: {selection}. Fallback to top {top_n}.")
                return summaries[:top_n]

        except Exception as e:
            logger.error(f"Reranking failed: {e}. Fallback to top {top_n}.")
            return summaries[:top_n]

    def _build_rerank_options(self, summaries: List[Document]) -> str:
        """Build formatted options string for LLM reranking."""
        options = []
        for i, doc in enumerate(summaries, 1):
            preview = doc.page_content[:300].replace("\n", " ")
            options.append(f"[{i}] ...{preview}...")
        return "\n\n".join(options)

    def _parse_llm_ranking(
        self, selection: str, num_candidates: int, top_n: int
    ) -> List[int]:
        """Parse LLM ranking response (e.g., '3,1,5' or '0' for none)."""
        # Extract all numbers from the response
        numbers = re.findall(r'\d+', selection)

        if not numbers:
            return []

        # If first number is 0, LLM says no relevant docs
        if numbers[0] == '0':
            return [-1]  # Signal explicit rejection

        # Convert to 0-based indices, validate range, deduplicate, keep order
        seen = set()
        indices = []
        for num_str in numbers:
            idx = int(num_str) - 1  # Convert to 0-based
            if 0 <= idx < num_candidates and idx not in seen:
                seen.add(idx)
                indices.append(idx)
                if len(indices) >= top_n:
                    break

        return indices if indices else []

    def _fetch_multiple_parent_docs(self, summaries: List[Document]) -> List[Document]:
        """Stage 3: Fetch parent documents for multiple summaries, deduplicated by doc_id."""
        seen_ids = set()
        results = []

        for summary in summaries:
            doc_id = summary.metadata.get("doc_id")

            # BM25 chunks don't have doc_id — include them directly
            if not doc_id:
                results.append(summary)
                logger.info(f"Including BM25 chunk directly (no doc_id): {summary.metadata.get('source', 'unknown')}")
                continue

            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)

            try:
                parents = self.docstore.mget([doc_id])
                for p in parents:
                    if p:
                        results.append(p)
                        logger.info(f"Fetched parent document: {doc_id}")
            except Exception as e:
                logger.error(f"Failed to fetch parent document {doc_id}: {e}")

        if not results:
            logger.warning("No parent documents found for any selected summaries")

        return results
