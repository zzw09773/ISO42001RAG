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
from typing import List, Callable, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ..core.prompts import (
    AGENT_SYSTEM_PROMPT,
    VERIFY_SYSTEM_MSG,
    VERIFY_PROMPT_TEMPLATE,
    CLASSIFY_SYSTEM_MSG,
    CLASSIFY_PROMPT_TEMPLATE,
    REJECTION_MSG,
    CAPABILITY_MSG,
    PRC_BLOCK_MSG,
    SECURITY_MSG,
)
from ..core.config import RAGConfig
from ..core.input_sanitizer import sanitize
from ..core.output_filter import filter_output
from ..core.audit_logger import AuditLogger
from .state import GraphState
from .memory import ConversationSummarizer
from ..services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

# REJECTION_MSG / SECURITY_MSG now imported from core.prompts (ISO 42001 A.9)
# — centralised so both classic & ReAct workflows show the same guided message.

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
_ZH_NUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3,
           "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ZH_UNIT = {"十": 10, "百": 100, "千": 1000}
_ARTICLE_TOKEN = r"[0-9零〇一二兩三四五六七八九十百千]+"
_CITATION_PATTERN = re.compile(rf'第\s*{_ARTICLE_TOKEN}\s*條|article\s*\d+', re.IGNORECASE)
_ARTICLE_NUM_RE = re.compile(rf'第\s*({_ARTICLE_TOKEN})\s*條')


def _parse_article_number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    total = 0
    current = 0
    for char in token:
        if char in _ZH_NUM:
            current = _ZH_NUM[char]
        elif char in _ZH_UNIT:
            total += (current or 1) * _ZH_UNIT[char]
            current = 0
        else:
            return None
    return total + current


def _article_nums(text: str) -> set:
    """Arabic or Chinese 第N條 numbers found in answer text or source ids."""
    values = (_parse_article_number(token) for token in _ARTICLE_NUM_RE.findall(text or ""))
    return {value for value in values if value is not None}


def _retrieved_article_nums(sources) -> set:
    """Article numbers present across retrieved_sources (法名.md#第N條)."""
    nums: set = set()
    for s in sources or []:
        nums |= _article_nums(str(s))
    return nums


def _retrieved_evidence_article_nums(sources, evidence=None) -> set:
    """Article numbers grounded by source identifiers or retrieved text."""
    nums = _retrieved_article_nums(sources)
    for item in evidence or []:
        nums |= _article_nums(str(item))
    return nums


# Deterministic PRC / 中共 hard-block pattern (中科院要求). SPECIFIC PRC markers
# only — must NOT match ROC terms (e.g. 陸海空軍, 中華民國) or generic words.
_PRC_BLOCK_RE = re.compile(
    "中華人民共和國|中华人民共和国|中共|中國共產黨|中国共产党|共產黨|共产党|"
    "解放軍|解放军|習近平|习近平|中國大陸|中国大陆|大陸地區|大陆地区|中南海|"
    "大陸法規|大陆法规|大陸軍事|大陆军事|大陸制度|大陆制度|大陸政府|大陆政府"
)


# ---------------------------------------------------------------------------
# Node 1: Classify
# ---------------------------------------------------------------------------

def _parse_classify_verdict(raw: str) -> str:
    """Best-effort JSON extraction of `scope` field. Defaults to 'legal'."""
    import json as _json
    import re as _re
    text = (raw or "").strip()
    text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.MULTILINE).strip()
    m = _re.search(r"\{[^{}]*\}", text, _re.DOTALL)
    if not m:
        return "legal"
    try:
        verdict = _json.loads(m.group(0))
        scope = str(verdict.get("scope", "")).lower().strip()
        if scope in ("legal", "capability", "reject", "passthrough"):
            return scope
        return "legal"
    except Exception:
        return "legal"


def create_classify_node(llm: Optional["ChatOpenAI"] = None) -> Callable:
    """Creates a classifier node. Two modes:

      - llm=None (default fallback):
            Regex keyword matching against legal vocabulary. Cheap but
            misroutes some borderline intent-driven queries.

      - llm=ChatOpenAI:
            LLM reads query intent and routes to legal/reject/passthrough.
            Adds ~300-500ms per query but handles edge cases better
            (e.g., "同事每天罵我可以告嗎" → legal even without "告" keyword).

    Security checks (sanitize + Task: prefix detection) always run first
    regardless of mode, so an LLM hiccup can't bypass security.
    """

    def classify_node(state: GraphState) -> dict:
        question = state.get("question", "")
        logger.info(f"--- CLASSIFY NODE --- question: {question[:80]}")

        # ── Security always first — never delegated to LLM ────────────────
        # wrapper_mode 由 graph state 傳入（API 層以不可偽造條件判定 OpenWebUI 背景
        # 任務後設定），使第二道 sanitizer 與前置 sanitizer 的 wrapper 豁免一致。
        san = sanitize(question, is_wrapper=bool(state.get("wrapper_mode", False)))
        if san.blocked:
            logger.warning(f"Input blocked by sanitizer: {san.threat_type} — {san.reason}")
            return {
                "scope": "security_block",
                "security_reason": san.reason,
                "threat_type": san.threat_type,
                "actions": [f"classify=security_block({san.threat_type})"],
            }

        # ── PRC / 中共 hard block — deterministic, BEFORE passthrough/LLM ──
        # Runs before the "### Task:" passthrough and the LLM so a framed or
        # LLM-misjudged PRC query can't slip through. Never delegated to the LLM.
        if _PRC_BLOCK_RE.search(question):
            logger.warning("Classification: prc_block (PRC/中共 content detected)")
            return {"scope": "prc_block", "actions": ["classify=prc_block"]}

        # Open WebUI system tasks: deterministic prefix check
        if question.strip().startswith("### Task:"):
            logger.info("Classification: passthrough (Open WebUI system task)")
            return {"scope": "passthrough", "actions": ["classify=passthrough(openwebui_task)"]}

        # ── LLM mode ─────────────────────────────────────────────────────
        if llm is not None:
            try:
                response = llm.invoke([
                    SystemMessage(content=CLASSIFY_SYSTEM_MSG),
                    HumanMessage(content=CLASSIFY_PROMPT_TEMPLATE.format(question=question)),
                ])
                scope = _parse_classify_verdict(response.content)
                logger.info(f"Classification (LLM): {scope}")
                return {"scope": scope, "actions": [f"classify=llm:{scope}"]}
            except Exception as e:
                logger.warning(f"LLM classify failed, falling back to regex: {e}")
                # Fall through to regex below

        # ── Regex fallback (or default mode) ──────────────────────────────
        if _LEGAL_KEYWORDS.search(question):
            logger.info("Classification: legal (regex keyword)")
            return {"scope": "legal", "actions": ["classify=regex:legal"]}

        if _CHAT_KEYWORDS.search(question):
            logger.info("Classification: reject (regex chat keyword)")
            return {"scope": "reject", "actions": ["classify=regex:reject"]}

        # Ambiguous — default to legal
        logger.info("Classification: legal (regex ambiguous default)")
        return {"scope": "legal", "actions": ["classify=regex:legal(ambiguous)"]}

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
            "actions": ["reject"],
        }

    return reject_node


def create_capability_node() -> Callable:
    """Node that answers system-capability / how-to-use questions directly,
    WITHOUT retrieval (scope == "capability"). Keeps legitimate "what can you
    do / how do I use you" questions from being rejected as off-topic."""

    def capability_node(state: GraphState) -> dict:
        logger.info("--- CAPABILITY NODE ---")
        return {
            "generation": CAPABILITY_MSG,
            "messages": [AIMessage(content=CAPABILITY_MSG)],
            "actions": ["capability"],
        }

    return capability_node


def create_prc_block_node() -> Callable:
    """Node that HARD-blocks PRC / 中共 content (scope == "prc_block"), returning
    a fixed refusal with no retrieval and no LLM. (中科院 deterministic control)"""

    def prc_block_node(state: GraphState) -> dict:
        logger.info("--- PRC BLOCK NODE ---")
        return {
            "generation": PRC_BLOCK_MSG,
            "messages": [AIMessage(content=PRC_BLOCK_MSG)],
            "actions": ["prc_block"],
        }

    return prc_block_node


def create_security_block_node(audit: AuditLogger = None) -> Callable:
    """Creates a node that returns a security rejection message and writes audit log."""

    def security_block_node(state: GraphState) -> dict:
        threat_type = state.get("threat_type", "unknown")
        reason = state.get("security_reason", "")
        question = state.get("question", "")
        session_id = state.get("session_id", "unknown")
        client_ip = state.get("client_ip", "")
        audit_context = state.get("audit_context", {}) or {}
        logger.warning(
            f"--- SECURITY BLOCK NODE --- threat={threat_type} reason={reason} ip={client_ip}"
        )
        if audit:
            audit.log_security_alert(
                session_id=session_id,
                user_query=question,
                threat_type=threat_type,
                reason=reason,
                stage="input",
                action_taken="blocked",
                user_notified=True,
                detection_method="input_sanitizer",
                client_ip=client_ip,
                **audit_context,
            )
        return {
            "generation": SECURITY_MSG,
            "messages": [AIMessage(content=SECURITY_MSG)],
            "actions": [f"security_block({threat_type})"],
        }

    return security_block_node


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
                "actions": ["passthrough"],
            }
        except Exception as e:
            logger.error(f"Passthrough failed: {e}")
            return {
                "generation": "",
                "messages": [AIMessage(content="")],
                "actions": ["passthrough(error)"],
            }

    return passthrough_node


# ---------------------------------------------------------------------------
# Query Expansion Helper
# ---------------------------------------------------------------------------

# Pattern to detect short article references (e.g., "8條", "第 8 條", "第八條")
_ARTICLE_REF_PATTERN = re.compile(
    r'^(?:第\s*)?([0-9一二三四五六七八九十百千]+)\s*條'
)

_CN_NUMS = {
    '一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
    '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
    '十一': '11', '十二': '12', '十三': '13', '十四': '14', '十五': '15',
    '十六': '16', '十七': '17', '十八': '18', '十九': '19', '二十': '20',
}


def _expand_query(question: str) -> str:
    """
    Expand only short article references for better vector search recall.

    Short queries like "第8條" lack semantic context for embeddings.
    Domain routing is intentionally not performed here: RetrievalService sends
    every legal query, unchanged, through global and per-source searches.
    """
    q = question.strip()
    m = _ARTICLE_REF_PATTERN.match(q)
    if m and len(q) <= 15:
        num = m.group(1)
        if num in _CN_NUMS:
            num = _CN_NUMS[num]
        return f"第 {num} 條 法律條文規定內容 第{num}條"

    return question


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

        # Only short article references are expanded. Domain coverage happens
        # inside RetrievalService with the original question unchanged.
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
            return {
                "retrieved_docs": [],
                "retrieved_sources": [],
                "actions": ["retrieve=empty(domain_search=source_scoped)"],
            }

        # Format with token budget + collect source identifiers for audit log
        formatted_parts: list = []
        retrieved_sources: list = []
        tokens_used = 0

        for i, doc in enumerate(docs, 1):
            source = doc.metadata.get("source", "Unknown")
            article_id = doc.metadata.get("article_id", "")
            content = doc.page_content

            # Build a precise source id (file + article) for ISO 42001 A.7 trace
            if article_id and article_id not in ("preamble", "whole_document"):
                src_id = f"{source}#{article_id}"
            else:
                src_id = source
            if src_id not in retrieved_sources:
                retrieved_sources.append(src_id)

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

        logger.info(
            "Retrieved %d docs (%d unique sources), ~%d tokens",
            len(formatted_parts), len(retrieved_sources), tokens_used,
        )

        action = (
            f"retrieve(docs={len(formatted_parts)},sources={len(retrieved_sources)},"
            "domain_search=source_scoped)"
        )
        if search_query != question:
            action = action[:-1] + ",article_query_expanded=true)"

        return {
            "retrieved_docs": formatted_parts,
            "retrieved_sources": retrieved_sources,
            "actions": [action],
        }

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

            # Output filter: redact any accidental sensitive data leakage
            filtered = filter_output(generation)
            if filtered.redacted:
                logger.warning(f"Output filter redacted findings: {filtered.findings}")

            # ── ISO 42001 monitoring fields ────────────────────────────────
            # A.6 — count article references in the answer (proxy for citation quality)
            citation_count = len(_CITATION_PATTERN.findall(filtered.text))
            # A.4 — real token usage from LLM response (replaces len/4 estimate)
            tokens_used = 0
            usage_meta = getattr(response, "usage_metadata", None) or {}
            response_meta = getattr(response, "response_metadata", None) or {}
            if isinstance(usage_meta, dict):
                tokens_used = int(usage_meta.get("total_tokens") or 0)
            if not tokens_used and isinstance(response_meta, dict):
                token_usage = response_meta.get("token_usage", {}) or {}
                tokens_used = int(token_usage.get("total_tokens") or 0)

            logger.info(
                "Generation metrics: citations=%d tokens=%d",
                citation_count, tokens_used,
            )

            redact_tag = "+redacted" if filtered.redacted else ""
            return {
                "generation": filtered.text,
                "messages": [AIMessage(content=filtered.text)],
                "output_redacted": filtered.redacted,
                "citation_count": citation_count,
                "tokens_used": tokens_used,
                "actions": [f"generate(citations={citation_count},tokens={tokens_used}{redact_tag})"],
            }
        except Exception as e:
            error_msg = f"抱歉，處理問題時發生錯誤: {str(e)}"
            logger.error(f"Generation failed: {e}")
            return {
                "generation": error_msg,
                "messages": [AIMessage(content=error_msg)],
                "citation_count": 0,
                "tokens_used": 0,
                "actions": ["generate=error"],
            }

    return generate_node


# ---------------------------------------------------------------------------
# Node 4: Verify — LLM-based self-reflection
# ---------------------------------------------------------------------------
#
# Replaces the previous regex-based verifier (which only checked "第N條"
# presence and section headers). The LLM evaluates whether the answer
# substantively addresses the user's question, not just whether it looks
# like a formatted legal response.
#
# Fast-path safety nets remain to avoid wasting an LLM call:
#   - Empty / very short answers → auto-PASS (caller already handled)
#   - "尚未收錄" / "無法提供" honest-rejection → PASS (no retry needed)
#   - retry_count ≥ MAX_RETRIES → PASS only after citation provenance passes
# ---------------------------------------------------------------------------

_NO_INFO_PHRASES = ["尚未收錄", "無法提供", "未發現", "未檢索到", "沒有相關"]
_CLAUSE_BOUNDARY_RE = re.compile(r"(?:[。！？；;，,\n]+|但是|但|然而)")


def _reported_missing_article_nums(text: str) -> set[int]:
    """Article numbers explicitly tied to a no-information clause."""
    missing: set[int] = set()
    asserted: set[int] = set()
    for clause in _CLAUSE_BOUNDARY_RE.split(text or ""):
        if any(phrase in clause for phrase in _NO_INFO_PHRASES):
            missing |= _article_nums(clause)
        else:
            asserted |= _article_nums(clause)
    return missing - asserted


def _parse_verify_verdict(raw: str) -> dict:
    """Best-effort JSON extraction from LLM response. Falls back to pass-through."""
    import json as _json
    import re as _re
    text = (raw or "").strip()
    # Strip common code fences
    text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=_re.MULTILINE).strip()
    # Find first JSON-looking object
    m = _re.search(r"\{[^{}]*\}", text, _re.DOTALL)
    if not m:
        return {"needs_retry": False, "reason": "no_json"}
    try:
        return _json.loads(m.group(0))
    except Exception:
        return {"needs_retry": False, "reason": "parse_error"}


def create_verify_node(llm: Optional["ChatOpenAI"] = None) -> Callable:
    """Creates an LLM-based answer-quality verifier.

    Returns a node closure. If `llm` is None, the closure will lazily
    skip LLM calls and fall back to regex (legacy behaviour) — safe default
    that lets existing tests / partial setups still work.
    """

    def verify_node(state: GraphState) -> dict:
        generation = state.get("generation", "")
        question = state.get("question", "")
        retry_count = state.get("retry_count", 0)
        logger.info(f"--- VERIFY NODE (LLM) --- retry={retry_count}")

        # ── Evidence-grounded gate: citation provenance (deterministic) ─────
        # Catch FABRICATED citations — an article number cited in the answer that
        # was NOT in the retrieved context = ungrounded (hallucinated) citation.
        # This does NOT second-guess WHICH articles to cite (retrieval's job); it
        # only requires the answer's citations to trace to retrieved evidence.
        # Deeper claim-vs-content grounding is covered offline by
        # monitoring_addon RAGAS faithfulness.
        retrieved_sources = state.get("retrieved_sources", []) or []
        answer_articles = _article_nums(generation)
        retrieved_articles = _retrieved_evidence_article_nums(
            retrieved_sources,
            state.get("retrieved_docs", []) or [],
        )
        reports_no_info = any(phrase in generation for phrase in _NO_INFO_PHRASES)
        honestly_missing = (
            _reported_missing_article_nums(generation)
            & _article_nums(question)
            & (answer_articles - retrieved_articles)
        )
        ungrounded = sorted(answer_articles - retrieved_articles - honestly_missing)
        if ungrounded:
            arts = "、第".join(str(n) for n in ungrounded)
            logger.info("VERIFY: ungrounded citation 第%s條 (retrieved=%s)",
                        arts, sorted(_retrieved_article_nums(retrieved_sources)))
            if retry_count >= MAX_RETRIES:
                logger.warning(
                    "VERIFY: retry budget exhausted with ungrounded citation; "
                    "replacing answer with a fail-safe response"
                )
                return {
                    "scope": "verified",
                    "generation": (
                        "目前無法從已檢索資料確認回答中的條文引用，為避免提供無據內容，"
                        "本次不提供該回答；請重新查詢或由人工查證原文。"
                    ),
                    "feedback": "",
                    "actions": ["verify=failed_safe(ungrounded_citation)"],
                }
            return {
                "scope": "needs_retry",
                "retry_count": retry_count + 1,
                "feedback": (f"答案引用第{arts}條，但檢索結果未含這些條文（疑似無據引用），"
                             "請僅依檢索到的條文作答"),
                "actions": ["verify=needs_retry(ungrounded_citation)"],
            }

        # A response that cites grounded evidence while claiming the knowledge
        # base has no relevant information is internally contradictory. Retry
        # it without inferring the user's intent from keywords or hard-coding a
        # specific statute/article. If the evidence is only conditionally
        # applicable, the regenerated answer must say so explicitly.
        if (
            retry_count < MAX_RETRIES
            and reports_no_info
            and bool(answer_articles & retrieved_articles)
            and bool(retrieved_sources)
        ):
            logger.info(
                "VERIFY: retrying answer that cites retrieved evidence while "
                "claiming no information is available"
            )
            return {
                "scope": "needs_retry",
                "retry_count": retry_count + 1,
                "feedback": (
                    "回答一方面引用已檢索條文，一方面宣稱知識庫沒有相關資料，兩者矛盾。"
                    "請重新檢視已檢索內容：若條文可提供條件式說明，明確交代適用前提後"
                    "回答；若確實不適用，則說明原因且不要把該條文列為支持答案的參考資料。"
                    "不得延伸條文未記載的事實或替使用者認定身分。"
                ),
                "actions": ["verify=needs_retry(contradictory_no_info)"],
            }

        # ── Fast-path 1: empty / non-substantive answer → PASS ──────────────
        if len(generation) < 50 or REJECTION_MSG in generation:
            logger.info("VERIFY: PASSED (non-substantive, skip retry)")
            return {"scope": "verified", "feedback": "", "actions": ["verify=passed(non_substantive)"]}

        # ── Fast-path 2: honest "no info found" → PASS ──────────────────────
        if any(p in generation for p in _NO_INFO_PHRASES) and not _CITATION_PATTERN.search(generation):
            logger.info("VERIFY: PASSED (model honestly reports no info)")
            return {"scope": "verified", "feedback": "", "actions": ["verify=passed(no_info)"]}

        # ── Fast-path 3: retry budget exhausted after provenance → PASS ─────
        if retry_count >= MAX_RETRIES:
            logger.warning(f"VERIFY: Max retries ({MAX_RETRIES}) reached, accepting grounded answer")
            return {"scope": "verified", "feedback": "", "actions": ["verify=passed(retry_limit)"]}

        # ── LLM verdict (or fallback to regex if llm not provided) ──────────
        if llm is None:
            has_citations = bool(_CITATION_PATTERN.search(generation))
            has_structure = "具體條文" in generation or "參考資料" in generation
            if has_citations and has_structure:
                return {"scope": "verified", "feedback": "", "actions": ["verify=passed(regex)"]}
            return {
                "scope": "needs_retry",
                "retry_count": retry_count + 1,
                "feedback": "缺乏條文引用或結構不完整（regex fallback）",
                "actions": ["verify=needs_retry(regex)"],
            }

        try:
            response = llm.invoke([
                SystemMessage(content=VERIFY_SYSTEM_MSG),
                HumanMessage(content=VERIFY_PROMPT_TEMPLATE.format(
                    question=question,
                    answer=generation[:2000],   # truncate to keep verify cheap
                )),
            ])
            verdict = _parse_verify_verdict(response.content)
        except Exception as e:
            logger.warning(f"Verify LLM call failed: {e}, defaulting to PASS")
            return {"scope": "verified", "feedback": "", "actions": ["verify=passed(llm_error)"]}

        needs_retry = bool(verdict.get("needs_retry"))
        reason = str(verdict.get("reason", ""))[:100]

        if not needs_retry:
            logger.info(
                "VERIFY: PASSED — answers_question=%s cites_article=%s reason=%s",
                verdict.get("answers_question"),
                verdict.get("cites_article"),
                reason,
            )
            return {"scope": "verified", "feedback": "", "actions": ["verify=passed(llm)"]}

        # LLM judged retry needed
        feedback_str = f"審查者判定需重試：{reason}（answers_question={verdict.get('answers_question')}, cites_article={verdict.get('cites_article')}）"
        logger.info(f"VERIFY: NEEDS RETRY — {feedback_str}")
        return {
            "scope": "needs_retry",
            "retry_count": retry_count + 1,
            "feedback": feedback_str,
            "actions": ["verify=needs_retry(llm)"],
        }

    return verify_node
