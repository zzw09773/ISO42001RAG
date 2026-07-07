from rag_system.core.prompts import PROMPT_VERSIONS, prompt_version_hash
from rag_system.core.version import (
    OPENWEBUI_MODEL_ID,
    SYSTEM_BASELINE_DATE,
    SYSTEM_BASELINE_NAME,
    SYSTEM_VERSION,
    SYSTEM_VERSION_LABEL,
)


def test_system_version_matches_external_audit_baseline():
    assert SYSTEM_VERSION == "1.1.0"
    assert SYSTEM_VERSION_LABEL == "v1.1.0"
    assert SYSTEM_BASELINE_DATE == "2026-07-07"
    assert SYSTEM_BASELINE_NAME == "external_audit_ready"
    assert OPENWEBUI_MODEL_ID == "rag-agent"


def test_prompt_baseline_is_single_version():
    assert PROMPT_VERSIONS == {"SYSTEM_PROMPT_BASELINE": SYSTEM_VERSION}
    assert prompt_version_hash() == "e61133c0a264b08604706292ba2dbf59b3092e1d9208b1e5c1f971b88c79dc3c"
