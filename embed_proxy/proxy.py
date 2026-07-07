"""
Embedding Proxy — OpenAI ↔ Triton 格式轉譯

接收 OpenAI 格式的 /v1/embeddings 請求，
使用 tritonclient gRPC 連線 Triton InferenceServer，
再將回應轉譯回 OpenAI 格式回傳。

可觀測性（2026-06-12）：所有失敗均寫入 stdout（docker logs 可見），
並附 TRITON_GRPC_URL / model 內容；另提供 /ready 診斷端點，回報
Triton 連線、模型狀態與「模型實際期望的 input/output tensor」，
供內網排查 embed 500（最常見：gRPC 埠 9000/9001 不符、模型名不符、
tensor 名稱/形狀不符）。
"""
import logging
import os
import time

import numpy as np
import tritonclient.grpc as grpcclient
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [embed-proxy] %(levelname)s %(message)s",
)
logger = logging.getLogger("embed-proxy")

app = FastAPI(title="Embedding Proxy (OpenAI ↔ Triton)")

# 注意：compose 預設 EMBED_GRPC_PORT=9000；若 Triton gRPC 實際為 9001，
# 請於 .env 設 EMBED_GRPC_PORT=9001（否則每個 embed 連不上 → 500）。
TRITON_GRPC_URL = os.environ.get("TRITON_GRPC_URL", "localhost:9001")
TRITON_MODEL = os.environ.get("TRITON_MODEL", "nv-embed-v2")
logger.info("啟動：TRITON_GRPC_URL=%s TRITON_MODEL=%s", TRITON_GRPC_URL, TRITON_MODEL)

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
    """存活檢查（僅代表 proxy 進程活著，不代表 Triton 可達）。
    Triton 連線診斷請用 /ready。"""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """診斷端點：檢查 Triton 連線、模型狀態，並回報模型實際期望的
    input/output tensor。內網排查 embed 500 時第一個打這個。"""
    info = {
        "triton_grpc_url": TRITON_GRPC_URL,
        "triton_model": TRITON_MODEL,
        "server_live": None,
        "server_ready": None,
        "model_ready": None,
        "expected_inputs": None,
        "expected_outputs": None,
        "error": None,
    }
    try:
        client = get_client()
        info["server_live"] = client.is_server_live()
        info["server_ready"] = client.is_server_ready()
        info["model_ready"] = client.is_model_ready(TRITON_MODEL)
        meta = client.get_model_metadata(TRITON_MODEL, as_json=True)
        info["expected_inputs"] = [
            {"name": i.get("name"), "datatype": i.get("datatype"), "shape": i.get("shape")}
            for i in meta.get("inputs", [])
        ]
        info["expected_outputs"] = [
            {"name": o.get("name"), "datatype": o.get("datatype"), "shape": o.get("shape")}
            for o in meta.get("outputs", [])
        ]
        # 與本 proxy 送出的 tensor 名稱比對提示
        in_names = {i["name"] for i in info["expected_inputs"]}
        info["proxy_sends_inputs"] = ["query (dims[1] BYTES)", "documents (dims[1,1] BYTES)"]
        info["proxy_expects_output"] = "embeddings"
        hints = []
        if not ({"query", "documents"} & in_names):
            hints.append("⚠ 模型 input 名稱與 proxy 預期的 query/documents 不符——需調整 proxy 或模型 config")
        if info["expected_outputs"] and not any(o["name"] == "embeddings" for o in info["expected_outputs"]):
            hints.append("⚠ 模型 output 名稱非 'embeddings'——需調整 proxy")
        info["hints"] = hints or ["input/output tensor 名稱與 proxy 預期相符"]
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
        info["hints"] = [
            f"⚠ 無法連線/查詢 Triton（{TRITON_GRPC_URL}）。常見原因："
            "(1) gRPC 埠不符（compose 預設 9000，實際可能 9001 → 設 .env EMBED_GRPC_PORT=9001）；"
            "(2) 主機/網路不通；(3) 模型未載入或名稱不符。",
        ]
        logger.error("/ready 診斷失敗：%s", info["error"])
    return JSONResponse(info)


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
        # 寫入 stdout（docker logs 可見）並附連線/模型內容，便於內網排查
        logger.exception(
            "embed 失敗 url=%s model=%s input_type=%s n_texts=%d",
            TRITON_GRPC_URL, TRITON_MODEL, input_type, len(texts),
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": f"{type(e).__name__}: {e}",
                "triton_grpc_url": TRITON_GRPC_URL,
                "triton_model": TRITON_MODEL,
                "hint": "打 GET /ready 看 Triton 連線與模型 tensor 診斷。"
                        "若埠不符請設 .env EMBED_GRPC_PORT（9000/9001）。",
            },
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
