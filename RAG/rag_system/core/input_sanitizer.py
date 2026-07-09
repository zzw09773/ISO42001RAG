"""
Input Sanitizer — ISO 42001 A.8 Security

Detects and blocks prompt injection, role-switching attacks, and
system information probing before user input reaches the LLM.
"""
import re
from dataclasses import dataclass
from typing import Optional

from .canonicalize import canonicalize, INJECTION_COLLAPSED_KEYWORDS


MAX_INPUT_LENGTH = 2000

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# injection 填充詞：ignore/disregard/forget 與 previous/prior/above 之間的
# 限定詞（僅列舉常見冠詞/指示詞/所有格，不用 \w+ 泛化，控誤判面）
_FILLER = r'(?:all|the|these|those|my|any)'

_INJECTION_PATTERNS = [
    # English injection
    re.compile(r'ignore\s+(' + _FILLER + r'\s+)?(previous|prior|above)\s+instructions?', re.IGNORECASE),
    re.compile(r'disregard\s+(' + _FILLER + r'\s+)?(previous|prior|above)\s+instructions?', re.IGNORECASE),
    re.compile(r'forget\s+(' + _FILLER + r'\s+)?(previous|prior|above)', re.IGNORECASE),
    re.compile(r'\bDAN\b', re.IGNORECASE),
    re.compile(r'do\s+anything\s+now', re.IGNORECASE),
    re.compile(r'jailbreak', re.IGNORECASE),
    # Token boundary injection
    re.compile(r'<\|im_start\|>|<\|im_end\|>|<\|endoftext\|>', re.IGNORECASE),
    re.compile(r'\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>', re.IGNORECASE),
    re.compile(r'</?(system|user|assistant)>', re.IGNORECASE),
    # System prompt override
    re.compile(r'\bsystem\s*:', re.IGNORECASE),
    re.compile(r'new\s+(system\s+)?prompt\s*:', re.IGNORECASE),
    # forget X above
    re.compile(r'forget\s+\w+\s+above', re.IGNORECASE),
]

_SYSTEM_PROBE_PATTERNS = [
    # Asking for system prompt (covers "show me your ...", "what are your ...")
    re.compile(r'(print|show(\s+me)?|repeat|output|display|reveal|tell\s+me|what\s+(is|are))\s+(your\s+)?(system\s+prompt|instructions?|rules?)', re.IGNORECASE),
    re.compile(r'(系統提示詞|系統指令|你的指令|你的規則|你的設定)\s*(是什麼|給我|說出|顯示)', re.IGNORECASE),
    re.compile(r'告訴\s*我\s*(你的|你有什麼)?\s*(指令|規則|設定|系統提示)', re.IGNORECASE),
    # Asking for connection strings / credentials
    re.compile(r'(connection\s+string|database\s+url|db\s+url|conn_string)', re.IGNORECASE),
    re.compile(r'(postgresql|mongodb|redis|mysql)\s*://', re.IGNORECASE),
    re.compile(r'(api[_\s]?key|api[_\s]?token|secret[_\s]?key|bearer\s+token)', re.IGNORECASE),
    re.compile(r'(連接字串|資料庫密碼|資料庫帳號|API\s*金鑰)', re.IGNORECASE),
    # 憑證變體（維持列舉具體詞風格，避免單一「密碼」誤判合法查詢）
    re.compile(r'(sql\s*密碼|sql\s*帳號|db\s*密碼|sql\s*password|db\s*password)', re.IGNORECASE),
    # Asking for server paths
    re.compile(r'(server\s+path|file\s+path|directory\s+path|working\s+directory)', re.IGNORECASE),
    re.compile(r'/home/\w+|/var/|/etc/|C:\\\\', re.IGNORECASE),
    re.compile(r'(伺服器路徑|檔案路徑|工作目錄)', re.IGNORECASE),
]

_SQL_INJECTION_PATTERNS = [
    # Classic tautology（含/不含引號）
    re.compile(r"'\s*(or|and)\s*'?\d+'?\s*=\s*'?\d+", re.IGNORECASE),
    re.compile(r"\b(or|and)\s+\d+\s*=\s*\d+", re.IGNORECASE),
    # Destructive statements
    re.compile(r"\b(drop|truncate)\s+table\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
    re.compile(r"\bunion\s*(all\s*)?select\b", re.IGNORECASE),
    # Schema probing
    re.compile(r"\binformation_schema\b", re.IGNORECASE),
    re.compile(r"\bpg_catalog\b|\bpg_tables\b|\bpg_class\b", re.IGNORECASE),
    re.compile(r"\bsys\.(tables|columns|databases)\b", re.IGNORECASE),
    # Time-based blind injection
    re.compile(r"\b(sleep|pg_sleep|waitfor\s+delay)\s*\(", re.IGNORECASE),
    # Stacked queries
    re.compile(r";\s*(select|insert|update|delete|drop)\b", re.IGNORECASE),
]

# LDAP 拆兩組：
#  - 風險/屬性型跑 norm（不會誤中中文合法文字）
#  - 結構型過濾器語法跑 RAW text，且必須帶相鄰運算子（| & !），避免全形/半形
#    相鄰括號（如「（債編）（第二版）」NFKC 折成 )(）誤擋合法法律文字。
_LDAP_RISK_PATTERNS = [
    # Wildcard authentication bypass: uid=* or cn=*
    re.compile(r'\b(uid|cn|dn|sn|mail|ou|dc|objectclass)\s*=\s*\*', re.IGNORECASE),
    # Probing common LDAP attributes
    re.compile(r'\b(userPassword|sambaNTPassword|unicodePwd)\b', re.IGNORECASE),
    # Null byte injection
    re.compile(r'\\00|%00|\x00', re.IGNORECASE),
]

_LDAP_STRUCT_PATTERNS = [
    # closing paren + new condition with operator: )(|  )(&  )(!
    re.compile(r'\)\s*\(\s*[|&!]', re.IGNORECASE),
    # Classic LDAP bypass: *)(  （如 *)(uid=*）
    re.compile(r'\*\s*\)\s*\(', re.IGNORECASE),
    # LDAP filter operators injected: (|(  (&(  (!(
    re.compile(r'\(\s*[|&!]\s*\(', re.IGNORECASE),
]

_SSRF_PATTERNS = [
    # Internal / loopback addresses
    re.compile(r'https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)', re.IGNORECASE),
    re.compile(r'https?://169\.254\.169\.254', re.IGNORECASE),   # AWS metadata
    re.compile(r'https?://10\.\d+\.\d+\.\d+', re.IGNORECASE),   # RFC-1918
    re.compile(r'https?://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+', re.IGNORECASE),
    re.compile(r'https?://192\.168\.\d+\.\d+', re.IGNORECASE),
    # Docker / Kubernetes internal hostnames
    re.compile(r'https?://(host\.docker\.internal|kubernetes\.default)', re.IGNORECASE),
    # Non-HTTP schemes that could trigger server-side fetch
    re.compile(r'\b(file|gopher|dict|ftp)://', re.IGNORECASE),
    # URL redirection tricks
    re.compile(r'(url|redirect|fetch|request|load|open)\s*=\s*["\']?https?://', re.IGNORECASE),
]

_CSRF_PATTERNS = [
    # Attempts to extract or forge CSRF tokens via the LLM
    re.compile(r'csrf[_\-\s]?(token|key|nonce)', re.IGNORECASE),
    re.compile(r'x-csrf|x-xsrf', re.IGNORECASE),
    # Injecting HTML form / hidden fields into LLM output（HTML 或純文字）
    re.compile(r'(<\s*form\s[^>]*action\s*=|form\s+action\s+https?://)', re.IGNORECASE),
    re.compile(r'<\s*input\s[^>]*type\s*=\s*["\']?hidden', re.IGNORECASE),
    # Forged request payloads embedded in queries
    re.compile(r'(POST|PUT|PATCH|DELETE)\s+https?://', re.IGNORECASE),
]

_ROLE_SWITCH_PATTERNS = [
    re.compile(r'(你現在是|你是一個|假裝你是|扮演|變成).{0,20}(AI|機器人|助手|系統|GPT|LLM|大型語言模型|模型)', re.IGNORECASE),
    # \b 必要：無詞界會誤中 "contact as ..." 等合法句尾 act 子字串
    re.compile(r'\bact\s+as\s+(a\s+)?', re.IGNORECASE),
    re.compile(r'pretend\s+(you\s+are|to\s+be)', re.IGNORECASE),
    re.compile(r'roleplay\s+as', re.IGNORECASE),
    re.compile(r'you\s+are\s+now\s+(a\s+)?', re.IGNORECASE),
    re.compile(r'imagine\s+you\s+(are|have\s+no)', re.IGNORECASE),
]


@dataclass
class SanitizeResult:
    """Result of input sanitization check."""
    blocked: bool
    reason: Optional[str] = None
    threat_type: Optional[str] = None


def sanitize(text: str, is_wrapper: bool = False) -> SanitizeResult:
    """檢查使用者輸入的注入/探測/危險模式。

    detection-only：對 canonicalize 產生的視圖比對，不改寫 text 本身。
    is_wrapper=True（OpenWebUI 背景任務，由 API 層以不可偽造條件判定後傳入）時，
    豁免 injection/role-switch/system-probe，但仍強制長度/SSRF/SQL/LDAP/CSRF。
    """
    if len(text) > MAX_INPUT_LENGTH:
        return SanitizeResult(
            blocked=True,
            reason=f"輸入長度超過上限（{len(text)} > {MAX_INPUT_LENGTH} 字元）",
            threat_type="input_too_long",
        )

    views = canonicalize(text)
    norm = views.normalized
    # spaced：隱形字元以空白代換的視圖 — 抓「零寬取代空白」的邊界規避
    # （act[ZWSP]as → norm 黏成 actas 繞過 \s+；spaced 還原 act as）。
    # 無隱形字元時 spaced == norm，不增加誤判面（隱形字元落在詞內的罕見情況才可能
    # 製造詞界，靠既有 \b 錨點收斂）。
    spaced = views.spaced

    # ── 結構化 SSRF：內網/危險 host（整數/十六進位/IPv6/短式皆已解析）──
    for h in views.hosts:
        if h.kind == "ip" and h.category in ("loopback", "private", "link_local", "metadata"):
            return SanitizeResult(blocked=True, reason=f"偵測到內部/危險位址（{h.category}）",
                                  threat_type="ssrf")
    for pattern in _SSRF_PATTERNS:   # 具名內部 host、非 http scheme、重導
        if pattern.search(norm):
            return SanitizeResult(blocked=True, reason="偵測到 SSRF 攻擊模式", threat_type="ssrf")

    # ── SQL：跑去註解後的 sql_view ──
    for pattern in _SQL_INJECTION_PATTERNS:
        if pattern.search(views.sql_view):
            return SanitizeResult(blocked=True, reason="偵測到 SQL Injection 攻擊模式",
                                  threat_type="sql_injection")

    # LDAP：風險/屬性型跑 norm；結構型跑 RAW text（需相鄰運算子，免誤擋全形括號）
    for pattern in _LDAP_RISK_PATTERNS:
        if pattern.search(norm):
            return SanitizeResult(blocked=True, reason="偵測到 LDAP Injection 攻擊模式",
                                  threat_type="ldap_injection")
    for pattern in _LDAP_STRUCT_PATTERNS:
        if pattern.search(text):
            return SanitizeResult(blocked=True, reason="偵測到 LDAP Injection 攻擊模式",
                                  threat_type="ldap_injection")

    for pattern in _CSRF_PATTERNS:
        if pattern.search(norm):
            return SanitizeResult(blocked=True, reason="偵測到 CSRF 攻擊模式", threat_type="csrf")

    # ── 以下為 wrapper 豁免類別（injection / system_probe / role_switch）──
    if is_wrapper:
        return SanitizeResult(blocked=False)

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(norm) or pattern.search(spaced):
            return SanitizeResult(blocked=True, reason="偵測到 Prompt Injection 攻擊模式",
                                  threat_type="prompt_injection")

    # system_probe 先於 collapsed injection 比對：
    # 正常語序的系統探測（如「show/reveal your system prompt」）其 collapsed 視圖含
    # 關鍵詞 "systemprompt"，若先跑 collapsed 會被誤標為 prompt_injection，破壞既有
    # system_probe 分類（稽核證據用）。破拆字注入（i g n o r e）不會命中 system_probe
    # 的字面 regex，故仍會落到下方 collapsed 檢查，分類不受影響。
    for pattern in _SYSTEM_PROBE_PATTERNS:
        if pattern.search(norm):
            return SanitizeResult(blocked=True, reason="偵測到系統資訊探測嘗試",
                                  threat_type="system_probe")

    # collapsed 關鍵詞（破拆字 i g n o r e）
    if any(kw in views.collapsed for kw in INJECTION_COLLAPSED_KEYWORDS):
        return SanitizeResult(blocked=True, reason="偵測到 Prompt Injection（去空白比對）",
                              threat_type="prompt_injection")

    for pattern in _ROLE_SWITCH_PATTERNS:
        if pattern.search(norm) or pattern.search(spaced):
            return SanitizeResult(blocked=True, reason="偵測到角色切換攻擊",
                                  threat_type="role_switch")

    return SanitizeResult(blocked=False)
