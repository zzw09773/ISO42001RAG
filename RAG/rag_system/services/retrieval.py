"""
Retrieval Service

Handles the searching and ranking of documents.
Implements Hybrid Retrieval: BM25 (keyword) + Vector (semantic) search,
followed by LLM reranking for top-N selection, then parent document fetch.

Special case: When the query contains article numbers (e.g., "第46條"),
BM25 exact-match hits are returned directly WITHOUT LLM reranking to
ensure precise article retrieval.

BM25 index is built per-article (one chunk per statute article) with a
"【法規名稱·第N條】" prefix injected at the start of each chunk to give
the keyword search a strong identifier signal — same convention as
IngestionService._split_law_by_article().
"""
import logging
import re
import os
import glob
from copy import copy
from typing import List, Optional, Set

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_community.retrievers import BM25Retriever

from ..core.config import RAGConfig
from ..core.factory import ComponentFactory
from ..core.prompts import (
    RERANK_SYSTEM_MSG,
    RERANK_PROMPT_TEMPLATE,
    HYDE_SYSTEM_MSG,
    HYDE_PROMPT_TEMPLATE,
    SELFQUERY_SYSTEM_MSG,
    SELFQUERY_PROMPT_TEMPLATE,
)

# Same regex as IngestionService._split_law_by_article(): match a line that
# IS an article header (e.g., "第 4 條", "第46條").
_ARTICLE_HEADER_RE = re.compile(
    r'^\s*第\s*([0-9一二三四五六七八九十百零兩]+)\s*條\s*$',
    re.MULTILINE,
)

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
        self.corpus_sources: tuple[str, ...] = ()

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
        """Build a BM25 index from all converted markdown files.

        Uses article-aware splitting: one BM25 chunk per statute article
        (matching the IngestionService convention). Each chunk's content
        starts with "【法規名稱·第N條】" so keyword searches that mention
        the law name OR an article number score those chunks high.
        """
        md_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "data", "converted_md"
        )

        md_files = glob.glob(os.path.join(md_dir, "*.md"))
        self.corpus_sources = tuple(sorted(os.path.basename(path) for path in md_files))
        if not md_files:
            logger.warning(f"No markdown files found in {md_dir}, BM25 disabled")
            return None

        all_chunks: List[Document] = []
        for filepath in md_files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                source = os.path.basename(filepath)
                all_chunks.extend(self._split_law_by_article_for_bm25(content, source))
            except Exception as e:
                logger.error(f"Error reading {filepath} for BM25: {e}")

        if not all_chunks:
            logger.warning("No chunks created for BM25")
            return None

        bm25 = BM25Retriever.from_documents(all_chunks, k=self.config.summary_top_k)
        logger.info(
            f"BM25 index built: {len(all_chunks)} article-chunks "
            f"from {len(md_files)} files"
        )
        return bm25

    @staticmethod
    def _split_law_by_article_for_bm25(content: str, source: str) -> List[Document]:
        """Split a single law markdown into one Document per article.

        Mirrors IngestionService._split_law_by_article so BM25 and vector
        indices stay aligned. Content kept pristine (no prefix injection) —
        article identity lives in metadata.
        """
        law_name = source[:-3] if source.endswith(".md") else source
        matches = list(_ARTICLE_HEADER_RE.finditer(content))
        if not matches:
            return [Document(page_content=content, metadata={"source": source})]

        docs: List[Document] = []
        preamble = content[: matches[0].start()].strip()
        if preamble:
            docs.append(
                Document(
                    page_content=preamble,
                    metadata={"source": source, "article_id": "preamble"},
                )
            )

        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            article_text = content[start:end].strip()
            article_id = f"第{m.group(1).strip()}條"
            docs.append(
                Document(
                    page_content=article_text,
                    metadata={
                        "source": source,
                        "article_id": article_id,
                        "law_name": law_name,
                    },
                )
            )
        return docs

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
            # Use a request-local shallow copy. Mutating the shared retriever's
            # k races with concurrent hybrid searches and article fast paths.
            article_retriever = copy(self.bm25_retriever)
            article_retriever.k = MAX_BM25_K
            candidates = article_retriever.invoke(question)
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

        Stage 0.5 (HyDE — for abstract queries only):
            If the query has NO article number, ask the LLM to draft a
            plausible statute fragment and run a SECOND hybrid search using
            that draft. Results merged with the original query's hybrid
            search before reranking. Skipped for article-number queries
            because Stage 0 already handles them precisely.

        Stage 1: Hybrid search plus per-source vector searches using the
                 unchanged question; merge all candidates (deduplicated).
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

            # ── Stage 1: Hybrid + source-scoped search ──────────────────────
            summaries = self._hybrid_search(question)
            source_summaries = self._source_scoped_search(question)
            source_fallback_candidates = source_summaries
            if source_summaries:
                before = len(summaries)
                # Source-scoped results come first so each corpus source remains
                # represented even if the LLM reranker returns an invalid list
                # and the deterministic top-N fallback is used.
                summaries = self._merge_summary_lists(source_summaries, summaries)
                logger.info(
                    "Source-scoped search: %d sources, added %d candidates",
                    len(self.corpus_sources), len(summaries) - before,
                )

            # Stage 0.5 — HyDE only for queries WITHOUT explicit article numbers
            if not article_numbers:
                hyde_text = self._generate_hyde_document(question)
                if hyde_text:
                    hyde_summaries = self._hybrid_search(hyde_text)
                    summaries = self._merge_summary_lists(summaries, hyde_summaries)
                    hyde_source_summaries = self._source_scoped_search(hyde_text)
                    if hyde_source_summaries:
                        summaries = self._merge_summary_lists(
                            summaries, hyde_source_summaries
                        )
                        # For abstract language, the statute-style HyDE path is
                        # a better deterministic safety net than unrelated raw
                        # query neighbours if the reranker rejects everything.
                        source_fallback_candidates = hyde_source_summaries
                    logger.info(
                        f"HyDE expansion: query→{len(summaries)} merged candidates "
                        f"(was {len(summaries) - len(hyde_summaries)} before)"
                    )

            # ── Stage 0.75 — Self-Query: for cross-reference queries, force
            #                  each law to contribute its own candidates ─────
            # Triggered when the LLM identifies the query as cross-statute.
            # Falls back silently if self-query fails — original results
            # remain unchanged. The goal is to break ties like eval_cr04
            # ("權保委員 vs 權責長官") where standard hybrid round-robin may
            # let one law dominate.
            if not article_numbers:
                filters = self._extract_self_query_filters(question)
                if filters["cross_reference"] and filters["law_names"]:
                    extra: List[Document] = []
                    for law in filters["law_names"]:
                        extra.extend(self._filtered_vector_search(question, law))
                    if extra:
                        before = len(summaries)
                        summaries = self._merge_summary_lists(summaries, extra)
                        logger.info(
                            f"Self-query cross-ref: laws={filters['law_names']}, "
                            f"added {len(summaries) - before} new candidates"
                        )

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
                        fallback_limit = max(
                            top_n,
                            len(self.corpus_sources) * self.config.rerank_top_n,
                        )
                        best_summaries = source_fallback_candidates[:fallback_limit]
                        if not best_summaries:
                            logger.warning("Reranking returned no valid summaries")
                            return []
                        logger.warning(
                            "Reranker rejected all candidates; preserving %d "
                            "source-scoped candidates for generation-time review",
                            len(best_summaries),
                        )

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

    def _extract_self_query_filters(self, question: str) -> dict:
        """Ask the LLM to extract structured filters (law names, article IDs,
        cross-reference flag) from the natural-language query.

        Returns a dict like:
          {
            "law_names": ["陸海空軍懲罰法", "軍人權益事件處理法"],
            "article_ids": ["第4條", "第8條"],
            "cross_reference": true,
          }
        Empty / failed extraction returns empty lists & cross_reference=False.
        """
        import json as _json
        import re as _re
        try:
            response = self.llm.invoke([
                SystemMessage(content=SELFQUERY_SYSTEM_MSG),
                HumanMessage(content=SELFQUERY_PROMPT_TEMPLATE.format(question=question)),
            ])
            text = (response.content or "").strip()
            text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.MULTILINE).strip()
            m = _re.search(r"\{.*\}", text, _re.DOTALL)
            if not m:
                return {"law_names": [], "article_ids": [], "cross_reference": False}
            data = _json.loads(m.group(0))
            return {
                "law_names": data.get("law_names", []) or [],
                "article_ids": data.get("article_ids", []) or [],
                "cross_reference": bool(data.get("cross_reference")),
            }
        except Exception as e:
            logger.warning(f"Self-query extraction failed: {e}")
            return {"law_names": [], "article_ids": [], "cross_reference": False}

    def _source_scoped_search(self, query: str) -> List[Document]:
        """Search every indexed source with the original query unchanged.

        This provides deterministic domain coverage without intent keywords,
        query rewriting, or assumptions about the user's identity.
        """
        candidates: List[Document] = []
        per_source_k = max(1, self.config.rerank_top_n)
        for source in self.corpus_sources:
            candidates.extend(
                self._filtered_vector_search_by_source(
                    query, source, k=per_source_k
                )
            )
        return self._merge_results(candidates, []) if candidates else []

    def _filtered_vector_search_by_source(
        self, query: str, source: str, *, k: int
    ) -> List[Document]:
        """Run vector search restricted to one corpus source."""
        try:
            results = self.vectorstore.similarity_search(
                query,
                k=k,
                filter={"source": source},
            )
            logger.info("Filtered vector search [%s]: %d results", source, len(results))
            return results
        except Exception as e:
            logger.warning("Filtered vector search failed for %s: %s", source, e)
            return []

    def _filtered_vector_search(self, query: str, law_name: str) -> List[Document]:
        """Compatibility wrapper for structured law-name filters."""
        return self._filtered_vector_search_by_source(
            query,
            f"{law_name}.md",
            k=self.config.summary_top_k,
        )

    def _generate_hyde_document(self, question: str) -> Optional[str]:
        """Ask the LLM to draft a hypothetical statute fragment.

        The draft is used as a secondary embedding target so we retrieve
        passages whose semantic space lies near "what the answer looks like",
        not just near "what the question looks like".

        Returns the draft text, or None if generation fails or is empty.
        """
        try:
            response = self.llm.invoke([
                SystemMessage(content=HYDE_SYSTEM_MSG),
                HumanMessage(content=HYDE_PROMPT_TEMPLATE.format(question=question)),
            ])
            draft = (response.content or "").strip()
            if not draft or len(draft) < 10:
                logger.info("HyDE: empty or too short, skipping")
                return None
            # Strip common LLM scaffolding patterns
            for prefix in ("回傳：", "答：", "Answer:", "Output:"):
                if draft.startswith(prefix):
                    draft = draft[len(prefix):].strip()
            logger.info(f"HyDE draft ({len(draft)} chars): {draft[:80]}...")
            return draft
        except Exception as e:
            logger.warning(f"HyDE generation failed, falling back to query-only: {e}")
            return None

    @staticmethod
    def _merge_summary_lists(
        primary: List[Document], secondary: List[Document]
    ) -> List[Document]:
        """Merge two summary lists, primary first, deduplicated by content."""
        seen = set()
        out: List[Document] = []
        for doc in primary + secondary:
            key = doc.page_content[:100].strip()
            if key in seen:
                continue
            seen.add(key)
            out.append(doc)
        return out

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
