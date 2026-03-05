import asyncio
from rag_system.core.config import RAGConfig
from rag_system.agent.graph import astream_query

async def main():
    config = RAGConfig.from_env()
    print("Starting stream...")
    async for chunk in astream_query("Article 8", config=config):
        print(f"CHUNK: {repr(chunk)}")
    print("Stream finished.")

asyncio.run(main())
