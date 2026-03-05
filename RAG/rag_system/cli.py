"""
Minimal CLI and HTTP entrypoints for running the legal RAG workflow without notebooks.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict

# Updated imports based on new modular structure
from .core.config import RAGConfig
from .agent.graph import create_llm, create_rag_workflow, run_query

import logging
logger = logging.getLogger(__name__)


def _run_single_query(question: str) -> str:
    """Execute a single query and return the generated answer."""
    config = RAGConfig.from_env()
    config.validate()
    state = run_query(
        question=question,
        config=config,
    )
    return state.get("generation", "")


def _build_workflow():
    """Build a reusable workflow for the lightweight HTTP server."""
    config = RAGConfig.from_env()
    config.validate()
    llm = create_llm(config)
    workflow = create_rag_workflow(config, llm=llm)
    return workflow


def _handle_query(workflow, question: str) -> Dict[str, Any]:
    """Invoke the workflow with a basic graph state."""
    initial_state = {
        "question": question,
        "generation": "",
        "messages": [("user", question)],
        "collection": "",
        "retrieved_docs": [],
    }
    result = workflow.invoke(initial_state, config={"recursion_limit": 50})
    return {
        "generation": result.get("generation", ""),
    }


def command_retrieve(args: argparse.Namespace) -> None:
    """Run a retrieval-only query for debugging."""
    # Use new RetrievalService
    from .services.retrieval import RetrievalService

    try:
        config = RAGConfig.from_env()
        config.validate()
        service = RetrievalService(config)

        logger.info(f"Running retrieval for: {args.question}")
        docs = service.query(args.question)
        
        if not docs:
            print("No documents retrieved.")
            return

        print(f"\nFound {len(docs)} document(s):")
        for i, doc in enumerate(docs, 1):
            source = doc.metadata.get("source", "Unknown")
            print(f"\n--- Document {i} ({source}) ---")
            print(doc.page_content)
            print("-" * 40)
            
    except Exception as exc:
        sys.stderr.write(f"Error running retrieval: {exc}\n")
        sys.exit(1)


def command_query(args: argparse.Namespace) -> None:
    """Run a single query from the command line."""
    try:
        answer = _run_single_query(args.question)
    except Exception as exc:  # pragma: no cover - defensive path
        sys.stderr.write(f"Error running query: {exc}\n")
        sys.exit(1)

    print(answer)


def command_serve(args: argparse.Namespace) -> None:
    """Expose a minimal HTTP endpoint: POST /query {\"question\": \"...\"}."""
    try:
        workflow = _build_workflow()
    except Exception as exc:  # pragma: no cover - defensive path
        sys.stderr.write(f"Error initializing workflow: {exc}\n")
        sys.exit(1)

    def run_workflow(question: str) -> Dict[str, Any]:
        return _handle_query(workflow, question)

    class QueryHandler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/query", "/"):
                self._send(404, {"error": "not found"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0

            raw_body = self.rfile.read(length) if length > 0 else b""
            try:
                data = json.loads(raw_body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "invalid JSON payload"})
                return

            question = (data.get("question") or "").strip()
            if not question:
                self._send(400, {"error": "missing 'question' field"})
                return

            try:
                result = run_workflow(question)
                self._send(200, result)
            except Exception as exc:  # pragma: no cover - defensive path
                self._send(500, {"error": f"failed to run query: {exc}"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            # Silence default HTTP server logging; rely on `log`.
            return

    server = HTTPServer((args.host, args.port), QueryHandler)
    logger.info(f"Serving RAG endpoint on http://{args.host}:{args.port}/query")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Minimal CLI/HTTP entrypoints for the legal RAG system.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    retrieve = subparsers.add_parser("retrieve", help="Run retrieval-only debugging.")
    retrieve.add_argument("question", help="The user query to test retrieval.")
    retrieve.set_defaults(func=command_retrieve)

    query = subparsers.add_parser("query", help="Run a single query.")
    query.add_argument("question", help="The user question to answer.")
    query.set_defaults(func=command_query)

    serve = subparsers.add_parser("serve", help="Run a minimal HTTP server.")
    serve.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host/IP to bind (default: 0.0.0.0).",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to bind (default: 8080).",
    )
    serve.set_defaults(func=command_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    from dotenv import load_dotenv
    load_dotenv(override=True)
    
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
