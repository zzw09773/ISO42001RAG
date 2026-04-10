"""
Output Filter — ISO 42001 A.8 Security

Scans LLM responses for accidental leakage of sensitive information
(connection strings, server paths, API keys, system prompt content)
and redacts matches before returning to the user.
"""
import re
from dataclasses import dataclass, field
from typing import List


# ---------------------------------------------------------------------------
# Sensitive patterns to redact from LLM output
# ---------------------------------------------------------------------------

_REDACT_RULES: List[tuple[str, re.Pattern, str]] = [
    (
        "connection_string",
        re.compile(r'(postgresql|mysql|mongodb|redis|sqlite)\+?\w*://[^\s\'"<>]+', re.IGNORECASE),
        "[REDACTED:connection_string]",
    ),
    (
        "server_path",
        re.compile(r'/home/[a-zA-Z0-9_\-]+(/[^\s\'"<>]*)+', re.IGNORECASE),
        "[REDACTED:server_path]",
    ),
    (
        "etc_path",
        re.compile(r'/(?:var|etc|usr|opt|root|proc)/[^\s\'"<>]+', re.IGNORECASE),
        "[REDACTED:server_path]",
    ),
    (
        "windows_path",
        re.compile(r'[A-Za-z]:\\[^\s\'"<>]+', re.IGNORECASE),
        "[REDACTED:server_path]",
    ),
    (
        "api_key_bearer",
        re.compile(r'Bearer\s+[A-Za-z0-9\-_\.]{20,}', re.IGNORECASE),
        "Bearer [REDACTED:api_key]",
    ),
    (
        "long_token",
        # Catch hex/base64 tokens ≥ 32 chars that look like secrets
        re.compile(r'\b[A-Za-z0-9+/]{32,}={0,2}\b'),
        "[REDACTED:token]",
    ),
]


@dataclass
class FilterResult:
    """Result of output filtering."""
    text: str
    redacted: bool
    findings: List[str] = field(default_factory=list)


def filter_output(text: str) -> FilterResult:
    """
    Scan and redact sensitive information from LLM output.

    Always returns a FilterResult. If redacted=True, the caller
    should log a security_alert before returning to the user.
    """
    result = text
    findings: List[str] = []

    for label, pattern, replacement in _REDACT_RULES:
        new_result, count = pattern.subn(replacement, result)
        if count > 0:
            findings.append(label)
            result = new_result

    return FilterResult(
        text=result,
        redacted=len(findings) > 0,
        findings=findings,
    )
