import logging
import sys
import os
# Ensure package visibility
sys.path.append(os.getcwd())

from rag_system.config import RAGConfig
from rag_system.rag_service import RAGService
from dotenv import load_dotenv

# Setup logging to see the internal steps
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

def main():
    # Reload env vars in case user changed them
    load_dotenv(override=True)
    
    try:
        config = RAGConfig.from_env()
        service = RAGService(config)
        
        question = "軍人權益事件處理法第15條規定了什麼？"
        print(f"\n====== query: {question} ======")
        
        # This calls the new 'query' method in rag_service.py
        docs = service.query(question)
        
        print(f"\n====== Final Result ======")
        if docs:
            print(f"Retrieved {len(docs)} document(s).")
            for i, doc in enumerate(docs):
                print(f"--- Document {i+1} ---")
                print(f"Source: {doc.metadata.get('source')}")
                print(f"Content Preview: {doc.page_content[:200]}...")
        else:
            print("No documents retrieved.")
            
    except Exception as e:
        print(f"Error during execution: {e}")

if __name__ == "__main__":
    main()

