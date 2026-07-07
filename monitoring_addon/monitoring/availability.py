"""Composite dependency availability probe (read-only, addon-local).

Probes rag-api/health, embed-proxy/ready, DB TCP, LLM /models. ok 條件解析
回應內容，不只看 2xx（embed-proxy /ready 即使 Triton 掛仍回 200）。連續
PROBE_FAIL_CONFIRM 次關鍵依賴失敗 → 直接 emit critical（bypass N 窗）。
不 import rag_system.*；不送合成查詢；log 非破壞式 rotate（跨月或滿 N MB）。
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, Set, Tuple

from .alerting import Alert, AlertSink

_TPE = timezone(timedelta(hours=8))
DepCheck = Callable[[], Tuple[bool, str, float]]


def _now_iso() -> str:
    return datetime.now(_TPE).isoformat()


def _http_check(url: str, validate: Callable[[dict], bool], timeout: float,
                headers: Optional[dict] = None) -> DepCheck:
    def check() -> Tuple[bool, str, float]:
        import time
        import requests
        t0 = time.perf_counter()
        try:
            r = requests.get(url, timeout=timeout, headers=headers or {},
                             verify=os.environ.get("VERIFY_SSL", "true").lower() != "false")
            rtt = (time.perf_counter() - t0) * 1000
            if r.status_code // 100 != 2:
                return False, f"http {r.status_code}", rtt
            return (validate(r.json()), "", rtt)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000
    return check


def _tcp_check(host: str, port: int, timeout: float) -> DepCheck:
    def check() -> Tuple[bool, str, float]:
        import time
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True, "", (time.perf_counter() - t0) * 1000
        except Exception as e:
            return False, f"{type(e).__name__}", (time.perf_counter() - t0) * 1000
    return check


def default_deps() -> Dict[str, DepCheck]:
    timeout = float(os.environ.get("PROBE_TIMEOUT_SEC", "5"))
    rag_url = os.environ.get("RAG_HEALTH_URL", "http://rag-api:8000/health")
    ready_url = os.environ.get("EMBED_READY_URL", "http://embed-proxy:7100/ready")
    llm_base = os.environ.get("LLM_API_BASE", "").rstrip("/")
    llm_url = os.environ.get("LLM_MODELS_URL", (llm_base + "/models") if llm_base else "")
    db_host = os.environ.get("DB_HOST", "db")
    db_port = int(os.environ.get("DB_PORT", "5432"))
    llm_key = os.environ.get("LLM_API_KEY", "")
    llm_headers = {"Authorization": f"Bearer {llm_key}"} if llm_key else None

    deps: Dict[str, DepCheck] = {
        "rag-api": _http_check(rag_url, lambda j: j.get("model_loaded") is True, timeout),
        "embed-proxy": _http_check(
            ready_url,
            lambda j: j.get("error") is None and j.get("server_ready") is True
            and j.get("model_ready") is True,
            timeout),
        "db": _tcp_check(db_host, db_port, timeout),
    }
    if llm_url:
        # LLM gateway 可能要 API key（compose 已注入 LLM_API_KEY）→ 帶 Authorization，
        # 否則需金鑰的 gateway 會回 401 被誤判 down。
        deps["llm"] = _http_check(llm_url, lambda j: bool(j.get("data")), timeout,
                                  headers=llm_headers)
    return deps


def _critical_from_env(deps: Dict[str, DepCheck]) -> Set[str]:
    raw = os.environ.get("AVAIL_CRITICAL_DEPS", "rag-api,embed-proxy,llm,db")
    want = {x.strip() for x in raw.split(",") if x.strip()}
    return {d for d in deps if d in want} or set(deps)


class AvailabilityProbe:
    def __init__(self, sink: AlertSink, data_dir: Path, *,
                 deps: Optional[Dict[str, DepCheck]] = None,
                 critical: Optional[Set[str]] = None,
                 fail_confirm: int = 3, rotate_mb: float = 5.0):
        self.sink = sink
        self.data_dir = Path(data_dir)
        self.log_path = self.data_dir / "availability_log.jsonl"
        self.deps = deps if deps is not None else default_deps()
        self.critical = critical if critical is not None else _critical_from_env(self.deps)
        self.fail_confirm = fail_confirm
        self.rotate_mb = rotate_mb
        self._consecutive_fail = 0

    def check(self) -> int:
        per_dep = {}
        for name, fn in self.deps.items():
            ok, detail, rtt = fn()
            per_dep[name] = {"ok": ok, "detail": detail, "rtt_ms": round(rtt, 1)}
        overall_ok = all(per_dep[n]["ok"] for n in self.critical)
        self._append({"timestamp": _now_iso(), "overall_ok": overall_ok, "per_dep": per_dep})
        if overall_ok:
            self._consecutive_fail = 0
            return 0
        self._consecutive_fail += 1
        if self._consecutive_fail >= self.fail_confirm:
            down = [n for n in self.critical if not per_dep[n]["ok"]]
            alert = Alert(
                severity="critical", source="availability",
                title="system availability down",
                message=(f"關鍵依賴連續 {self._consecutive_fail} 次探測失敗：{down}。"
                         f"（/ready 失敗可能為 Triton 後端掛或 embed-proxy gRPC 設定過時，勿逕自斷定）"),
                evidence={"down": down, "per_dep": per_dep,
                          "consecutive_fail": self._consecutive_fail},
                dedup_key="availability:down",
            )
            return 1 if self.sink.emit(alert) else 0   # 被 dedup 抑制 → 0（無新告警）
        return 0

    def _append(self, rec: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _rotate_if_needed(self) -> None:
        if not self.log_path.exists():
            return
        too_big = self.log_path.stat().st_size >= self.rotate_mb * 1024 * 1024
        last_month = self._last_record_month()
        cross_month = last_month is not None and last_month != datetime.now(_TPE).strftime("%Y-%m")
        if too_big or cross_month:
            stamp = last_month or datetime.now(_TPE).strftime("%Y-%m")  # 以資料所屬月份命名
            archive = self.data_dir / f"availability_log_{stamp}.jsonl"
            i = 1
            while archive.exists():
                archive = self.data_dir / f"availability_log_{stamp}.{i}.jsonl"
                i += 1
            self.log_path.rename(archive)   # 非破壞式：rename 不刪資料

    def _last_record_month(self) -> Optional[str]:
        """讀 log 最後一筆的 timestamp 月份（YYYY-MM），用於跨月歸檔。"""
        try:
            last = None
            with open(self.log_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = line
            if not last:
                return None
            ts = json.loads(last).get("timestamp", "")
            return ts[:7] if len(ts) >= 7 else None
        except Exception:
            return None


def load_availability(log_path: Path, window_hours: int = 24, recent_probes: int = 3) -> dict:
    log_path = Path(log_path)
    cutoff = datetime.now(_TPE) - timedelta(hours=window_hours)
    # 讀 current + 同目錄歸檔（rotate 後 24h 視窗可能跨到 availability_log_YYYY-MM.jsonl）
    files = [log_path] + sorted(log_path.parent.glob(log_path.stem + "_*.jsonl"))
    rows = []
    for fp in files:
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec["timestamp"])
            except Exception:
                continue
            if ts >= cutoff:
                rows.append(rec)
    if not rows:
        return {"uptime_pct": None, "probes": 0, "per_dep_uptime": {},
                "recent_ok_pct": None, "recent_probes": 0,
                "current_ok": None, "current_at": None,
                "current_per_dep": {},
                "hard_down": False, "last_ok_at": None}
    rows.sort(key=lambda r: r.get("timestamp", ""))   # 跨檔合併後重排，last_ok/hard_down 才正確
    ok = sum(1 for r in rows if r.get("overall_ok"))
    per_dep_total: Dict[str, int] = {}
    per_dep_ok: Dict[str, int] = {}
    for r in rows:
        for name, d in (r.get("per_dep") or {}).items():
            per_dep_total[name] = per_dep_total.get(name, 0) + 1
            per_dep_ok[name] = per_dep_ok.get(name, 0) + (1 if d.get("ok") else 0)
    per_dep_uptime = {n: round(per_dep_ok[n] / per_dep_total[n] * 100, 2)
                      for n in per_dep_total}
    last_ok = next((r["timestamp"] for r in reversed(rows) if r.get("overall_ok")), None)
    tail = rows[-max(1, recent_probes):]
    recent_ok = sum(1 for r in tail if r.get("overall_ok"))
    current = rows[-1]
    hard_down = len(tail) >= 3 and all(not r.get("overall_ok") for r in tail)
    return {
        "uptime_pct": round(ok / len(rows) * 100, 2),
        "probes": len(rows),
        "per_dep_uptime": per_dep_uptime,
        # Recent/current fields drive the live health light. The cumulative
        # uptime above remains audit evidence, but must not make recovery sticky.
        "recent_ok_pct": round(recent_ok / len(tail) * 100, 2) if tail else None,
        "recent_probes": len(tail),
        "current_ok": bool(current.get("overall_ok")),
        "current_at": current.get("timestamp"),
        "current_per_dep": current.get("per_dep") or {},
        "hard_down": hard_down,
        "last_ok_at": last_ok,
    }


__all__ = ["AvailabilityProbe", "load_availability", "default_deps"]
