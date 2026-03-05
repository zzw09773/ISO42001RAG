import os
import sys
import warnings
from typing import List, Any, Dict
import httpx
import logging
import json
from datetime import datetime # Added datetime import

# Configure basic logging
LOG_LEVEL = os.environ.get("RAG_LOG_LEVEL", "INFO").upper()
# Set up a logger that outputs to stderr
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    stream=sys.stderr
)
# Create a logger for the common module
logger = logging.getLogger(__name__)

# Global flag to control logging output (default: quiet for initial simple log)
_QUIET_MODE = os.environ.get("RAG_LOG_VERBOSE", "0").lower() not in ("1", "true", "yes", "on")

def set_quiet_mode(quiet: bool = True):
    """Enable or disable quiet mode globally (for backward compatibility with simple log)."""
    global _QUIET_MODE
    _QUIET_MODE = quiet

def log(msg: str, level: str = "info"):
    """
    Simple logging function, now using Python's standard logging.
    Respects _QUIET_MODE for backward compatibility with previous simple log calls.
    """
    if not _QUIET_MODE:
        if level.lower() == "debug":
            logger.debug(msg)
        elif level.lower() == "info":
            logger.info(msg)
        elif level.lower() == "warning":
            logger.warning(msg)
        elif level.lower() == "error":
            logger.error(msg)
        elif level.lower() == "critical":
            logger.critical(msg)
        else:
            logger.info(msg) # Default to info

def log_json(event_name: str, data: Dict[str, Any], level: str = "info"):
    """
    Logs structured data as a JSON string.
    Useful for audit logs and machine readability.
    """
    log_entry = {
        "event": event_name,
        "timestamp": datetime.utcnow().isoformat() + "Z", # ISO 8601 UTC
        "data": data
    }
    json_msg = json.dumps(log_entry, ensure_ascii=False)
    log(json_msg, level)


class LocalApiEmbeddings:
    """
    A wrapper for a local embedding API that mimics LangChain's Embeddings interface.
    It includes batching and retry logic.
    """
    def __init__(self, api_base: str, api_key: str, model_name: str = "nvidia/nv-embed-v2", batch_size: int = 8, verify_ssl: bool = False):
        self.api_base = api_base.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        self.batch_size = batch_size
        
        if verify_ssl:
            verify_context = True
        else:
            warnings.warn(
                "SSL verification is disabled. This is insecure and should only be used for development.",
                UserWarning
            )
            verify_context = False

        # Configure a client with built-in retries for robustness.
        if verify_ssl:
            transport = httpx.HTTPTransport(retries=3)
        else:
            transport = httpx.HTTPTransport(retries=3, verify=False)
        timeout_config = httpx.Timeout(600.0, connect=30.0)
        self.client = httpx.Client(verify=verify_context, transport=transport, timeout=timeout_config, follow_redirects=True)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embeds a list of documents, handling batching automatically."""
        all_embeddings = []
        num_texts = len(texts)
        logger.info(f"Embedding {num_texts} documents in batches of {self.batch_size}...")
        
        for i in range(0, num_texts, self.batch_size):
            batch = texts[i:i + self.batch_size]
            num_batches = (num_texts + self.batch_size - 1) // self.batch_size
            logger.info(f"Processing batch {i//self.batch_size + 1}/{num_batches}")
            try:
                batch_embeddings = self._embed_batch(batch)
                all_embeddings.extend(batch_embeddings)
            except httpx.HTTPStatusError as e:
                logger.error(f"Batch failed with status {e.response.status_code}: {e.response.text}")
                raise  # Re-raise the exception after logging
            except httpx.RequestError as e:
                logger.error(f"Batch failed due to request error: {e}")
                raise

        return all_embeddings

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embeds a single batch of documents."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model_name,
            "input": texts,
            "encoding_format": "float"
        }
        
        logger.info(f"Sending {len(texts)} texts to {self.api_base}/embeddings")
        response = self.client.post(f"{self.api_base}/embeddings", headers=headers, json=payload)
        response.raise_for_status()  # Will raise an exception for 4xx/5xx responses
        
        data = response.json()
        embeddings = [item["embedding"] for item in data["data"]]
        logger.info(f"Successfully received {len(embeddings)} vectors.")
        return embeddings

    def embed_query(self, text: str) -> List[float]:
        """Embeds a single query."""
        return self.embed_documents([text])[0]

