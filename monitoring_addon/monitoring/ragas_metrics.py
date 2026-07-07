"""
RAGAS-style metrics — Faithfulness / Answer Relevancy / Context Precision

Implements lightweight, LLM-based RAG evaluation inspired by the RAGAS
framework (https://github.com/explodinggradients/ragas) but **without the
heavy `ragas` dependency** and tailored for Chinese legal QA.

Three metrics:

  1. Faithfulness (忠實度)
     Does the answer's claims come from the retrieved context, or did
     the LLM hallucinate? 0 = all hallucinated; 1 = fully grounded.

  2. Answer Relevancy (回答相關性)
     Does the answer actually address the user's question, or does it
     drift? Computed by asking the LLM to generate hypothetical questions
     from the answer, then comparing them to the original query.

  3. Context Precision (上下文精準度)
     Of the retrieved chunks, how many were actually useful for the
     answer? Detects retrieve-rich-but-answer-narrow waste.

These are A.9 V&V evidence — auditor-readable proof that the system was
evaluated beyond mere hit-rate, including answer-grounding quality.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict, field
from typing import List, Optional

try:
    import requests
except ImportError:
    requests = None


# ============================================================================
# Prompts (Chinese, tuned for our legal domain)
# ============================================================================

FAITHFULNESS_PROMPT = """以下是 RAG 系統的查詢、檢索到的文件、與系統的回答。請判斷回答的主張是否**完全來自檢索到的文件**。

查詢：{question}

檢索到的文件：
{context}

系統的回答：
{answer}

請只回傳一個 JSON 物件（不要程式碼框、不要解釋）：

```
{{
  "is_abstention": <true 若答案是「無相關資料/查無資料/無法回答」等拒答，否則 false>,
  "grounded_claims": <int, 答案中有幾個主張是文件支持的>,
  "ungrounded_claims": <int, 答案中有幾個主張在文件中找不到依據>,
  "score": <float 0.0–1.0 = grounded / (grounded + ungrounded)；若 is_abstention=true 則填 null>
}}
```

判斷準則：
- 一個「主張」= 答案中的一個獨立事實陳述（如「第3條規定救濟程序」「期限為30日」）
- 法規名稱、條文編號、具體文字內容必須在文件中找到才算 grounded
- 模型自行推論的延伸（如「依此規定，您可以...」）若不在文件中明示，算 ungrounded
- 若答案是拒答（如「無相關資料」），設 is_abstention=true、grounded_claims=0、ungrounded_claims=0、score=null（此題不計入忠實度平均，另計拒答率，**不得當作滿分**）

只回傳 JSON。"""


ANSWER_RELEVANCY_PROMPT = """以下是 RAG 系統的查詢與回答。請評估**回答是否真的回應了查詢**。

查詢：{question}

系統的回答：
{answer}

請只回傳一個 JSON 物件：

```
{{
  "addresses_query": true | false,
  "drifts_off_topic": true | false,
  "score": <float 0.0–1.0>
}}
```

判斷準則：
- `addresses_query` : 回答是否直接回應了使用者問題的核心
- `drifts_off_topic` : 是否大幅偏題、講了無關的法條
- `score` :
  - 1.0 = 完全回應且聚焦
  - 0.7 = 回應但有些枝節
  - 0.4 = 部分回應、有不少偏題
  - 0.0 = 完全沒回應

只回傳 JSON。"""


CONTEXT_PRECISION_PROMPT = """以下是查詢、檢索到的所有文件編號、與系統的回答。請評估每份檢索文件**對最終答案的貢獻度**。

查詢：{question}

檢索到的文件（編號 1..N）：
{context_list}

系統的回答：
{answer}

請只回傳一個 JSON 物件：

```
{{
  "useful_count": <int, 對答案有實質貢獻的文件數>,
  "total_count": <int, 檢索文件總數>,
  "score": <float 0.0–1.0, useful_count / total_count>
}}
```

只回傳 JSON。"""


# ============================================================================
# Lightweight HTTP LLM client
# ============================================================================

@dataclass
class RagasScore:
    faithfulness: Optional[float] = None
    answer_relevancy: Optional[float] = None
    context_precision: Optional[float] = None
    sample_size: int = 0
    skipped: int = 0
    # Faithfulness mean is taken ONLY over substantive answers the judge scored
    # ("ok"). Refusals and judge outages are EXCLUDED (a refuse-everything run
    # can't read 1.0; an outage can't read 0.0) and surfaced here by category:
    #   faithfulness_breakdown = {"ok", "no_coverage", "rejection_oos",
    #                             "abstention", "unavailable"} -> count
    # no_coverage_rate = in-scope refusals / sample → a POSSIBLE retrieval miss
    # (cross-check Hit Rate/Recall), NOT a hallucination signal.
    faithfulness_evaluated: int = 0
    faithfulness_breakdown: dict = field(default_factory=dict)
    no_coverage_rate: Optional[float] = None

    def overall(self) -> Optional[float]:
        """Equal-weighted mean of the metrics that were actually measured.

        None when nothing was measured, so the dashboard shows "尚未評估"
        rather than a fake 0.0.
        """
        vals = [v for v in (self.faithfulness, self.answer_relevancy,
                            self.context_precision) if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals), 4) if vals else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["overall"] = self.overall()
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()}


def _call_llm(prompt: str, system_msg: str = "你是 RAG 系統評估專家。") -> str:
    """Lightweight OpenAI-compatible chat completion call.

    Reads LLM_API_BASE / LLM_API_KEY / CHAT_MODEL_NAME from env so it
    works inside the monitoring container (uses same gateway as RAG).
    """
    if requests is None:
        return ""
    # Chat judge must use the LLM gateway ONLY. Never fall back to EMBED_API_BASE
    # — the embedding proxy has no /chat/completions and would fail (the original
    # intranet "no faithfulness" footgun). Unset → "" → caller marks unavailable.
    base = os.environ.get("LLM_API_BASE")
    if not base:
        return ""
    key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("CHAT_MODEL_NAME", "gpt-oss-20b")
    try:
        resp = requests.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0.0,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=60,
            verify=os.environ.get("VERIFY_SSL", "false").lower() == "true",
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return ""


def _parse_json(raw: str) -> Optional[dict]:
    """Extract first JSON object from LLM response."""
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ============================================================================
# Per-query scoring
# ============================================================================


def _valid_unit_score(data: Optional[dict]) -> Optional[float]:
    """Extract a clamped 0–1 score from a judge reply, or None if unusable.

    Returns None (NOT 0.0) when the judge gave no usable score, so an evaluator
    outage or a malformed reply is never silently counted as a perfect-
    hallucination 0.0. (P1-1)
    """
    if not isinstance(data, dict):
        return None
    sc = data.get("score")
    if isinstance(sc, bool) or not isinstance(sc, (int, float)):
        return None
    sc = float(sc)
    if sc != sc:  # NaN
        return None
    return round(max(0.0, min(1.0, sc)), 4)


def _natural_int(x) -> Optional[int]:
    return x if isinstance(x, int) and not isinstance(x, bool) and x >= 0 else None


def score_faithfulness(question: str, context: str, answer: str) -> dict:
    """Judge answer-groundedness. Returns a STATUS dict (never a bare float):

        {"status": "ok"|"abstention"|"unavailable",
         "score": Optional[float], "grounded": Optional[int],
         "ungrounded": Optional[int]}

    - "unavailable": judge unreachable / unparseable / no usable counts or
                     score → score=None (NOT 0.0). (P1-1)
    - "abstention" : answer was a refusal ("無相關資料"); score=None so the
                     query is excluded from the faithfulness mean and counted in
                     abstention_rate instead of a misleading 1.0. (P1-2)
    - "ok"         : score RE-COMPUTED as grounded/(grounded+ungrounded) in
                     Python, not trusting the judge's own arithmetic. (P1-3)
    """
    data = _parse_json(_call_llm(FAITHFULNESS_PROMPT.format(
        question=question, context=context, answer=answer,
    )))
    if not isinstance(data, dict):
        return {"status": "unavailable", "score": None, "grounded": None, "ungrounded": None}

    g = _natural_int(data.get("grounded_claims"))
    u = _natural_int(data.get("ungrounded_claims"))

    # Abstention: explicit flag, or nothing to evaluate (0 grounded + 0 ungrounded).
    if bool(data.get("is_abstention", False)) or (g == 0 and u == 0):
        return {"status": "abstention", "score": None, "grounded": g, "ungrounded": u}

    # Re-compute from counts (P1-3) — don't trust the LLM's division.
    if g is not None and u is not None and (g + u) > 0:
        return {"status": "ok", "score": round(g / (g + u), 4),
                "grounded": g, "ungrounded": u}

    # Counts missing/unusable → the judge reply is malformed. Do NOT fall back to
    # the judge's self-reported score — that is exactly the untrusted arithmetic
    # P1-3 removes. Mark unavailable.
    return {"status": "unavailable", "score": None, "grounded": g, "ungrounded": u}


def score_answer_relevancy(question: str, answer: str) -> Optional[float]:
    return _valid_unit_score(_parse_json(_call_llm(ANSWER_RELEVANCY_PROMPT.format(
        question=question, answer=answer,
    ))))


def score_context_precision(
    question: str, retrieved_docs: List[str], answer: str
) -> Optional[float]:
    if not retrieved_docs:
        return None
    context_list = "\n".join(
        f"[{i}] {src}" for i, src in enumerate(retrieved_docs, 1)
    )
    return _valid_unit_score(_parse_json(_call_llm(CONTEXT_PRECISION_PROMPT.format(
        question=question, context_list=context_list, answer=answer,
    ))))


# Canonical refusal phrasings emitted by the RAG agent (AGENT_SYSTEM_PROMPT).
# Mirror RAG/rag_system/core/answer_evaluator.py (_REJECTION_PREFIX) and
# RAG/api.py's rejection detection — keep in sync if those messages change.
# Distinctive, near-complete phrases (not short fragments) so a substantive
# answer that merely mentions coverage isn't misread as a refusal.
_OOS_REJECTION_MARKERS = ("本系統僅提供法律文件", "無法回答與法律無關的問題")
_NO_COVERAGE_MARKERS = ("尚未收錄與此問題相關的法規內容", "無法提供具體條文")


def classify_rag_answer(answer: str) -> str:
    """Classify a RAG answer by the system's 3 response types so the judge only
    scores REAL answers (AGENT_SYSTEM_PROMPT 範圍判斷規則).

      "answer"        : substantive answer → run the LLM faithfulness judge.
      "rejection_oos" : out-of-scope guardrail refusal — CORRECT decline of a
                        non-legal question. Excluded from faithfulness; rejection
                        *correctness* is a separate, golden-labelled axis (see
                        answer_evaluator.py), and OOS golden entries are filtered
                        out of this eval entirely.
      "no_coverage"   : in-scope but the KB had no relevant article. Excluded
                        from faithfulness; a POSSIBLE retrieval miss → cross-check
                        Hit Rate/Recall (online_vv), not a hallucination metric.
      "empty"         : no answer text.
    """
    a = (answer or "").strip()
    if not a:
        return "empty"
    if any(m in a for m in _OOS_REJECTION_MARKERS):
        return "rejection_oos"
    if any(m in a for m in _NO_COVERAGE_MARKERS):
        return "no_coverage"
    return "answer"


def score_single(question: str, context: str, retrieved_docs: List[str], answer: str) -> dict:
    """Score one (query, context, retrieved, answer) tuple on all 3 metrics.

    Refusals/empties are classified deterministically (classify_rag_answer) and
    NOT sent to the judge — faithfulness only scores substantive answers.
    `faithfulness` is always a status dict; relevancy/precision are
    Optional[float] (None when not scored).
    """
    cls = classify_rag_answer(answer)
    if cls != "answer":
        status = "unavailable" if cls == "empty" else cls
        return {
            "faithfulness": {"status": status, "score": None, "grounded": None, "ungrounded": None},
            "answer_relevancy": None,
            "context_precision": None,
        }
    # Substantive answer but NO grounding context (retrieval returned nothing, or
    # retrieved_docs weren't logged for this session): faithfulness is
    # unmeasurable — judging an answer against empty context yields a FALSE 0.0.
    # Mark "no_context" and still score relevancy (which needs no context).
    if not (context or "").strip():
        return {
            "faithfulness": {"status": "no_context", "score": None, "grounded": None, "ungrounded": None},
            "answer_relevancy": score_answer_relevancy(question, answer),
            "context_precision": None,
        }
    return {
        "faithfulness": score_faithfulness(question, context, answer),
        "answer_relevancy": score_answer_relevancy(question, answer),
        "context_precision": score_context_precision(question, retrieved_docs, answer),
    }


def aggregate_ragas_scores(scores: List[dict]) -> RagasScore:
    """Aggregate a batch.

    Faithfulness mean is taken ONLY over queries the judge actually scored
    ("ok"); abstentions and evaluator failures are excluded and reported
    separately so a refuse-everything run can't show 1.0 and an evaluator
    outage can't show 0.0. None faithfulness when nothing was scored →
    dashboard shows "尚未評估". (P1-1/P1-2)
    """
    n = len(scores)
    faith_ok: List[float] = []
    breakdown: dict = {}
    for s in scores:
        f = s.get("faithfulness")
        if isinstance(f, dict):
            st = f.get("status") or "unavailable"
            sc = f.get("score")
        elif isinstance(f, (int, float)) and not isinstance(f, bool):
            st, sc = "ok", float(f)        # tolerate a legacy bare float
        else:
            st, sc = "unavailable", None
        breakdown[st] = breakdown.get(st, 0) + 1
        if st == "ok" and isinstance(sc, (int, float)):
            faith_ok.append(float(sc))

    def _avg(key: str) -> Optional[float]:
        vals = [s[key] for s in scores
                if isinstance(s.get(key), (int, float)) and not isinstance(s.get(key), bool)]
        return round(sum(vals) / len(vals), 4) if vals else None

    def _has_any_metric(s: dict) -> bool:
        f = s.get("faithfulness")
        f_ok = isinstance(f, dict) and f.get("status") == "ok"
        return (f_ok
                or isinstance(s.get("answer_relevancy"), (int, float))
                or isinstance(s.get("context_precision"), (int, float)))

    # In-scope refusals: system gave no answer for an answerable query.
    # (OOS rejections are filtered out of this eval, so they shouldn't appear;
    #  if rejection_oos > 0 here, a golden entry was mis-categorised.)
    no_coverage = breakdown.get("no_coverage", 0) + breakdown.get("abstention", 0)

    return RagasScore(
        faithfulness=round(sum(faith_ok) / len(faith_ok), 4) if faith_ok else None,
        answer_relevancy=_avg("answer_relevancy"),
        context_precision=_avg("context_precision"),
        sample_size=n,
        skipped=sum(1 for s in scores if not _has_any_metric(s)),
        faithfulness_evaluated=len(faith_ok),
        faithfulness_breakdown=breakdown,
        no_coverage_rate=round(no_coverage / n, 4) if n else None,
    )
