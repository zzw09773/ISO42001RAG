"""
Multi-node workflow nodes for the RAG agent.

Implements five distinct nodes for the LangGraph workflow:
  1. classify_node  — Route query (legal / reject / passthrough)
  2. reject_node    — Return ISO 42001 rejection
  3. retrieve_node  — Call retrieval service
  4. generate_node  — Generate answer with retrieved context
  5. verify_node    — Check citation quality
"""
import logging
import re
from typing import List, Callable

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ..core.prompts import AGENT_SYSTEM_PROMPT
from ..core.config import RAGConfig
from .state import GraphState
from .memory import ConversationSummarizer
from ..services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

# ISO 42001 A.9: Rejection message
REJECTION_MSG = "本系統僅提供法律文件檢索與解釋服務，無法回答與法律無關的問題。請提出與法律相關的查詢。"

MAX_RETRIES = 2

# ---------------------------------------------------------------------------
# Keywords for fast scope classification
# ---------------------------------------------------------------------------

_LEGAL_KEYWORDS = re.compile(
    r'(法|條|款|項|罰|刑|罪|訴|律|規|令|判|審|憲|章|辦法|懲罰|處分|'
    r'告訴|起訴|犯罪|民事|刑事|行政|訴訟|上訴|管轄|賠償|損害|契約|'
    r'合同|侵權|債務|繼承|婚姻|勞動|就業|著作權|專利|商標|'
    r'霸凌|騷擾|歧視|申訴|救濟|權益|復審|懲戒|違紀|軍人|'
    r'怎麼辦|怎麽辦|如何處理|可以.*嗎|有.*保護|'
    r'ISO|42001|AIMS|治理|合規|稽核|風險)',
    re.IGNORECASE
)

_CHAT_KEYWORDS = re.compile(
    r'^(你好|嗨|哈囉|hello|hi|hey|早安|午安|晚安|謝謝|感謝|再見|拜拜|'
    r'天氣|吃飯|好嗎|你是誰|你叫什麼|聊天|開心|無聊|笑話|故事|'
    r'寫程式|寫code|coding|python|javascript|幫我寫|'
    r'計算|算數|數學|幾加幾|多少錢)',
    re.IGNORECASE
)

# Pattern to detect legal article citations in generated text
_CITATION_PATTERN = re.compile(r'第\s*\d+\s*條|article\s*\d+', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Node 1: Classify
# ---------------------------------------------------------------------------

def create_classify_node() -> Callable:
    """Creates a node that classifies the query as legal, reject, or passthrough."""

    def classify_node(state: GraphState) -> dict:
        question = state.get("question", "")
        logger.info(f"--- CLASSIFY NODE --- question: {question[:80]}")

        # Open WebUI system tasks: let them pass through to LLM directly
        if question.strip().startswith("### Task:"):
            logger.info("Classification: passthrough (Open WebUI system task)")
            return {"scope": "passthrough"}

        if _LEGAL_KEYWORDS.search(question):
            logger.info("Classification: legal (keyword match)")
            return {"scope": "legal"}

        if _CHAT_KEYWORDS.search(question):
            logger.info("Classification: reject (chat keyword)")
            return {"scope": "reject"}

        # Ambiguous — default to legal
        logger.info("Classification: legal (ambiguous, default)")
        return {"scope": "legal"}

    return classify_node


# ---------------------------------------------------------------------------
# Node: Reject
# ---------------------------------------------------------------------------

def create_reject_node() -> Callable:
    """Creates a node that returns the ISO 42001 rejection message."""

    def reject_node(state: GraphState) -> dict:
        logger.info("--- REJECT NODE ---")
        return {
            "generation": REJECTION_MSG,
            "messages": [AIMessage(content=REJECTION_MSG)],
        }

    return reject_node


# ---------------------------------------------------------------------------
# Node: Passthrough (for Open WebUI system tasks)
# ---------------------------------------------------------------------------

def create_passthrough_node(llm: ChatOpenAI) -> Callable:
    """Creates a node that handles Open WebUI system tasks (title/tag generation)."""

    def passthrough_node(state: GraphState) -> dict:
        logger.info("--- PASSTHROUGH NODE ---")
        messages_input = state.get("messages", [])

        # Filter out non-standard messages — only keep Human/AI/System
        clean_msgs = []
        for msg in messages_input:
            if isinstance(msg, (HumanMessage, AIMessage, SystemMessage)):
                clean_msgs.append(msg)

        try:
            response = llm.invoke(clean_msgs)
            return {
                "generation": response.content,
                "messages": [AIMessage(content=response.content)],
            }
        except Exception as e:
            logger.error(f"Passthrough failed: {e}")
            return {
                "generation": "",
                "messages": [AIMessage(content="")],
            }

    return passthrough_node


# ---------------------------------------------------------------------------
# Query Expansion Helper
# ---------------------------------------------------------------------------

# Pattern to detect short article references (e.g., "第8條", "第 8 條", "第八條")
_ARTICLE_REF_PATTERN = re.compile(
    r'^第\s*([0-9一二三四五六七八九十百千]+)\s*條'
)

_CN_NUMS = {
    '一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
    '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
    '十一': '11', '十二': '12', '十三': '13', '十四': '14', '十五': '15',
    '十六': '16', '十七': '17', '十八': '18', '十九': '19', '二十': '20',
}


def _expand_query(question: str) -> str:
    """
    Expand short legal article queries for better vector search recall.

    Short queries like "第8條" lack semantic context for embeddings.
    This expands them to include more keywords so the embedding model
    can match the correct chunk.
    """
    q = question.strip()
    if len(q) > 15:
        return question

    m = _ARTICLE_REF_PATTERN.match(q)
    if not m:
        return question

    num = m.group(1)
    if num in _CN_NUMS:
        num = _CN_NUMS[num]

    return f"第 {num} 條 法律條文規定內容 第{num}條"


# ---------------------------------------------------------------------------
# Node 2: Retrieve
# ---------------------------------------------------------------------------

def create_retrieve_node(config: RAGConfig) -> Callable:
    """Creates a node that retrieves relevant legal documents."""
    retrieval_service = RetrievalService(config)

    # Token budget settings
    max_tokens = config.max_retrieval_tokens
    chars_per_token = 4

    def retrieve_node(state: GraphState) -> dict:
        question = state.get("question", "")
        retry_count = state.get("retry_count", 0)
        logger.info(f"--- RETRIEVE NODE --- (attempt {retry_count + 1})")

        # Query expansion for short article references
        search_query = _expand_query(question)
        if search_query != question:
            logger.info(f"Query expanded: '{question}' -> '{search_query}'")

        try:
            docs = retrieval_service.query(search_query)
        except Exception as e:
            logger.error(f"Retrieval failed: {e}")
            docs = []

        if not docs:
            logger.warning("No documents retrieved")
            return {"retrieved_docs": []}

        # Format with token budget
        formatted_parts = []
        tokens_used = 0

        for i, doc in enumerate(docs, 1):
            source = doc.metadata.get("source", "Unknown")
            content = doc.page_content
            header = f"=== 文件 {i} ===\n來源: {source}\n內容: "
            entry_tokens = (len(header) + len(content)) // chars_per_token

            remaining = max_tokens - tokens_used
            if remaining <= 0:
                break

            if entry_tokens > remaining:
                max_chars = remaining * chars_per_token - len(header)
                if max_chars > 200:
                    content = content[:max_chars] + "\n... (因 token 預算限制已截斷)"
                else:
                    break

            formatted_parts.append(f"{header}{content}")
            tokens_used += (len(header) + len(content)) // chars_per_token

        logger.info(f"Retrieved {len(formatted_parts)} docs, ~{tokens_used} tokens")

        return {"retrieved_docs": formatted_parts}

    return retrieve_node




# ---------------------------------------------------------------------------
# Node 3: Generate
# ---------------------------------------------------------------------------

def create_generate_node(llm: ChatOpenAI, config: RAGConfig) -> Callable:
    """Creates a node that generates the final answer using retrieved context."""
    summarizer = ConversationSummarizer(llm, summary_threshold=config.summary_threshold)

    def generate_node(state: GraphState) -> dict:
        logger.info("--- GENERATE NODE ---")
        question = state.get("question", "")
        retrieved_docs = state.get("retrieved_docs", [])

        # Build context from retrieved docs (NOT from ToolMessages)
        context_section = ""
        if retrieved_docs:
            if isinstance(retrieved_docs[0], str):
                # Already formatted strings from retrieve_node
                context_section = "\n\n".join(retrieved_docs)
            else:
                # Raw Document objects (fallback)
                parts = []
                for i, doc in enumerate(retrieved_docs, 1):
                    src = doc.metadata.get("source", "Unknown") if hasattr(doc, 'metadata') else "Unknown"
                    content = doc.page_content if hasattr(doc, 'page_content') else str(doc)
                    parts.append(f"=== 文件 {i} ===\n來源: {src}\n內容: {content}")
                context_section = "\n\n".join(parts)

        # Get conversation history — filter out non-standard message types
        messages_input = []
        for msg in state.get("messages", []):
            if isinstance(msg, (HumanMessage, AIMessage, SystemMessage)):
                messages_input.append(msg)

        # Compress history if too long
        try:
            messages_input = summarizer.process_messages(messages_input)
        except Exception as e:
            logger.warning(f"Summarization failed: {e}")
            if len(messages_input) > 10:
                messages_input = messages_input[-10:]

        # Build the prompt: System + History + Context + Question
        prompt_messages = [SystemMessage(content=AGENT_SYSTEM_PROMPT)]

        # Add compressed history (excluding the last user message to avoid duplication)
        for msg in messages_input[:-1] if messages_input else []:
            prompt_messages.append(msg)
            
        # Add feedback if this is a retry
        feedback = state.get("feedback", "")
        if feedback:
            logger.info("Applying verify feedback to generate prompt")
            prompt_messages.append(SystemMessage(content=f"【系統強制糾正】\n你先前的回答未通過審查，原因如下：\n{feedback}\n請務必在這次的回答中修正此問題，嚴格遵守格式與引用要求。"))

        # Build the final user message with context
        if context_section:
            final_user_msg = (
                f"以下是從知識庫檢索到的相關法律文件：\n\n"
                f"{context_section}\n\n"
                f"---\n\n"
                f"根據上述文件，請回答以下問題：\n{question}"
            )
        else:
            final_user_msg = (
                f"(系統提示：知識庫中未檢索到與此問題相關的法規條文。"
                f"請依照範圍判斷規則回覆使用者。)\n\n"
                f"使用者的問題：\n{question}"
            )

        prompt_messages.append(HumanMessage(content=final_user_msg))

        try:
            response = llm.invoke(prompt_messages)
            generation = response.content
            logger.info(f"Generated answer: {len(generation)} chars")

            return {
                "generation": generation,
                "messages": [AIMessage(content=generation)],
            }
        except Exception as e:
            error_msg = f"抱歉，處理問題時發生錯誤: {str(e)}"
            logger.error(f"Generation failed: {e}")
            return {
                "generation": error_msg,
                "messages": [AIMessage(content=error_msg)],
            }

    return generate_node


# ---------------------------------------------------------------------------
# Node 4: Verify
# ---------------------------------------------------------------------------

def create_verify_node() -> Callable:
    """Creates a node that checks if the answer properly cites legal articles."""

    def verify_node(state: GraphState) -> dict:
        generation = state.get("generation", "")
        retry_count = state.get("retry_count", 0)
        logger.info(f"--- VERIFY NODE --- retry={retry_count}")

        # Check 1: Does the answer contain legal article citations?
        has_citations = bool(_CITATION_PATTERN.search(generation))

        # Check 2: Does the answer contain the required sections?
        has_structure = "具體條文" in generation or "參考資料" in generation

        # Check 3: Is the answer substantive?
        is_substantive = len(generation) > 100 and REJECTION_MSG not in generation

        # Check 4: Did the model explicitly state no information was found?
        no_info = any(k in generation for k in ["尚未收錄", "無法提供", "未發現", "未檢索到", "沒有相關"])

        if (has_citations and has_structure) or no_info:
            logger.info(f"VERIFY: PASSED — criteria met or no info found (no_info={no_info})")
            return {"scope": "verified", "feedback": ""}

        if not is_substantive:
            logger.info("VERIFY: PASSED (non-substantive, skip retry)")
            return {"scope": "verified", "feedback": ""}

        if retry_count >= MAX_RETRIES:
            logger.warning(f"VERIFY: Max retries ({MAX_RETRIES}) reached, accepting answer")
            return {"scope": "verified", "feedback": ""}

        # Needs improvement — trigger another retrieval attempt
        feedback_msgs = []
        if not has_citations:
            feedback_msgs.append("- 缺乏明確的法條引用 (如第X條)")
        if not has_structure:
            feedback_msgs.append("- 未按照規定的格式輸出 (缺少「具體條文」或「參考資料」節點)")
            
        feedback_str = "\n".join(feedback_msgs)
        logger.info(f"VERIFY: NEEDS IMPROVEMENT — {feedback_str}")
        
        return {
            "scope": "needs_retry",
            "retry_count": retry_count + 1,
            "feedback": feedback_str,
        }

    return verify_node
