"""Resolve retrieved-doc labels to real article text from converted_md.

Faithfulness needs the ACTUAL article text as context, not just the source
label ("陸海空軍懲罰法.md#第14條"). Judging an answer against labels alone can
only catch "cited an article that wasn't retrieved" — it cannot verify whether
the answer's CONTENT is supported by the article TEXT. This module parses the
law markdown into per-article text so the RAGAS faithfulness judge sees the
real content.

The monitoring container mounts /rag_data:ro (= RAG/data), so converted_md is
readable at /rag_data/converted_md without importing rag_system.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Article header line, e.g. "第 14 條" / "第14條" / "第 一二三 條"
_HEADER_RE = re.compile(r"^第\s*[0-9一二三四五六七八九十百零兩]+\s*條\s*$")


def _norm_article(s: str) -> str:
    """'第 14 條' / '第14條' → '第14條' (whitespace removed).

    Matches the compact form used in audit-log retrieved_docs labels.
    """
    return re.sub(r"\s+", "", s.strip())


def default_converted_md_dir() -> Path:
    import os
    return Path(os.environ.get("RAG_DATA_DIR", "/rag_data")) / "converted_md"


def load_article_index(converted_md_dir: Optional[Path] = None) -> Dict[Tuple[str, str], str]:
    """Return {(law_name, '第N條'): article_text} from every *.md in the dir.

    law_name is the filename without .md. Returns {} if the dir is missing.
    """
    index: Dict[Tuple[str, str], str] = {}
    d = Path(converted_md_dir) if converted_md_dir else default_converted_md_dir()
    if not d.exists():
        return index
    for f in sorted(d.glob("*.md")):
        law = f.stem
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        cur_id: Optional[str] = None
        buf: List[str] = []
        for line in lines:
            if _HEADER_RE.match(line.strip()):
                if cur_id and buf:
                    index[(law, cur_id)] = "\n".join(buf).strip()
                cur_id = _norm_article(line)
                buf = []
            elif cur_id is not None:
                buf.append(line)
        if cur_id and buf:
            index[(law, cur_id)] = "\n".join(buf).strip()
    return index


def resolve_context(
    retrieved_docs: List[str],
    index: Dict[Tuple[str, str], str],
    *,
    max_chars: int = 4000,
) -> str:
    """Build judge context from retrieved_docs labels using REAL article text.

    retrieved_docs entries look like 'chinese_law.md#第14條'. When the article
    text is found in the index it is inlined; otherwise the label is kept (so
    the judge still knows what was retrieved even if a file changed).
    """
    parts: List[str] = []
    for doc in retrieved_docs or []:
        if "#" not in doc:
            parts.append(f"- {doc}")
            continue
        law_part, art = doc.split("#", 1)
        law = law_part[:-3] if law_part.endswith(".md") else law_part
        art_norm = _norm_article(art)
        text = index.get((law, art_norm))
        if text:
            parts.append(f"【{law} {art_norm}】\n{text}")
        else:
            parts.append(f"- {law} {art_norm}（條文未解析）")
    return "\n\n".join(parts)[:max_chars]
