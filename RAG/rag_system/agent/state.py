"""GraphState definition for legal document RAG workflow.

Extends MessagesState to track the multi-node workflow state:
  classify → retrieve → generate → verify
"""
import operator
from typing import Annotated, List, Optional
from langgraph.graph import MessagesState


class GraphState(MessagesState):
    """State structure for the multi-node legal document RAG workflow.

    Attributes:
        messages: Chat message history (inherited from MessagesState)
        question: The user's original question
        generation: The final generated answer with legal citations
        collection: Optional target collection name for legal documents
        retrieved_docs: Documents retrieved from the vector database (formatted text)
        retrieved_sources: List of retrieved document source names (for ISO 42001 A.7 audit)
        citation_count: Number of "第X條" citations detected in generation (for A.6 monitoring)
        scope: Classification result: 'legal' | 'reject'
        retry_count: Number of retrieve→verify retries (to prevent infinite loops)
        tokens_used: Actual LLM token count from response.usage (for A.4 resource tracking)
    """
    question: str = ""
    generation: str = ""
    collection: str = ""
    retrieved_docs: list = []
    retrieved_sources: list = []   # NEW: filenames or filename#article_id
    citation_count: int = 0        # NEW: count of article references in answer
    scope: str = ""       # 'legal' | 'reject' | 'security_block' | 'passthrough'
    retry_count: int = 0
    feedback: str = ""
    session_id: str = ""
    client_ip: str = ""     # ISO 27001 A.8.15 — originating IP for audit/security trace
    audit_context: dict = {}  # request_id / OpenWebUI correlation fields
    threat_type: str = ""   # set by classify_node on security block
    security_reason: str = ""  # human-readable block reason
    tokens_used: int = 0           # NEW: real token count from LLM usage
    # ISO 42001 A.6 — workflow action trail. Annotated reducer makes each
    # node return value `["my_action"]` get APPENDED (not replaced) so the
    # final state has the full ordered list of steps the agent executed.
    actions: Annotated[list, operator.add] = []
