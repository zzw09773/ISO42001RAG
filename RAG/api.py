import time
import uuid
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uvicorn
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from dotenv import load_dotenv

from rag_system.core.config import RAGConfig
from rag_system.core.audit_logger import AuditLogger, QueryTimer
from rag_system.agent.graph import run_query, astream_query
from rag_system.services.converter import FileConverter, ConversionPipeline, ConversionError
from rag_system.services.conversation_store import ConversationStore

# Initialize FastAPI app
app = FastAPI(title="RAG Agent API", description="OpenAI-compatible API for Agentic RAG")

# Load Config globally to avoid reloading on every request
load_dotenv(override=True)
config = None
audit = None
conv_store = None

try:
    config = RAGConfig.from_env()
    config.validate()
    audit = AuditLogger(config.audit_log_dir)
    conv_store = ConversationStore(config.conn_string)
    print("RAG Configuration loaded successfully.")
except Exception as e:
    print(f"Warning: Configuration load failed: {e}. Ensure .env is set.")

# --- Pydantic Models for OpenAI API Compatibility ---

class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "rag-agent"
    messages: List[Message]
    temperature: Optional[float] = 0.0
    stream: Optional[bool] = False

class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: Message
    finish_reason: str

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionResponseChoice]
    usage: Optional[Dict[str, int]] = None

# --- Routes ---

@app.get("/health")
async def health_check():
    return {"status": "healthy", "model_loaded": config is not None}

@app.get("/v1/models")
async def list_models():
    # Return a dummy model list so OpenWebUI can see it
    return {
        "object": "list",
        "data": [
            {
                "id": "rag-agent",
                "object": "model",
                "created": 1677610602,
                "owned_by": "user",
            }
        ]
    }

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, raw_request: Request = None):
    if not config:
        raise HTTPException(status_code=500, detail="Server configuration invalid.")

    # Generate session_id from request header or create a new one
    session_id = ""
    if raw_request:
        session_id = raw_request.headers.get("x-session-id", "")
    if not session_id:
        session_id = str(uuid.uuid4())

    try:
        timer = QueryTimer()

        # 1. Convert OpenAI history to LangChain format
        langchain_messages = []
        for msg in request.messages:
            if msg.role == "user":
                langchain_messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                langchain_messages.append(AIMessage(content=msg.content))
            elif msg.role == "system":
                langchain_messages.append(SystemMessage(content=msg.content))
        
        # Extract last user question
        last_user_content = ""
        for msg in reversed(request.messages):
            if msg.role == "user":
                last_user_content = msg.content
                break
        
        if not last_user_content:
             raise HTTPException(status_code=400, detail="No user message found.")

        # Load conversation history from DB (if available)
        if conv_store:
            stored_history = conv_store.get_history(
                session_id, limit=config.conversation_history_limit
            )
            if stored_history:
                # Prepend stored history before current messages
                history_msgs = []
                for role, content in stored_history:
                    if role == "user":
                        history_msgs.append(HumanMessage(content=content))
                    elif role == "assistant":
                        history_msgs.append(AIMessage(content=content))
                # Only use stored history if current request doesn't already include it
                if len(langchain_messages) <= 2:
                    langchain_messages = history_msgs + langchain_messages

        # Construct IDs early
        chat_id = f"chatcmpl-{uuid.uuid4()}"
        created_time = int(time.time())

        # =====================================================================
        # STREAMING PATH: Use astream_query only (no blocking run_query)
        # =====================================================================
        if request.stream:
            async def event_generator():
                # First chunk: role
                first_chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant"},
                            "finish_reason": None
                        }
                    ]
                }
                yield f"data: {json.dumps(first_chunk)}\n\n"

                # Stream tokens from the LangGraph workflow
                full_response = []
                stream_timer = QueryTimer()
                with stream_timer:
                    async for token in astream_query(
                        question=last_user_content,
                        config=config,
                        messages=langchain_messages,
                    ):
                        if token:
                            full_response.append(token)
                            content_chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created_time,
                                "model": request.model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": token},
                                        "finish_reason": None
                                    }
                                ]
                            }
                            yield f"data: {json.dumps(content_chunk, ensure_ascii=False)}\n\n"

                # Final chunk
                final_chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop"
                        }
                    ]
                }
                yield f"data: {json.dumps(final_chunk)}\n\n"
                yield "data: [DONE]\n\n"

                # Post-stream: persist and audit
                streamed_content = "".join(full_response)
                if conv_store:
                    conv_store.save_message(session_id, "user", last_user_content)
                    conv_store.save_message(session_id, "assistant", streamed_content)
                if audit:
                    is_rejection = "無法回答與法律無關的問題" in streamed_content
                    if is_rejection:
                        audit.log_rejection(session_id, last_user_content)
                    else:
                        audit.log_query(
                            session_id=session_id,
                            user_query=last_user_content,
                            scope_check="in_scope",
                            model_name=config.chat_model,
                            response_time_ms=stream_timer.elapsed_ms,
                        )

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        # =====================================================================
        # NON-STREAMING PATH: Run query in background thread to avoid blocking
        # =====================================================================
        import asyncio
        with timer:
            result_state = await asyncio.to_thread(
                run_query,
                question=last_user_content,
                config=config,
                messages=langchain_messages,
            )

        # Extract the response
        response_content = ""
        if result_state.get("generation"):
             response_content = result_state["generation"]
        elif result_state.get("messages"):
            last_msg = result_state["messages"][-1]
            if isinstance(last_msg, AIMessage) or hasattr(last_msg, 'content'):
                response_content = last_msg.content
            else:
                response_content = str(last_msg)
        else:
            response_content = "Error: No response generated from the RAG agent."

        # Persist conversation to DB
        if conv_store:
            conv_store.save_message(session_id, "user", last_user_content)
            conv_store.save_message(session_id, "assistant", response_content)

        # Audit log (ISO 42001)
        if audit:
            is_rejection = "無法回答與法律無關的問題" in response_content
            if is_rejection:
                audit.log_rejection(session_id, last_user_content)
            else:
                audit.log_query(
                    session_id=session_id,
                    user_query=last_user_content,
                    scope_check="out_of_scope" if is_rejection else "in_scope",
                    model_name=config.chat_model,
                    tokens_used=len(response_content) // 4,
                    response_time_ms=timer.elapsed_ms,
                )

        return ChatCompletionResponse(
            id=chat_id,
            created=created_time,
            model=request.model,
            choices=[
                ChatCompletionResponseChoice(
                    index=0,
                    message=Message(role="assistant", content=response_content),
                    finish_reason="stop"
                )
            ]
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# --- File Upload Endpoints ---

@app.post("/v1/upload")
async def upload_file(
    file: UploadFile = File(...),
    index_after_convert: bool = Form(default=True),
    overwrite: bool = Form(default=False)
):
    """
    Upload a document file to be converted to Markdown and optionally indexed.

    Supported formats: PDF, RTF, DOCX, TXT, MD

    Args:
        file: The file to upload
        index_after_convert: Whether to index the file after conversion (default: True)
        overwrite: If false, checks hash to prevent duplicate content upload.

    Returns:
        Conversion and indexing results
    """
    if not config:
        raise HTTPException(status_code=500, detail="Server configuration invalid.")

    # Check file extension
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()

    if ext not in FileConverter.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {ext}. "
                   f"Supported: {', '.join(FileConverter.SUPPORTED_EXTENSIONS)}"
        )

    try:
        # Read file content
        content = await file.read()
        
        # Hash checking to avoid duplicates
        import hashlib
        file_hash = hashlib.sha256(content).hexdigest()
        
        if not overwrite and index_after_convert:
            from rag_system.services.ingestion import IngestionService
            ingestion_service = IngestionService(config)
            
            # Check for existing hash
            from sqlalchemy import text
            with ingestion_service.vectorstore._make_session() as session:
                res = session.execute(
                    text("SELECT 1 FROM langchain_pg_embedding WHERE cmetadata->>'hash' = :hash LIMIT 1"),
                    {"hash": file_hash}
                ).fetchone()
                
                if res:
                    return {
                        "status": "skipped",
                        "original_file": filename,
                        "indexed": False,
                        "message": f"Document with identical content already exists."
                    }

        # Get output directory (use repo's converted_md folder)
        converted_dir = Path("./data/converted_md")
        converted_dir.mkdir(parents=True, exist_ok=True)

        if index_after_convert:
            # Use pipeline for conversion + indexing
            pipeline = ConversionPipeline(config, converted_dir=converted_dir)
            result = pipeline.process_bytes(content, filename)
        else:
            # Just convert without indexing
            converter = FileConverter(output_dir=converted_dir)
            md_path = converter.convert_bytes(content, filename)
            result = {
                'original_file': filename,
                'converted_path': str(md_path),
                'indexed': False,
                'message': f"File converted successfully: {md_path.name}"
            }

        # Audit log
        if audit:
            audit.log_upload(
                filename=filename,
                indexed=result.get('indexed', False),
                message=result['message'],
            )

        return {
            "status": "success" if result.get('indexed', True) else "partial",
            "original_file": result['original_file'],
            "converted_path": result.get('converted_path'),
            "indexed": result.get('indexed', False),
            "message": result['message']
        }

    except ConversionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.delete("/v1/documents/{filename}")
async def delete_document(filename: str):
    """
    Delete a document from the RAG index and local storage.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Server configuration invalid.")
        
    try:
        from rag_system.services.ingestion import IngestionService
        ingestion_service = IngestionService(config)
        
        # 1. Delete from vector DB and docstore
        deleted_chunks = ingestion_service.delete_document(filename)
        
        # 2. Delete local Markdown file if it exists
        converted_dir = Path("./data/converted_md")
        md_file = converted_dir / filename
        file_deleted = False
        
        if md_file.exists():
            md_file.unlink()
            file_deleted = True
            
        # Also try to delete the .md version if the original filename had a different extension
        if Path(filename).suffix != '.md':
            alt_md = converted_dir / f"{Path(filename).stem}.md"
            if alt_md.exists():
                alt_md.unlink()
                file_deleted = True
                
        if deleted_chunks == 0 and not file_deleted:
            raise HTTPException(status_code=404, detail=f"Document '{filename}' not found.")
            
        return {
            "status": "success",
            "message": f"Deleted document '{filename}'",
            "details": {
                "chunks_deleted": deleted_chunks,
                "file_deleted": file_deleted
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")


@app.post("/v1/upload/batch")
async def upload_files_batch(
    files: List[UploadFile] = File(...),
    index_after_convert: bool = Form(default=True)
):
    """
    Upload multiple document files for batch conversion and indexing.

    Args:
        files: List of files to upload
        index_after_convert: Whether to index files after conversion

    Returns:
        Batch processing results
    """
    if not config:
        raise HTTPException(status_code=500, detail="Server configuration invalid.")

    results = []
    converted_dir = Path("./data/converted_md")
    converted_dir.mkdir(parents=True, exist_ok=True)

    pipeline = ConversionPipeline(config, converted_dir=converted_dir) if index_after_convert else None
    converter = FileConverter(output_dir=converted_dir) if not index_after_convert else None

    for file in files:
        filename = file.filename or "unknown"
        ext = Path(filename).suffix.lower()

        if ext not in FileConverter.SUPPORTED_EXTENSIONS:
            results.append({
                "file": filename,
                "status": "error",
                "message": f"Unsupported format: {ext}"
            })
            continue

        try:
            content = await file.read()

            if pipeline:
                result = pipeline.process_bytes(content, filename)
                results.append({
                    "file": filename,
                    "status": "success" if result.get('indexed') else "partial",
                    "converted_path": result.get('converted_path'),
                    "indexed": result.get('indexed', False),
                    "message": result['message']
                })
            else:
                md_path = converter.convert_bytes(content, filename)
                results.append({
                    "file": filename,
                    "status": "success",
                    "converted_path": str(md_path),
                    "indexed": False,
                    "message": f"Converted to {md_path.name}"
                })

            # Audit log per file
            if audit:
                audit.log_upload(
                    filename=filename,
                    indexed=result.get('indexed', False) if pipeline else False,
                    message=f"Batch upload: {filename}",
                )

        except Exception as e:
            results.append({
                "file": filename,
                "status": "error",
                "message": str(e)
            })

    success_count = sum(1 for r in results if r['status'] == 'success')

    return {
        "total": len(files),
        "success": success_count,
        "failed": len(files) - success_count,
        "results": results
    }


@app.get("/v1/documents")
async def list_documents():
    """
    List all indexed documents in the converted_md directory.
    """
    converted_dir = Path("./data/converted_md")

    if not converted_dir.exists():
        return {"documents": [], "count": 0}

    documents = []
    for f in converted_dir.glob("*.md"):
        documents.append({
            "name": f.name,
            "path": str(f),
            "size_bytes": f.stat().st_size,
            "modified": f.stat().st_mtime
        })

    # Sort by modified time (newest first)
    documents.sort(key=lambda x: x['modified'], reverse=True)

    return {
        "documents": documents,
        "count": len(documents)
    }


@app.post("/v1/reindex")
async def reindex_all():
    """
    Reindex all documents in the converted_md directory.
    This clears the existing index and rebuilds it.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Server configuration invalid.")

    from rag_system.services.ingestion import IngestionService

    converted_dir = Path("./data/converted_md")

    if not converted_dir.exists():
        raise HTTPException(status_code=404, detail="No documents directory found")

    try:
        # Initialize ingestion service
        ingestion_service = IngestionService(config)

        # Clear existing index
        ingestion_service.clear_index()

        # Re-initialize to ensure tables are recreated
        ingestion_service = IngestionService(config)

        # Index all markdown files
        results = ingestion_service.index_directory(converted_dir, pattern="*.md")

        # Audit log
        if audit:
            audit.log_reindex(
                success=results['success'],
                failed=results['failed'],
            )

        return {
            "status": "success",
            "indexed": results['success'],
            "failed": results['failed'],
            "message": f"Reindexed {results['success']} documents"
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Reindex failed: {str(e)}")


if __name__ == "__main__":
    # Run via: python api.py
    uvicorn.run(app, host="0.0.0.0", port=8000)
