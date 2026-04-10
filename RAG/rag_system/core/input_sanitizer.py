"""
Input Sanitizer — ISO 42001 A.8 Security

Detects and blocks prompt injection, role-switching attacks, and
system information probing before user input reaches the LLM.
"""
import re
from dataclasses import dataclass
from typing import Optional


MAX_INPUT_LENGTH = 2000

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    # English injection
    re.compile(r'ignore\s+(all\s+)?(previous|prior|above)\s+instructions?', re.IGNORECASE),
    re.compile(r'disregard\s+(all\s+)?(previous|prior|above)\s+instructions?', re.IGNORECASE),
    re.compile(r'forget\s+(all\s+)?(previous|prior|above)', re.IGNORECASE),
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
    re.compile(r"\bunion\s+(all\s+)?select\b", re.IGNORECASE),
    # Schema probing
    re.compile(r"\binformation_schema\b", re.IGNORECASE),
    re.compile(r"\bpg_catalog\b|\bpg_tables\b|\bpg_class\b", re.IGNORECASE),
    re.compile(r"\bsys\.(tables|columns|databases)\b", re.IGNORECASE),
    # Time-based blind injection
    re.compile(r"\b(sleep|pg_sleep|waitfor\s+delay)\s*\(", re.IGNORECASE),
    # Stacked queries
    re.compile(r";\s*(select|insert|update|delete|drop)\b", re.IGNORECASE),
]

_LDAP_INJECTION_PATTERNS = [
    # Filter manipulation — closing paren followed by new condition
    re.compile(r'\)\s*(\(|\||\&|!)', re.IGNORECASE),
    # Wildcard authentication bypass: uid=* or cn=*
    re.compile(r'\b(uid|cn|dn|sn|mail|ou|dc|objectclass)\s*=\s*\*', re.IGNORECASE),
    # Classic LDAP bypass: admin)(&) or *)(uid=*))(|(uid=*
    re.compile(r'\*\s*\)\s*\(', re.IGNORECASE),
    # LDAP filter operators injected in query
    re.compile(r'\(\s*(\||&|!)\s*\(', re.IGNORECASE),
    # Null byte injection
    re.compile(r'\\00|%00|\x00', re.IGNORECASE),
    # Probing common LDAP attributes
    re.compile(r'\b(userPassword|sambaNTPassword|unicodePwd)\b', re.IGNORECASE),
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
    re.compile(r'(你現在是|你是一個|假裝你是|扮演|變成).{0,20}(AI|機器人|助手|系統|GPT|Claude)', re.IGNORECASE),
    re.compile(r'act\s+as\s+(a\s+)?', re.IGNORECASE),
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


def sanitize(text: str) -> SanitizeResult:
    """
    Check user input for prompt injection and system probing attempts.

    Returns SanitizeResult with blocked=True if malicious input is detected.
    The caller should log and reject without passing to the LLM.
    """
    if len(text) > MAX_INPUT_LENGTH:
        return SanitizeResult(
            blocked=True,
            reason=f"輸入長度超過上限（{len(text)} > {MAX_INPUT_LENGTH} 字元）",
            threat_type="input_too_long",
        )

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return SanitizeResult(
                blocked=True,
                reason="偵測到 Prompt Injection 攻擊模式",
                threat_type="prompt_injection",
            )

    for pattern in _SYSTEM_PROBE_PATTERNS:
        if pattern.search(text):
            return SanitizeResult(
                blocked=True,
                reason="偵測到系統資訊探測嘗試",
                threat_type="system_probe",
            )

    for pattern in _SQL_INJECTION_PATTERNS:
        if pattern.search(text):
            return SanitizeResult(
                blocked=True,
                reason="偵測到 SQL Injection 攻擊模式",
                threat_type="sql_injection",
            )

    for pattern in _LDAP_INJECTION_PATTERNS:
        if pattern.search(text):
            return SanitizeResult(
                blocked=True,
                reason="偵測到 LDAP Injection 攻擊模式",
                threat_type="ldap_injection",
            )

    for pattern in _SSRF_PATTERNS:
        if pattern.search(text):
            return SanitizeResult(
                blocked=True,
                reason="偵測到 SSRF 攻擊模式",
                threat_type="ssrf",
            )

    for pattern in _CSRF_PATTERNS:
        if pattern.search(text):
            return SanitizeResult(
                blocked=True,
                reason="偵測到 CSRF 攻擊模式",
                threat_type="csrf",
            )

    for pattern in _ROLE_SWITCH_PATTERNS:
        if pattern.search(text):
            return SanitizeResult(
                blocked=True,
                reason="偵測到角色切換攻擊",
                threat_type="role_switch",
            )

    return SanitizeResult(blocked=False)
