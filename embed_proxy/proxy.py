"""
Embedding Proxy — OpenAI ↔ Triton 格式轉譯

接收 OpenAI 格式的 /v1/embeddings 請求，
使用 tritonclient gRPC 連線 Triton InferenceServer，
再將回應轉譯回 OpenAI 格式回傳。
"""
import os
import time
import numpy as np
import tritonclient.grpc as grpcclient
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Embedding Proxy (OpenAI ↔ Triton)")

TRITON_GRPC_URL = os.environ.get("TRITON_GRPC_URL", "localhost:9001")
TRITON_MODEL = os.environ.get("TRITON_MODEL", "nv-embed-v2")

# 建立 gRPC client（持久連線）
_client = None

def get_client():
    global _client
    if _client is None:
        _client = grpcclient.InferenceServerClient(
            url=TRITON_GRPC_URL,
            verbose=False,
        )
    return _client


def embed_texts(texts: list[str], input_type: str = "passage") -> list[list[float]]:
    """使用 Triton gRPC 取得 embeddings（逐筆送以確保穩定性）"""
    client = get_client()
    all_embeddings = []

    for text in texts:
        if input_type == "query":
            # query 輸入：dims [1]，單字串
            input_tensor = grpcclient.InferInput("query", [1], "BYTES")
            input_tensor.set_data_from_numpy(np.array([text], dtype=object))
        else:
            # documents 輸入：dims [1, -1]，文件批次
            input_tensor = grpcclient.InferInput("documents", [1, 1], "BYTES")
            input_tensor.set_data_from_numpy(np.array([[text]], dtype=object))

        output = grpcclient.InferRequestedOutput("embeddings")

        result = client.infer(
            model_name=TRITON_MODEL,
            inputs=[input_tensor],
            outputs=[output],
            client_timeout=120,
        )

        # Triton 回傳 shape (1, 1, 4096)，取出 1D 向量
        emb = result.as_numpy("embeddings").flatten().tolist()
        all_embeddings.append(emb)

    return all_embeddings


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": TRITON_MODEL,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "triton",
            }
        ],
    }


@app.post("/v1/embeddings")
async def create_embeddings(request: Request):
    body = await request.json()

    # 解析 OpenAI 格式的 input
    raw_input = body.get("input", "")
    if isinstance(raw_input, str):
        texts = [raw_input]
    elif isinstance(raw_input, list):
        texts = [t if isinstance(t, str) else str(t) for t in raw_input]
    else:
        texts = [str(raw_input)]

    # input_type: "query" 或 "passage"（預設），對應 Triton 的 query/documents input
    input_type = body.get("input_type", "passage")

    try:
        embeddings = embed_texts(texts, input_type=input_type)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )

    # 組裝 OpenAI 格式回應
    data = []
    total_tokens = 0
    for i, emb in enumerate(embeddings):
        data.append(
            {
                "object": "embedding",
                "embedding": emb,
                "index": i,
            }
        )
        total_tokens += len(texts[i].split())

    return {
        "object": "list",
        "data": data,
        "model": body.get("model", TRITON_MODEL),
        "usage": {
            "prompt_tokens": total_tokens,
            "total_tokens": total_tokens,
        },
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PROXY_PORT", "8100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
