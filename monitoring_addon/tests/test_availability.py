import json
from monitoring.availability import AvailabilityProbe, load_availability


class FakeSink:
    def __init__(self): self.alerts = []
    def emit(self, alert): self.alerts.append(alert); return True


def _probe(tmp_path, deps, **kw):
    return AvailabilityProbe(FakeSink(), tmp_path, deps=deps,
                             critical=set(deps), fail_confirm=3, **kw)


def test_all_ok_no_alert_and_logs(tmp_path):
    deps = {"rag-api": lambda: (True, "", 1.0), "db": lambda: (True, "", 0.5)}
    p = _probe(tmp_path, deps)
    assert p.check() == 0
    lines = (tmp_path / "availability_log.jsonl").read_text().strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["overall_ok"] is True


def test_critical_dep_down_emits_on_third(tmp_path):
    deps = {"rag-api": lambda: (False, "conn refused", 9.0)}
    p = _probe(tmp_path, deps)
    assert p.check() == 0
    assert p.check() == 0
    assert p.check() == 1
    assert p.sink.alerts[0].severity == "critical"
    assert p.sink.alerts[0].dedup_key == "availability:down"


def test_recovery_resets_counter(tmp_path):
    state = {"ok": False}
    deps = {"rag-api": lambda: (state["ok"], "", 1.0)}
    p = _probe(tmp_path, deps)
    p.check(); p.check()
    state["ok"] = True
    assert p.check() == 0
    state["ok"] = False
    assert p.check() == 0      # counter reset; only 1 fail now


def test_load_availability_uptime(tmp_path):
    log = tmp_path / "availability_log.jsonl"
    log.write_text(
        json.dumps({"timestamp": "2026-06-26T10:00:00+08:00", "overall_ok": True, "per_dep": {}}) + "\n" +
        json.dumps({"timestamp": "2026-06-26T10:01:00+08:00", "overall_ok": False, "per_dep": {}}) + "\n" +
        json.dumps({"timestamp": "2026-06-26T10:02:00+08:00", "overall_ok": True, "per_dep": {}}) + "\n",
        encoding="utf-8")
    out = load_availability(log, window_hours=24 * 365 * 100)
    assert out["probes"] == 3
    assert round(out["uptime_pct"], 1) == round(2 / 3 * 100, 1)
    assert round(out["recent_ok_pct"], 1) == round(2 / 3 * 100, 1)
    assert out["current_ok"] is True


def test_rotate_when_oversized(tmp_path):
    deps = {"rag-api": lambda: (True, "", 1.0)}
    p = _probe(tmp_path, deps, rotate_mb=0.0)   # 0 → 每次都觸發 rotate
    p.check()
    p.check()
    archives = list(tmp_path.glob("availability_log_*.jsonl"))
    assert archives, "應產生歸檔檔，而非 truncate"


def test_rotate_on_month_change(tmp_path):
    deps = {"rag-api": lambda: (True, "", 1.0)}
    (tmp_path / "availability_log.jsonl").write_text(
        json.dumps({"timestamp": "2026-05-01T10:00:00+08:00", "overall_ok": True, "per_dep": {}}) + "\n",
        encoding="utf-8")
    p = AvailabilityProbe(FakeSink(), tmp_path, deps=deps, critical=set(deps), rotate_mb=999)
    p.check()
    assert (tmp_path / "availability_log_2026-05.jsonl").exists(), "跨月應把上月資料歸檔（以資料月份命名）"


def test_dedup_suppressed_emit_returns_zero(tmp_path):
    class DedupSink:
        def emit(self, alert): return False
    deps = {"rag-api": lambda: (False, "down", 9.0)}
    p = AvailabilityProbe(DedupSink(), tmp_path, deps=deps, critical=set(deps), fail_confirm=3)
    p.check(); p.check()
    assert p.check() == 0      # 第 3 次達門檻但 emit 被抑制 → 0


def test_load_availability_spans_archive(tmp_path):
    arch = tmp_path / "availability_log_2026-06.jsonl"
    arch.write_text(
        json.dumps({"timestamp": "2026-06-26T09:58:00+08:00", "overall_ok": True, "per_dep": {}}) + "\n" +
        json.dumps({"timestamp": "2026-06-26T09:59:00+08:00", "overall_ok": True, "per_dep": {}}) + "\n",
        encoding="utf-8")
    cur = tmp_path / "availability_log.jsonl"
    cur.write_text(
        json.dumps({"timestamp": "2026-06-26T10:00:00+08:00", "overall_ok": False, "per_dep": {}}) + "\n",
        encoding="utf-8")
    out = load_availability(cur, window_hours=24 * 365 * 100)
    assert out["probes"] == 3       # 2 歸檔 + 1 current，未漏讀歸檔


def test_recent_ok_recovers_even_when_24h_uptime_is_low(tmp_path):
    log = tmp_path / "availability_log.jsonl"
    rows = []
    for i in range(7):
        rows.append({"timestamp": f"2026-06-26T10:0{i}:00+08:00", "overall_ok": False, "per_dep": {}})
    for i in range(7, 10):
        rows.append({"timestamp": f"2026-06-26T10:0{i}:00+08:00", "overall_ok": True, "per_dep": {}})
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    out = load_availability(log, window_hours=24 * 365 * 100)
    assert out["uptime_pct"] == 30.0
    assert out["recent_ok_pct"] == 100.0
    assert out["hard_down"] is False
