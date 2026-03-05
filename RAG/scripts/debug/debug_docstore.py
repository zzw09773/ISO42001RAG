import sys
import os
from pathlib import Path
import shutil

# Ensure package visibility
sys.path.append(os.getcwd())

from langchain.storage import LocalFileStore, EncoderBackedStore
from langchain_core.documents import Document
from langchain_core.load import dumps, loads

def debug_docstore():
    print("--- Debugging Docstore ---")
    base_path = Path("./data/processed/docstore_debug")
    
    # Clean up previous run
    if base_path.exists():
        shutil.rmtree(base_path)
    base_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Created debug dir: {base_path.absolute()}")
    
    # 1. Test Raw Store
    print("\nTesting Raw LocalFileStore...")
    raw_store = LocalFileStore(str(base_path))
    try:
        raw_store.mset([("test_key", b"test_value")])
        print("Write success.")
        val = raw_store.mget(["test_key"])
        print(f"Read back: {val}")
        
        files = list(base_path.glob("*\n"))
        print(f"Files in dir: {[f.name for f in files]}")
        
    except Exception as e:
        print(f"Raw Store Failed: {e}")
        
    # 2. Test Encoded Store (Same logic as RAGService)
    print("\nTesting EncoderBackedStore...")
    def _dumps(x):
        return dumps(x).encode('utf-8')
        
    def _loads(x):
        return loads(x.decode('utf-8'))

    encoded_store = EncoderBackedStore(
        store=raw_store,
        key_encoder=lambda x: x,
        value_serializer=_dumps,
        value_deserializer=_loads
    )
    
    doc = Document(page_content="Hello World", metadata={"id": 1})
    
    try:
        encoded_store.mset([("doc_1", doc)])
        print("Encoded Write success.")
        
        retrieved_docs = encoded_store.mget(["doc_1"])
        print(f"Read back doc: {retrieved_docs[0]}")
        
        files = list(base_path.glob("*\n"))
        print(f"Files in dir now: {[f.name for f in files]}")
        
    except Exception as e:
        print(f"Encoded Store Failed: {e}")

if __name__ == "__main__":
    debug_docstore()
