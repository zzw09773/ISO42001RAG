import logging
import sys
import os
from pathlib import Path
sys.path.append(os.getcwd())

from rag_system.config import RAGConfig
from rag_system.rag_service import RAGService
from dotenv import load_dotenv

logging.basicConfig(level=logging.ERROR)

def check_db_content():
    load_dotenv(override=True)
    config = RAGConfig.from_env()
    service = RAGService(config)
    
    print("\n--- Checking Vector Store Content ---")
    # Try to find ANYTHING containing "軍人權益"
    try:
        results = service.vectorstore.similarity_search_with_score("軍人權益", k=10)
        print(f"Found {len(results)} chunks matching '軍人權益'.")
        
        seen_sources = set()
        for doc, score in results:
            src = doc.metadata.get("source", "Unknown")
            seen_sources.add(src)
            # print(f"Score: {score:.4f} | Source: {src} | Content: {doc.page_content[:50]}...")
            
        print(f"Sources found in vector DB: {seen_sources}")
        
        if "軍人權益事件處理法.md" not in seen_sources:
            print("\n[WARNING] '軍人權益事件處理法.md' seems MISSING from the top vector search results.")
        else:
             print("\n[OK] '軍人權益事件處理法.md' is present in vector DB.")

    except Exception as e:
        print(f"Vector search error: {e}")

    print("\n--- Checking Docstore (Parent Documents) ---")
    # Check if docstore directory has files
    docstore_path = Path("./data/processed/docstore")
    if not docstore_path.exists():
        print("Docstore directory does NOT exist.")
    else:
        files = list(docstore_path.glob("*"))
        print(f"Docstore contains {len(files)} files (chunks).")

if __name__ == "__main__":
    check_db_content()
