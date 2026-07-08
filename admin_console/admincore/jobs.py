"""單一併發背景 job 管理：docker exec 執行、jobs.jsonl 與 changes.jsonl 留痕。"""
from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime
from pathlib import Path


class JobBusy(RuntimeError):
    """已有 job 執行中；全域互斥（評估互搶資源會失真）。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class JobManager:
    def __init__(self, data_dir: Path, runner):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._runner = runner
        self._lock = threading.Lock()
        self._job: dict | None = None
        self._tail: deque[str] = deque(maxlen=80)
        self._thread: threading.Thread | None = None

    # ── public ────────────────────────────────────────────────
    def start(self, name: str, container: str, cmd: list[str], meta: dict | None = None) -> dict:
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise JobBusy(f"job {self._job['name']} 執行中")
            self._tail = deque(maxlen=80)
            self._job = {
                "id": f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{name}",
                "name": name, "container": container, "cmd": cmd,
                "meta": meta or {}, "state": "running",
                "started_at": _now(), "ended_at": None, "exit_code": None,
            }
            # 在鎖內取剛啟動（running）狀態快照後回傳；避免 worker thread 於
            # start() 回傳前搶先跑完（fake runner 極快），使快照誤讀成 done。
            snap = dict(self._job)
            snap["tail"] = list(self._tail)
        self.log_change({"kind": "job", "name": name, "container": container, "cmd": cmd})
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return snap

    def current(self) -> dict | None:
        with self._lock:
            if self._job is None:
                return None
            snap = dict(self._job)
            snap["tail"] = list(self._tail)
            return snap

    def wait(self, timeout: float = 30) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    def log_change(self, entry: dict) -> None:
        rec = {"ts": _now(), **entry}
        with (self.data_dir / "changes.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ── internal ──────────────────────────────────────────────
    def _run(self) -> None:
        exit_code: int | None = None
        try:
            lines, wait_exit = self._runner(self._job["container"], self._job["cmd"])
            for line in lines:
                with self._lock:
                    self._tail.append(str(line).rstrip())
            exit_code = int(wait_exit())
        except Exception as e:  # runner 例外＝失敗，不吞
            with self._lock:
                self._tail.append(f"[runner error] {e}")
            exit_code = -1
        with self._lock:
            self._job["exit_code"] = exit_code
            self._job["state"] = "done" if exit_code == 0 else "failed"
            self._job["ended_at"] = _now()
            record = dict(self._job)
            record["tail"] = list(self._tail)
        with (self.data_dir / "jobs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
