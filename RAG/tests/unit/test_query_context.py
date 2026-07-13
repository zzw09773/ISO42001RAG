"""Regression coverage for keyword-free, source-scoped retrieval."""
import sys
from pathlib import Path

from langchain_core.documents import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rag_system.agent import nodes
from rag_system.core.config import RAGConfig
from rag_system.services.retrieval import RetrievalService


def test_non_article_query_is_never_rewritten_for_domain_detection():
    question = "我遇到一種事先完全沒列過的刁難方式"

    assert nodes._expand_query(question) == question


def test_short_article_reference_keeps_its_existing_expansion():
    assert nodes._expand_query("第八條") == "第 8 條 法律條文規定內容 第8條"


def test_short_article_reference_without_prefix_is_expanded():
    assert nodes._expand_query("46條") == "第 46 條 法律條文規定內容 第46條"


def test_source_scoped_search_sends_original_query_to_every_corpus_source():
    calls = []

    class FakeVectorStore:
        def similarity_search(self, query, *, k, filter):
            calls.append((query, k, filter))
            source = filter["source"]
            return [Document(page_content=f"{source} 內容", metadata={"source": source})]

    service = object.__new__(RetrievalService)
    service.config = RAGConfig(rerank_top_n=3)
    service.vectorstore = FakeVectorStore()
    service.corpus_sources = ("甲法.md", "乙法.md")
    question = "在一般公司遇到一種從沒列過的情況"

    docs = service._source_scoped_search(question)

    assert [call[0] for call in calls] == [question, question]
    assert [call[2] for call in calls] == [
        {"source": "甲法.md"},
        {"source": "乙法.md"},
    ]
    assert {doc.metadata["source"] for doc in docs} == {"甲法.md", "乙法.md"}


def test_query_preserves_source_candidates_when_reranker_rejects_all():
    source_docs = [
        Document(page_content="甲法內容", metadata={"source": "甲法.md"}),
        Document(page_content="乙法內容", metadata={"source": "乙法.md"}),
    ]
    service = object.__new__(RetrievalService)
    service.config = RAGConfig(rerank_top_n=3)
    service.corpus_sources = ("甲法.md", "乙法.md")
    service._detect_article_numbers = lambda _question: set()
    service._hybrid_search = lambda _question: []
    service._source_scoped_search = lambda _question: source_docs
    service._generate_hyde_document = lambda _question: None
    service._extract_self_query_filters = lambda _question: {
        "cross_reference": False,
        "law_names": [],
    }
    service._rerank_top_n_with_llm = lambda _question, _docs, _top_n: []
    service._fetch_multiple_parent_docs = lambda docs: docs

    result = service.query("完全沒預先列出的表達方式")

    assert result == source_docs


def test_retrieve_node_keeps_original_question_and_audits_source_scoped_search(monkeypatch):
    captured = []

    class FakeRetrievalService:
        def __init__(self, _config):
            pass

        def query(self, query):
            captured.append(query)
            return [Document(page_content="內容", metadata={"source": "軍人權益事件處理法.md"})]

    monkeypatch.setattr(nodes, "RetrievalService", FakeRetrievalService)
    question = "我遇到一種事先完全沒列過的刁難方式"

    result = nodes.create_retrieve_node(RAGConfig())({"question": question})

    assert captured == [question]
    assert result["retrieved_sources"] == ["軍人權益事件處理法.md"]
    assert "domain_search=source_scoped" in result["actions"][0]


def test_react_retrieval_tool_keeps_the_original_query(monkeypatch):
    from rag_system.agent import react_workflow

    captured = []

    class FakeRetrievalService:
        def __init__(self, _config):
            pass

        def query(self, query):
            captured.append(query)
            return [Document(page_content="內容", metadata={"source": "軍人權益事件處理法.md"})]

    monkeypatch.setattr(react_workflow, "RetrievalService", FakeRetrievalService)

    tool = react_workflow._build_react_tool(RAGConfig())
    question = "我遇到一種事先完全沒列過的刁難方式"
    tool.invoke({"query": question})

    assert captured == [question]
