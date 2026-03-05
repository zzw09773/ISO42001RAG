from rag_system.node import _extract_sources_from_text, _collect_sources, _build_sources_section


def test_extract_sources_handles_metadata_line():
    tool_output = (
        "=== 文件 1 ===\n"
        "來源: flight_manual.pdf, 頁碼: 5\n"
        "內容: ...\n"
        "---\n"
        "=== 文件 2 ===\n"
        "來源: aero_report.docx\n"
        "Metadata: 模組: 氣動設計\n"
        "內容: ...\n"
    )

    entries = _extract_sources_from_text(tool_output)

    assert entries == [
        "flight_manual.pdf, 頁碼: 5",
        "aero_report.docx (模組: 氣動設計)",
    ]


def test_collect_sources_deduplicates_and_preserves_order():
    tool_responses = [
        {"name": "retrieve_legal_documents", "content": "來源: a.md, 頁碼: 3\n內容: ..."},
        {"name": "metadata", "content": "來源: a.md, 頁碼: 3\n內容: ..."},
        {"name": "metadata", "content": "來源: b.md\nMetadata: 模組: 結構分析"},
    ]

    collected = _collect_sources(tool_responses)

    assert collected == [
        "a.md, 頁碼: 3",
        "b.md (模組: 結構分析)",
    ]


def test_build_sources_section_format():
    section = _build_sources_section(["alpha.md, 頁碼: 2", "beta.docx (模組: 空氣力學)"])

    assert section.startswith("\n\n參考資料:\n")
    assert "- 來源: alpha.md, 頁碼: 2" in section
    assert section.strip().endswith("- 來源: beta.docx (模組: 空氣力學)")


def test_build_sources_section_empty_returns_blank():
    assert _build_sources_section([]) == ""
