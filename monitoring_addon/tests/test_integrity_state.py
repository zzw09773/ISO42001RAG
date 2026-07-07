import json
from datetime import datetime, timedelta, timezone

from monitoring.alert_checkers import IntegrityChecker, load_integrity_state


def test_load_missing_is_unknown(tmp_path):
    assert load_integrity_state(tmp_path) == "unknown"


def test_load_reads_status(tmp_path):
    (tmp_path / "integrity_state.json").write_text(
        json.dumps({"last_integrity_status": "broken", "checked_at": "2026-06-26T10:00:00+08:00"}),
        encoding="utf-8")
    assert load_integrity_state(tmp_path) == "broken"


def test_state_broken_even_when_alert_deduped(tmp_path):
    """鏈損毀但告警被 dedup 抑制（emit→False）時，state 仍須為 broken。"""
    audit = tmp_path / "audit"; audit.mkdir()
    data = tmp_path / "data"; data.mkdir()
    tpe = timezone(timedelta(hours=8))
    today = datetime.now(tpe).date().isoformat()
    # 第一行 prev_hash 非 genesis → _inline_verify_integrity 判 invalid
    (audit / f"audit_{today}.jsonl").write_text(
        '{"prev_hash":"deadbeef","entry_hash":"x"}\n', encoding="utf-8")

    class DedupSink:                # emit 永遠被抑制
        def emit(self, alert): return False

    IntegrityChecker(audit, DedupSink(), data_dir=data).check()
    assert load_integrity_state(data) == "broken"


def test_state_intact_when_no_broken(tmp_path):
    """無損毀（無 audit 檔）→ 寫 intact。"""
    audit = tmp_path / "audit"; audit.mkdir()
    data = tmp_path / "data"; data.mkdir()

    class Sink:
        def emit(self, alert): return True

    IntegrityChecker(audit, Sink(), data_dir=data).check()
    assert load_integrity_state(data) == "intact"
