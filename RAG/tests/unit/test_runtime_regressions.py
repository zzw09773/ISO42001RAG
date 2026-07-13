"""Regression coverage for RAG API runtime lifecycle boundaries."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from fastapi.testclient import TestClient
from langchain_core.documents import Document


RAG_ROOT = Path(__file__).resolve().parents[2]


def test_dotenv_is_loaded_before_local_modules_read_environment():
    """Import-time settings such as rate limiting must see the mounted .env."""
    script = """
import os
import dotenv

os.environ.pop("RATE_LIMIT_PER_MINUTE", None)

def load_test_env(*args, **kwargs):
    os.environ["RATE_LIMIT_PER_MINUTE"] = "137"
    return True

dotenv.load_dotenv = load_test_env
import api
from rag_system.core import rate_limiter
print(f"RATE_LIMIT={rate_limiter._LIMIT_PER_MINUTE}")
"""
    env = os.environ.copy()
    env.pop("RATE_LIMIT_PER_MINUTE", None)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=RAG_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "RATE_LIMIT=137" in completed.stdout


def test_effective_runtime_snapshot_contains_only_admin_whitelist(tmp_path):
    runtime_env = tmp_path / "rag-runtime.env"
    runtime_env.write_text(
        "TOP_K=17\nRAG_LOG_LEVEL=WARNING\nLLM_API_KEY=must-not-leak\n",
        encoding="utf-8",
    )
    snapshot = tmp_path / "rag-effective.env"
    script = "import api"
    env = os.environ.copy()
    env.update({
        "RAG_ENV_FILE": str(runtime_env),
        "RAG_EFFECTIVE_ENV_FILE": str(snapshot),
    })

    subprocess.run(
        [sys.executable, "-c", script],
        cwd=RAG_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    text = snapshot.read_text(encoding="utf-8")
    assert "TOP_K=17" in text
    assert "RAG_LOG_LEVEL=WARNING" in text
    assert "LLM_API_KEY" not in text
    assert "must-not-leak" not in text
    assert snapshot.stat().st_mode & 0o777 == 0o600


def _intranet_client(monkeypatch):
    import api
    from rag_system.core import auth, rate_limiter

    monkeypatch.setenv("ALLOW_INTRANET_MODE", "true")
    monkeypatch.setattr(auth, "_ALLOW_INTRANET", None)
    monkeypatch.setattr(auth, "_VALID_KEYS", None)
    rate_limiter._counters.clear()
    monkeypatch.setattr(rate_limiter, "_LIMIT_PER_MINUTE", 60)
    monkeypatch.setattr(api, "conv_store", None)
    monkeypatch.setattr(api, "audit", None)
    return api, TestClient(api.app)


def _stream_content(response_text):
    events = []
    for line in response_text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        events.append(json.loads(line.removeprefix("data: ")))
    content = "".join(
        event["choices"][0]["delta"].get("content", "") for event in events
    )
    return events, content


def _stream_reasoning(events):
    return "".join(
        event["choices"][0]["delta"].get("reasoning_content", "")
        for event in events
    )


def test_stream_filters_complete_buffer_before_sending_sse(monkeypatch):
    import api

    async def sensitive_stream(**kwargs):
        # Deliberately split the secret so filtering individual tokens would fail.
        yield "資料庫為 postgresql://audit:"
        yield "secret@db.internal/Judge，請勿外傳。"

    monkeypatch.setattr(api, "astream_query", sensitive_stream)
    api, client = _intranet_client(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "rag",
            "messages": [{"role": "user", "content": "請說明第46條"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert "postgresql://audit:" not in response.text
    assert "secret@db.internal/Judge" not in response.text
    events, content = _stream_content(response.text)
    assert "[REDACTED:connection_string]" in content
    assert api.ANSWER_DISCLAIMER in content
    assert events[0]["object"] == "chat.completion.chunk"
    assert events[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert events[-1]["choices"][0]["finish_reason"] == "stop"
    assert response.text.rstrip().endswith("data: [DONE]")


def test_stream_preserves_safe_content_and_disclaimer(monkeypatch):
    import api

    async def safe_stream(**kwargs):
        yield "第46條"
        yield "規定應依正當程序處理。"

    monkeypatch.setattr(api, "astream_query", safe_stream)
    api, client = _intranet_client(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "rag",
            "messages": [{"role": "user", "content": "請說明第46條"}],
            "stream": True,
        },
    )

    _, content = _stream_content(response.text)
    assert response.status_code == 200
    assert content == "第46條規定應依正當程序處理。" + api.ANSWER_DISCLAIMER


def test_stream_exposes_reasoning_summary_as_collapsible_think(monkeypatch):
    import api

    async def answer_stream(**kwargs):
        yield "**思考過程**  \n引用第58條，因其規範申訴程序。\n\n"
        yield "## **問題答案**\n可向申訴管轄機關提出申訴。"

    monkeypatch.setattr(api, "astream_query", answer_stream)
    api, client = _intranet_client(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "rag",
            "messages": [{"role": "user", "content": "該向哪裡反映？"}],
            "stream": True,
        },
    )

    events, content = _stream_content(response.text)
    reasoning = _stream_reasoning(events)
    content_events = [
        event for event in events
        if event["choices"][0]["delta"].get("content")
    ]
    reasoning_events = [
        event for event in events
        if event["choices"][0]["delta"].get("reasoning_content")
    ]
    assert response.status_code == 200
    assert len(content_events) > 1
    assert len(reasoning_events) > 1
    assert events[1]["choices"][0]["delta"] == {
        "reasoning_content": api.STREAM_REASONING_PROGRESS
    }
    assert reasoning.startswith(api.STREAM_REASONING_PROGRESS)
    assert "引用第58條，因其規範申訴程序。" in reasoning
    assert "<think>" not in content
    assert "**思考過程**" not in content
    assert "## **問題答案**" in content
    assert content.endswith(api.ANSWER_DISCLAIMER)
    assert content.count("問題答案") == 1
    assert content.count("本回答由 AI") == 1


def test_nonstream_exposes_reasoning_summary_as_collapsible_think(monkeypatch):
    import api

    monkeypatch.setattr(
        api,
        "run_query",
        lambda **kwargs: {
            "generation": "**思考過程**\n引用第58條。\n\n**問題答案**\n提出申訴。",
            "actions": [],
        },
    )
    api, client = _intranet_client(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "rag",
            "messages": [{"role": "user", "content": "該向哪裡反映？"}],
            "stream": False,
        },
    )

    content = response.json()["choices"][0]["message"]["content"]
    assert response.status_code == 200
    assert content.startswith("<think>\n引用第58條。\n</think>")
    assert "**思考過程**" not in content


def test_verify_retry_reuses_retrieved_documents():
    from rag_system.agent.graph import _route_after_verify

    assert _route_after_verify({"scope": "needs_retry"}) == "generate"
    assert _route_after_verify({"scope": "verified"}) == "__end__"


def test_reasoning_model_defaults_to_low_effort(monkeypatch):
    from types import SimpleNamespace
    from rag_system.core import factory

    monkeypatch.delenv("REASONING_EFFORT", raising=False)
    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "ChatOpenAI", fake_chat_openai)
    config = SimpleNamespace(
        verify_ssl=False,
        chat_model="gpt-oss-20b",
        llm_api_base="http://llm.invalid/v1",
        llm_api_key="dummy",
    )

    component_factory = factory.ComponentFactory(config)
    try:
        component_factory.create_llm()
    finally:
        component_factory._http_client.close()

    assert captured["reasoning_effort"] == "low"


def test_stream_uses_verified_final_generation_not_preverify_tokens(monkeypatch):
    import api

    async def preverify_stream(*, trace, **kwargs):
        yield "依據第99條，這是尚未通過引用核對的暫存輸出。"
        trace["final_generation"] = "無據引用已安全終止，請重新查詢。"
        trace["actions"] = ["verify=failed_safe(ungrounded_citation)"]

    monkeypatch.setattr(api, "astream_query", preverify_stream)
    api, client = _intranet_client(monkeypatch)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "rag",
            "messages": [{"role": "user", "content": "請說明第99條"}],
            "stream": True,
        },
    )

    _, content = _stream_content(response.text)
    assert response.status_code == 200
    assert "第99條" not in content
    assert content == "無據引用已安全終止，請重新查詢。" + api.ANSWER_DISCLAIMER


def test_health_is_not_ready_when_configuration_is_invalid(monkeypatch):
    import api

    monkeypatch.setattr(api, "config", None)
    response = TestClient(api.app).get("/health")

    assert response.status_code == 503


def test_health_response_is_preserved_for_valid_configuration(monkeypatch):
    import api

    monkeypatch.setattr(api, "config", object())
    response = TestClient(api.app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["model_loaded"] is True


def test_workflow_cache_invalidation_clears_classic_and_react_caches(monkeypatch, tmp_path):
    from rag_system.agent import graph, react_workflow
    from rag_system.core.retrieval_generation import current_retrieval_generation

    marker = tmp_path / "retrieval-generation"
    monkeypatch.setenv("RAG_RETRIEVAL_GENERATION_FILE", str(marker))

    graph._WORKFLOW_CACHE["classic"] = object()
    react_workflow._REACT_CACHE["react"] = object()
    before = current_retrieval_generation()

    graph.invalidate_workflow_cache()

    assert graph._WORKFLOW_CACHE == {}
    assert react_workflow._REACT_CACHE == {}
    assert current_retrieval_generation() != before


def test_retrieval_generation_marker_is_shared_across_processes(monkeypatch, tmp_path):
    from rag_system.core.retrieval_generation import (
        bump_retrieval_generation,
        current_retrieval_generation,
    )

    marker = tmp_path / "retrieval-generation"
    monkeypatch.setenv("RAG_RETRIEVAL_GENERATION_FILE", str(marker))
    assert current_retrieval_generation() == "0"

    bumped = bump_retrieval_generation()

    assert bumped != "0"
    assert current_retrieval_generation() == bumped
    assert marker.stat().st_mode & 0o777 == 0o600


def test_workflow_build_cannot_repopulate_stale_generation(monkeypatch, tmp_path):
    from rag_system.agent import graph
    from rag_system.core.retrieval_generation import bump_retrieval_generation

    marker = tmp_path / "retrieval-generation"
    monkeypatch.setenv("RAG_RETRIEVAL_GENERATION_FILE", str(marker))
    graph._WORKFLOW_CACHE.clear()
    build_started = threading.Event()
    release_build = threading.Event()

    class Config:
        temperature = 0
        audit_log_dir = tmp_path

        def __hash__(self):
            return 42

        def validate(self):
            return None

    class FakeWorkflow:
        def add_node(self, *args, **kwargs):
            pass

        def set_entry_point(self, *args, **kwargs):
            pass

        def add_conditional_edges(self, *args, **kwargs):
            pass

        def add_edge(self, *args, **kwargs):
            pass

        def compile(self):
            return object()

    monkeypatch.setattr(graph, "StateGraph", lambda _state: FakeWorkflow())
    monkeypatch.setattr(graph, "create_llm", lambda _config: object())
    monkeypatch.setattr(graph, "AuditLogger", lambda _path: object())
    for name in (
        "create_classify_node", "create_reject_node", "create_security_block_node",
        "create_passthrough_node", "create_capability_node", "create_prc_block_node",
        "create_generate_node", "create_verify_node",
    ):
        monkeypatch.setattr(graph, name, lambda *args, **kwargs: object())

    def blocking_retrieve(_config):
        build_started.set()
        release_build.wait(timeout=5)
        return object()

    monkeypatch.setattr(graph, "create_retrieve_node", blocking_retrieve)
    worker = threading.Thread(target=graph.create_rag_workflow, args=(Config(),), daemon=True)
    worker.start()
    assert build_started.wait(timeout=5)

    bump_retrieval_generation()
    release_build.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert graph._WORKFLOW_CACHE == {}


class _Upload:
    def __init__(self, filename="law.md", content=b"law"):
        self.filename = filename
        self._content = content

    async def read(self):
        return self._content


def test_document_mutations_invalidate_cached_retrieval(monkeypatch, tmp_path):
    import api
    import rag_system.services.ingestion as ingestion_module

    invalidations = []
    monkeypatch.setattr(api, "config", object())
    monkeypatch.setattr(api, "audit", None)
    monkeypatch.setattr(api, "invalidate_workflow_cache", lambda: invalidations.append(True))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "converted_md").mkdir(parents=True)

    class FakePipeline:
        def __init__(self, *args, **kwargs):
            pass

        def process_bytes(self, content, filename):
            return {
                "original_file": filename,
                "converted_path": f"data/converted_md/{filename}",
                "indexed": True,
                "message": "indexed",
            }

    class FakeIngestionService:
        def __init__(self, *args, **kwargs):
            pass

        def delete_document(self, filename):
            return 1

        def clear_index(self):
            pass

        def index_directory(self, *args, **kwargs):
            return {"success": 1, "failed": 0}

    monkeypatch.setattr(api, "ConversionPipeline", FakePipeline)
    monkeypatch.setattr(ingestion_module, "IngestionService", FakeIngestionService)

    asyncio.run(api.upload_file(_Upload(), True, True, "intranet:test"))
    asyncio.run(api.delete_document("law.md", "intranet:test"))
    asyncio.run(api.upload_files_batch([_Upload("batch.md")], True, "intranet:test"))
    asyncio.run(api.reindex_all("intranet:test"))

    assert len(invalidations) == 4


def test_article_fast_path_does_not_mutate_shared_bm25_k():
    from rag_system.services.retrieval import RetrievalService, MAX_BM25_K

    started = threading.Event()
    release = threading.Event()
    observed_k = []

    class BlockingRetriever:
        def __init__(self):
            self.k = 3

        def invoke(self, question):
            observed_k.append(self.k)
            started.set()
            release.wait(timeout=5)
            return [Document(page_content="第46條\n內容", metadata={})]

    service = object.__new__(RetrievalService)
    retriever = BlockingRetriever()
    service.bm25_retriever = retriever
    worker = threading.Thread(
        target=service._bm25_article_hits,
        args=("第46條", {46}),
        daemon=True,
    )
    worker.start()
    assert started.wait(timeout=5)
    try:
        assert retriever.k == 3
    finally:
        release.set()
        worker.join(timeout=5)

    assert observed_k == [MAX_BM25_K]
    assert not worker.is_alive()


def test_conversation_history_selects_latest_rows_then_orders_ascending():
    from rag_system.services.conversation_store import ConversationStore

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params):
            captured["query"] = " ".join(query.split())
            captured["params"] = params

        def fetchall(self):
            return [("user", "recent-1"), ("assistant", "recent-2")]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return Cursor()

    store = object.__new__(ConversationStore)
    store._get_conn = lambda: Connection()

    history = store.get_history("session-1", limit=2)

    assert history == [("user", "recent-1"), ("assistant", "recent-2")]
    assert "ORDER BY created_at DESC, id DESC LIMIT %s" in captured["query"]
    assert captured["query"].endswith("ORDER BY created_at ASC, id ASC")
    assert captured["params"] == ("session-1", 2)
