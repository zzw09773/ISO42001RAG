"""GraphState definition for legal document RAG workflow.

Extends MessagesState to track the multi-node workflow state:
  classify → retrieve → generate → verify
"""
from typing import List, Optional
from langgraph.graph import MessagesState


class GraphState(MessagesState):
    """State structure for the multi-node legal document RAG workflow.

    Attributes:
        messages: Chat message history (inherited from MessagesState)
        question: The user's original question
        generation: The final generated answer with legal citations
        collection: Optional target collection name for legal documents
        retrieved_docs: Documents retrieved from the vector database
        scope: Classification result: 'legal' | 'reject'
        retry_count: Number of retrieve→verify retries (to prevent infinite loops)
    """
    question: str = ""
    generation: str = ""
    collection: str = ""
    retrieved_docs: list = []
    scope: str = ""       # 'legal' or 'reject'
    retry_count: int = 0
    feedback: str = ""