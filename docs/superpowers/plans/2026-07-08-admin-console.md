# ISO 42001 維運管理台（admin console）— 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增獨立 admin 容器（:8300）收攏維運操作——model 設定寫 `.env` + 一鍵重啟 rag-api、docker-exec 觸發評估/索引腳本、報告檢視與 flip 比對、告警測試——並同捆完成 monitoring 告警訊息拆層次（規格：`docs/superpowers/specs/2026-07-08-admin-console-design.md`）。

**Architecture:** admin 為 FastAPI + 伺服器端渲染單頁（沿用印刷報告書視覺 token），核心分四個獨立模組：`envstore`（白名單 .env 讀改寫）、`jobs`（單一併發 job 管理 + 留痕）、`dockerops`（docker SDK 包裝：exec 串流/重啟/生效 env）、`reports`（報告列表與 per-query flip 比對）。腳本一律 `docker exec` 到原生容器執行（評估→ISO42001_monitoring，索引→ISO42001_rag_api），admin 鏡像不帶評估依賴。

**Tech Stack:** Python 3.11、FastAPI、uvicorn、docker SDK（`docker` 套件）、httpx、pytest。

## Global Constraints

- 工作目錄：`/home/c1147259/桌面/ISO42001/ISO42001RAG`（repo 根）；分支 main。
- UI 文案一律 zh-TW（台灣用語）、**無 emoji**；視覺沿用報告書 token（`--ink:#1a1f2c; --muted:#5b6578; --line:#c9d1dc; --hairline:#e5eaf1; --accent:#1e3a8a; mono:"JetBrains Mono","Consolas",monospace`）、無漸層/陰影/圓角卡。
- 設定白名單固定 10 鍵（見 Task 1 `SETTINGS`）；金鑰與連線 URL 類**絕不**進 UI 或回傳值。
- **`.env` 必須原地覆寫（同 inode）**：容器對 `.env` 是單檔 bind mount，`tempfile+os.replace` 會換 inode 使容器內看到舊檔——一律 `Path.write_text()`，禁止 rename 寫法。
- 備份寫到 admin 自己的資料目錄（`admin_console/data/env-backups/`，時間戳檔名），不寫回 repo 根（單檔 mount 旁無法建檔）。
- `dockerops` 模組**延遲 import docker**（在建構子內、僅當未注入 client 時），讓宿主測試不需安裝 docker SDK。
- job 全域互斥：同時只允許一個 running job；重複觸發回 409。
- 容器名固定：`ISO42001_monitoring`、`ISO42001_rag_api`；容器內報告路徑 `/app/data/reports`（monitoring 的 bind mount `./monitoring_addon/data:/app/data` 已存在於 compose）。
- 測試命令：`cd admin_console && python3 -m pytest tests/ -q`（Task 7 為 `cd monitoring_addon && python3 -m pytest tests/ -q`）。全綠才可 commit。
- **登入保護（憑證卡為主）**：整站（含 `/api/*`）需登入。主通道＝中科院憑證卡（HiPKI/PKCS#7）：challenge nonce → 本機元件簽章 → CMS 驗簽＋憑證鏈＋nonce binding → 憑證 `serialNumber`（員編）∈ `.env` 白名單 `ADMIN_CARD_SERIALS`（CSV）才放行。參考包：`~/anila-card-login-export-20260708`（下稱 `SRC`；prod 驗證過，MANIFEST 明示供他專案重用）。**不得**弱化 CMS 驗簽/憑證鏈/nonce binding。
- **break-glass 帳密後備**：`ADMIN_USERNAME`/`ADMIN_PASSWORD` 僅在 `ENABLE_PASSWORD_FALLBACK=true` 時可用（預設 false）；值只存 gitignored `.env`（控制者已寫入，執行者**不得**印出、記錄或提交）；源碼與測試一律用假帳密（如 `u`/`p`）；比對用 `secrets.compare_digest`。
- **`CARD_DEV_SKIP_NONCE_BINDING` 僅限 E2E 臨時 override**（mock 簽章是寫死的，eContent 固定 `b"TBS"`），禁止寫入 `.env`、compose 或任何常駐設定。
- session 為記憶體 token + httponly cookie；登入成功寫 `changes.jsonl`（kind=login，含員編或 fallback 標記）。
- **前提**：GitHub repo 已於 2026-07-08 轉為 Private——憑證卡相關檔案（cardauth.py、CA bundle、含測試卡憑證的測試素材）**不得**存在於 Public repo；若未來需公開本 repo，須先移除這些檔案的歷史。
- 每個 task 結束 commit；訊息結尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- monitoring 端只動 Task 7 指定範圍；不動判定函式與既有 JS 契約選擇器（`.alerts-table tbody`、td class `ts`/`sev-*`、pill 文字格式 `CRITICAL {n}` 等）。

## 檔案結構

```
admin_console/
  Dockerfile
  requirements.txt
  service/__init__.py
  service/app.py          # FastAPI 路由 + 依賴組裝（create_app 可注入替身）
  admincore/__init__.py
  admincore/cardauth.py   # 憑證卡 CMS 驗簽核心（自 ANILA 參考包移植）
  admincore/cspki_ca_bundle.pem  # CSPKI 信任錨（公開 CA bundle）
  admincore/challenge_store.py   # challenge nonce 一次性存放
  admincore/envstore.py   # SETTINGS 白名單 + EnvStore（讀/驗證/原地寫/備份）
  admincore/jobs.py       # JobManager（單一併發、jobs.jsonl、changes.jsonl）
  admincore/dockerops.py  # DockerOps（exec 串流、restart、effective_env）
  admincore/reports.py    # 報告列表摘要 + per-query flip 比對
  admincore/render.py     # 管理台 HTML 渲染
  tests/                  # 各模組測試 + app 整合測試
  data/.gitkeep           # jobs.jsonl / changes.jsonl / env-backups/（git-ignored）
```

---

### Task 1: envstore — 白名單 .env 讀改寫

**Files:**
- Create: `admin_console/admincore/__init__.py`（空檔）
- Create: `admin_console/admincore/envstore.py`
- Create: `admin_console/tests/__init__.py`（空檔）
- Test: `admin_console/tests/test_envstore.py`

**Interfaces:**
- Produces: `SETTINGS: list[dict]`（每項含 `key,type,label,restart` 與型別相依欄位 `min/max/options/reindex`）、`WHITELIST: set[str]`、`SettingError(ValueError)`、`validate(key: str, value: str) -> str`（回正規化值或 raise）、`EnvStore(env_path: Path, backup_dir: Path)` 具 `read() -> dict[str,str]`（僅白名單鍵）與 `apply(updates: dict[str,str]) -> list[tuple[str, str|None, str]]`（回 `(key, old, new)` 已變更清單；無變更回空 list 且不寫檔不備份）。

- [ ] **Step 1: 寫失敗測試**

`admin_console/tests/test_envstore.py`：

```python
from pathlib import Path

import pytest

from admincore.envstore import EnvStore, SettingError, SETTINGS, WHITELIST, validate

SAMPLE = """# 由舊容器 env 重建
CHAT_MODEL_NAME=gpt-oss-20b
TOP_K=5
LLM_API_KEY=secret-do-not-touch
# 註解要活著
RAG_LOG_LEVEL=INFO
"""


@pytest.fixture()
def store(tmp_path):
    env = tmp_path / ".env"
    env.write_text(SAMPLE, encoding="utf-8")
    return EnvStore(env, tmp_path / "backups"), env


def test_whitelist_has_exactly_ten_keys():
    assert len(SETTINGS) == 10 and len(WHITELIST) == 10
    assert "LLM_API_KEY" not in WHITELIST and "EMBED_API_KEY" not in WHITELIST


def test_read_returns_only_whitelist(store):
    s, _ = store
    d = s.read()
    assert d["TOP_K"] == "5" and d["CHAT_MODEL_NAME"] == "gpt-oss-20b"
    assert "LLM_API_KEY" not in d
    assert d["RERANK_TOP_N"] is None  # .env 沒寫的白名單鍵回 None


def test_apply_preserves_comments_and_foreign_keys(store):
    s, env = store
    changes = s.apply({"TOP_K": "8", "RERANK_TOP_N": "10"})
    text = env.read_text(encoding="utf-8")
    assert ("TOP_K", "5", "8") in changes
    assert ("RERANK_TOP_N", None, "10") in changes
    assert "TOP_K=8" in text and "RERANK_TOP_N=10" in text
    assert "LLM_API_KEY=secret-do-not-touch" in text
    assert "# 註解要活著" in text


def test_apply_writes_in_place_same_inode(store):
    s, env = store
    ino = env.stat().st_ino
    s.apply({"TOP_K": "9"})
    assert env.stat().st_ino == ino  # 單檔 bind mount：換 inode 容器就看不到


def test_apply_creates_timestamped_backup(store):
    s, env = store
    s.apply({"TOP_K": "7"})
    baks = list((env.parent / "backups").glob("env-*.bak"))
    assert len(baks) == 1 and "TOP_K=5" in baks[0].read_text(encoding="utf-8")


def test_apply_noop_writes_nothing(store):
    s, env = store
    assert s.apply({"TOP_K": "5"}) == []
    assert not list((env.parent / "backups").glob("*.bak"))


def test_validate_rejects_bad_values():
    with pytest.raises(SettingError):
        validate("TOP_K", "abc")
    with pytest.raises(SettingError):
        validate("TOP_K", "0")          # min 1
    with pytest.raises(SettingError):
        validate("REASONING_EFFORT", "max")   # 不在 options
    with pytest.raises(SettingError):
        validate("LLM_API_KEY", "x")    # 非白名單
    assert validate("REACT_MODE", "true") == "true"
    assert validate("TOP_K", " 8 ") == "8"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd admin_console && python3 -m pytest tests/test_envstore.py -q`
Expected: FAIL（ModuleNotFoundError: admincore）

- [ ] **Step 3: 實作 `admincore/envstore.py`**

```python
"""管理台設定儲存層：白名單鍵的 .env 讀改寫。

值域治理走 ISO 稽核 Excel 表單（外部流程）；本層只做型別/範圍防呆，
金鑰與連線 URL 類一律不在白名單。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

SETTINGS: list[dict] = [
    {"key": "CHAT_MODEL_NAME", "type": "str", "label": "聊天模型名稱", "restart": True},
    {"key": "TOP_K", "type": "int", "min": 1, "max": 50, "label": "檢索 Top-K", "restart": True},
    {"key": "RERANK_TOP_N", "type": "int", "min": 1, "max": 100, "label": "重排 Top-N", "restart": True},
    {"key": "REASONING_EFFORT", "type": "enum", "options": ["low", "medium", "high"],
     "label": "推理力度", "restart": True},
    {"key": "REACT_MODE", "type": "enum", "options": ["false", "true"],
     "label": "ReAct 代理模式", "restart": True},
    {"key": "CHUNK_SIZE", "type": "int", "min": 100, "max": 4000,
     "label": "切塊大小", "restart": True, "reindex": True},
    {"key": "MAX_RETRIEVAL_TOKENS", "type": "int", "min": 500, "max": 32000,
     "label": "檢索 token 上限", "restart": True},
    {"key": "RATE_LIMIT_PER_MINUTE", "type": "int", "min": 1, "max": 10000,
     "label": "每分鐘速率上限", "restart": True},
    {"key": "RAG_LOG_LEVEL", "type": "enum", "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
     "label": "日誌等級", "restart": True},
    {"key": "RAG_LOG_VERBOSE", "type": "enum", "options": ["0", "1"],
     "label": "詳細日誌", "restart": True},
]
WHITELIST: set[str] = {s["key"] for s in SETTINGS}
_BY_KEY = {s["key"]: s for s in SETTINGS}


class SettingError(ValueError):
    """設定鍵不在白名單，或值不符型別/範圍。"""


def validate(key: str, value: str) -> str:
    spec = _BY_KEY.get(key)
    if spec is None:
        raise SettingError(f"{key} 不在可管理的白名單內")
    v = str(value).strip()
    if spec["type"] == "int":
        if not re.fullmatch(r"-?\d+", v):
            raise SettingError(f"{key} 需為整數，收到 {value!r}")
        n = int(v)
        if not (spec["min"] <= n <= spec["max"]):
            raise SettingError(f"{key} 需介於 {spec['min']}–{spec['max']}，收到 {n}")
        return str(n)
    if spec["type"] == "enum":
        if v not in spec["options"]:
            raise SettingError(f"{key} 只能是 {spec['options']}，收到 {value!r}")
        return v
    if not v:
        raise SettingError(f"{key} 不可為空")
    return v


_LINE_RE = re.compile(r"^([A-Z_0-9]+)=(.*)$")


class EnvStore:
    def __init__(self, env_path: Path, backup_dir: Path):
        self.env_path = Path(env_path)
        self.backup_dir = Path(backup_dir)

    def _lines(self) -> list[str]:
        return self.env_path.read_text(encoding="utf-8").splitlines()

    def read(self) -> dict[str, str | None]:
        found: dict[str, str | None] = {k: None for k in WHITELIST}
        for line in self._lines():
            m = _LINE_RE.match(line)
            if m and m.group(1) in WHITELIST:
                found[m.group(1)] = m.group(2)
        return found

    def apply(self, updates: dict[str, str]) -> list[tuple[str, str | None, str]]:
        current = self.read()
        todo: dict[str, str] = {}
        for k, v in updates.items():
            nv = validate(k, v)
            if current.get(k) != nv:
                todo[k] = nv
        if not todo:
            return []

        original = self.env_path.read_text(encoding="utf-8")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (self.backup_dir / f"env-{stamp}.bak").write_text(original, encoding="utf-8")

        lines = original.splitlines()
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            m = _LINE_RE.match(line)
            if m and m.group(1) in todo:
                out.append(f"{m.group(1)}={todo[m.group(1)]}")
                seen.add(m.group(1))
            else:
                out.append(line)
        for k in sorted(set(todo) - seen):
            out.append(f"{k}={todo[k]}")
        # 原地覆寫（同 inode）：.env 是單檔 bind mount，rename 會讓容器看不到新值
        self.env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return [(k, current.get(k), v) for k, v in sorted(todo.items())]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd admin_console && python3 -m pytest tests/test_envstore.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add admin_console/admincore admin_console/tests
git commit -m "feat(admin): envstore 白名單 .env 讀改寫（原地覆寫+時間戳備份）"
```

---

### Task 2: jobs — 單一併發 Job 管理與留痕

**Files:**
- Create: `admin_console/admincore/jobs.py`
- Test: `admin_console/tests/test_jobs.py`

**Interfaces:**
- Consumes: 無（runner 為注入的 callable）。
- Produces: `JobBusy(RuntimeError)`、`JobManager(data_dir: Path, runner)`：
  - `runner` 簽名 `runner(container: str, cmd: list[str]) -> tuple[Iterator[str], Callable[[], int]]`（行迭代器、取得 exit code 的函式）——Task 3 的 `DockerOps.exec_stream` 同簽名。
  - `start(name: str, container: str, cmd: list[str], meta: dict | None = None) -> dict`（回 job 快照；已有 running → raise JobBusy）
  - `current() -> dict | None`（最近一個 job 的快照：`id,name,container,cmd,state,started_at,ended_at,exit_code,tail`；state ∈ `running|done|failed`）
  - `log_change(entry: dict) -> None`（追加 `changes.jsonl`，自動補 `ts`）
  - `wait(timeout: float = 30) -> None`（等當前 job 結束；測試用）

- [ ] **Step 1: 寫失敗測試**

`admin_console/tests/test_jobs.py`：

```python
import json
import time
from pathlib import Path

import pytest

from admincore.jobs import JobBusy, JobManager


def fake_runner_ok(container, cmd):
    def lines():
        yield "step 1"
        yield "step 2"
    return lines(), lambda: 0


def fake_runner_fail(container, cmd):
    def lines():
        yield "boom"
    return lines(), lambda: 2


def slow_runner(container, cmd):
    def lines():
        yield "working"
        time.sleep(0.3)
    return lines(), lambda: 0


def test_job_success_records_jsonl(tmp_path):
    jm = JobManager(tmp_path, fake_runner_ok)
    job = jm.start("extended_vv", "ISO42001_monitoring", ["python3", "x.py"])
    assert job["state"] == "running"
    jm.wait()
    cur = jm.current()
    assert cur["state"] == "done" and cur["exit_code"] == 0
    assert cur["tail"][-1] == "step 2"
    rec = [json.loads(l) for l in (tmp_path / "jobs.jsonl").read_text().splitlines()]
    assert rec[-1]["name"] == "extended_vv" and rec[-1]["state"] == "done"


def test_job_failure_state(tmp_path):
    jm = JobManager(tmp_path, fake_runner_fail)
    jm.start("ragas", "ISO42001_monitoring", ["python3", "y.py"])
    jm.wait()
    assert jm.current()["state"] == "failed"
    assert jm.current()["exit_code"] == 2


def test_single_flight(tmp_path):
    jm = JobManager(tmp_path, slow_runner)
    jm.start("online_vv", "ISO42001_monitoring", ["python3", "z.py"])
    with pytest.raises(JobBusy):
        jm.start("ragas", "ISO42001_monitoring", ["python3", "y.py"])
    jm.wait()
    jm.start("ragas", "ISO42001_monitoring", ["python3", "y.py"])  # 結束後可再跑
    jm.wait()


def test_log_change_appends(tmp_path):
    jm = JobManager(tmp_path, fake_runner_ok)
    jm.log_change({"kind": "setting", "key": "TOP_K", "old": "5", "new": "8"})
    line = json.loads((tmp_path / "changes.jsonl").read_text().splitlines()[0])
    assert line["key"] == "TOP_K" and "ts" in line
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd admin_console && python3 -m pytest tests/test_jobs.py -q`
Expected: FAIL（jobs 模組不存在）

- [ ] **Step 3: 實作 `admincore/jobs.py`**

```python
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
        self.log_change({"kind": "job", "name": name, "container": container, "cmd": cmd})
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.current()

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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd admin_console && python3 -m pytest tests/test_jobs.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add admin_console/admincore/jobs.py admin_console/tests/test_jobs.py
git commit -m "feat(admin): JobManager 單一併發 job 與 changes/jobs 留痕"
```

---

### Task 3: dockerops — docker SDK 包裝

**Files:**
- Create: `admin_console/admincore/dockerops.py`
- Test: `admin_console/tests/test_dockerops.py`

**Interfaces:**
- Produces: `DockerOps(client=None)`：
  - `exec_stream(container: str, cmd: list[str]) -> tuple[Iterator[str], Callable[[], int]]`（與 JobManager runner 同簽名）
  - `restart(container: str, timeout: int = 30) -> None`
  - `effective_env(container: str) -> dict[str, str]`（讀 `Config.Env`）
  - `container_state(container: str) -> str`（`running`/`exited`/...；不存在回 `absent`）
- 延遲 import：`docker` 套件只在 `client=None` 時於建構子內 import（宿主測試注入替身即可，不需安裝 docker SDK）。

- [ ] **Step 1: 寫失敗測試**

`admin_console/tests/test_dockerops.py`：

```python
import pytest

from admincore.dockerops import DockerOps


class FakeAPI:
    def __init__(self):
        self.created = []

    def exec_create(self, container, cmd):
        self.created.append((container, cmd))
        return {"Id": "exec123"}

    def exec_start(self, exec_id, stream=True):
        assert exec_id == "exec123"
        return iter([b"line one\nline ", b"two\n"])

    def exec_inspect(self, exec_id):
        return {"ExitCode": 0}


class FakeContainer:
    def __init__(self):
        self.attrs = {"Config": {"Env": ["TOP_K=5", "LLM_API_KEY=zzz"]},
                      "State": {"Status": "running"}}
        self.status = "running"
        self.restarted = False

    def restart(self, timeout=30):
        self.restarted = True


class FakeContainers:
    def __init__(self, container):
        self._c = container

    def get(self, name):
        if name == "missing":
            raise KeyError(name)
        return self._c


class FakeClient:
    def __init__(self):
        self.api = FakeAPI()
        self._container = FakeContainer()
        self.containers = FakeContainers(self._container)


def test_exec_stream_lines_and_exit():
    ops = DockerOps(client=FakeClient())
    lines, wait_exit = ops.exec_stream("ISO42001_monitoring", ["python3", "x.py"])
    assert list(lines) == ["line one", "line two"]
    assert wait_exit() == 0


def test_restart_and_effective_env():
    c = FakeClient()
    ops = DockerOps(client=c)
    ops.restart("ISO42001_rag_api")
    assert c._container.restarted
    env = ops.effective_env("ISO42001_rag_api")
    assert env["TOP_K"] == "5" and env["LLM_API_KEY"] == "zzz"


def test_container_state_absent():
    ops = DockerOps(client=FakeClient())
    assert ops.container_state("missing") == "absent"
    assert ops.container_state("ISO42001_rag_api") == "running"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd admin_console && python3 -m pytest tests/test_dockerops.py -q`
Expected: FAIL（dockerops 不存在）

- [ ] **Step 3: 實作 `admincore/dockerops.py`**

```python
"""docker SDK 薄包裝：exec 串流、重啟、生效 env。client 可注入以利測試。"""
from __future__ import annotations

from typing import Callable, Iterator


class DockerOps:
    def __init__(self, client=None):
        if client is None:
            import docker  # 延遲 import：宿主測試不需安裝 docker SDK
            client = docker.from_env()
        self._c = client

    def exec_stream(self, container: str, cmd: list[str]) -> tuple[Iterator[str], Callable[[], int]]:
        api = self._c.api
        ex = api.exec_create(container, cmd)
        raw = api.exec_start(ex["Id"], stream=True)

        def lines() -> Iterator[str]:
            buf = b""
            for chunk in raw:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    yield line.decode("utf-8", errors="replace")
            if buf:
                yield buf.decode("utf-8", errors="replace")

        def wait_exit() -> int:
            code = api.exec_inspect(ex["Id"]).get("ExitCode")
            return int(code) if code is not None else -1

        return lines(), wait_exit

    def restart(self, container: str, timeout: int = 30) -> None:
        self._c.containers.get(container).restart(timeout=timeout)

    def effective_env(self, container: str) -> dict[str, str]:
        env_list = self._c.containers.get(container).attrs["Config"]["Env"] or []
        out: dict[str, str] = {}
        for item in env_list:
            k, _, v = item.partition("=")
            out[k] = v
        return out

    def container_state(self, container: str) -> str:
        try:
            return self._c.containers.get(container).status
        except Exception:
            return "absent"
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd admin_console && python3 -m pytest tests/test_dockerops.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add admin_console/admincore/dockerops.py admin_console/tests/test_dockerops.py
git commit -m "feat(admin): DockerOps exec 串流/重啟/生效 env（可注入 client）"
```

---

### Task 4: reports — 報告列表與 per-query flip 比對

**Files:**
- Create: `admin_console/admincore/reports.py`
- Test: `admin_console/tests/test_reports.py`

**Interfaces:**
- Produces:
  - `list_reports(reports_dir: Path) -> list[dict]`：新→舊排序，每項 `{"file": str, "kind": "vv"|"ragas"|"other", "generated_at": str, "hit_rate": float|None, "n": int|None}`；壞 JSON 略過不炸。
  - `flip_compare(base_path: Path, cur_path: Path) -> dict`：`{"newly_failed": [...], "newly_passed": [...], "still_failed": [...], "base_n": int, "cur_n": int}`，各清單元素 `{"id": str, "query": str}`。
- per_query 記錄欄位（run_online_vv 產出）：`id`、`query`、`hit_rate`（0.0/1.0）、`expected_articles`、`cited_articles`。hit 判定：`hit_rate == 1.0` 或（無 hit_rate 時）`hit is True`。

- [ ] **Step 1: 寫失敗測試**

`admin_console/tests/test_reports.py`：

```python
import json
from pathlib import Path

from admincore.reports import flip_compare, list_reports


def _write(dirp: Path, name: str, per_query, hit_rate=0.9, when="2026-07-08T10:00:00"):
    dirp.mkdir(parents=True, exist_ok=True)
    (dirp / name).write_text(json.dumps({
        "generated_at": when, "hit_rate": hit_rate, "per_query": per_query,
    }, ensure_ascii=False), encoding="utf-8")


def _pq(qid, hit):
    return {"id": qid, "query": f"問題{qid}", "hit_rate": 1.0 if hit else 0.0,
            "expected_articles": [], "cited_articles": []}


def test_list_reports_sorted_and_tolerant(tmp_path):
    _write(tmp_path, "vv_report_2026-07-01.json", [_pq("q1", True)], when="2026-07-01T09:00:00")
    _write(tmp_path, "vv_report_2026-07-08.json", [_pq("q1", True)], when="2026-07-08T09:00:00")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    rows = list_reports(tmp_path)
    assert [r["file"] for r in rows] == ["vv_report_2026-07-08.json", "vv_report_2026-07-01.json"]
    assert rows[0]["kind"] == "vv" and rows[0]["n"] == 1 and rows[0]["hit_rate"] == 0.9


def test_flip_compare(tmp_path):
    _write(tmp_path, "base.json", [_pq("q1", True), _pq("q2", True), _pq("q3", False)])
    _write(tmp_path, "cur.json", [_pq("q1", True), _pq("q2", False), _pq("q3", False), _pq("q4", True)])
    out = flip_compare(tmp_path / "base.json", tmp_path / "cur.json")
    assert [x["id"] for x in out["newly_failed"]] == ["q2"]
    assert out["newly_passed"] == []
    assert [x["id"] for x in out["still_failed"]] == ["q3"]
    assert out["base_n"] == 3 and out["cur_n"] == 4
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd admin_console && python3 -m pytest tests/test_reports.py -q`
Expected: FAIL

- [ ] **Step 3: 實作 `admincore/reports.py`**

```python
"""評估報告列表摘要與 per-query flip 比對（對應 monitoring 的比較方法）。"""
from __future__ import annotations

import json
from pathlib import Path


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _kind(name: str) -> str:
    if name.startswith("vv_report"):
        return "vv"
    if "ragas" in name:
        return "ragas"
    return "other"


def list_reports(reports_dir: Path) -> list[dict]:
    reports_dir = Path(reports_dir)
    rows: list[dict] = []
    if not reports_dir.is_dir():
        return rows
    for p in reports_dir.glob("*.json"):
        d = _load(p)
        if d is None:
            continue
        pq = d.get("per_query") or []
        rows.append({
            "file": p.name,
            "kind": _kind(p.name),
            "generated_at": str(d.get("generated_at", "")),
            "hit_rate": d.get("hit_rate"),
            "n": len(pq) if pq else None,
        })
    rows.sort(key=lambda r: r["generated_at"], reverse=True)
    return rows


def _hit(rec: dict) -> bool:
    if "hit_rate" in rec:
        return rec["hit_rate"] == 1.0
    return bool(rec.get("hit"))


def _index(path: Path) -> dict[str, dict]:
    d = _load(path) or {}
    return {str(r.get("id")): r for r in d.get("per_query") or []}


def flip_compare(base_path: Path, cur_path: Path) -> dict:
    base, cur = _index(Path(base_path)), _index(Path(cur_path))
    def brief(r: dict) -> dict:
        return {"id": str(r.get("id")), "query": str(r.get("query", ""))[:80]}
    newly_failed = [brief(cur[q]) for q in sorted(cur) if q in base and _hit(base[q]) and not _hit(cur[q])]
    newly_passed = [brief(cur[q]) for q in sorted(cur) if q in base and not _hit(base[q]) and _hit(cur[q])]
    still_failed = [brief(cur[q]) for q in sorted(cur) if q in base and not _hit(base[q]) and not _hit(cur[q])]
    return {"newly_failed": newly_failed, "newly_passed": newly_passed,
            "still_failed": still_failed, "base_n": len(base), "cur_n": len(cur)}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd admin_console && python3 -m pytest tests/test_reports.py -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add admin_console/admincore/reports.py admin_console/tests/test_reports.py
git commit -m "feat(admin): 報告列表摘要與 per-query flip 比對"
```

---

### Task 5: cardauth — 憑證卡驗簽核心移植

**Files:**
- Create: `admin_console/admincore/cardauth.py`（自 `SRC/services/csp/app/services/card_auth.py` 移植；`SRC` = `/home/c1147259/anila-card-login-export-20260708`）
- Create: `admin_console/admincore/cspki_ca_bundle.pem`（自 `SRC/services/csp/app/services/cspki_ca_bundle.pem` 原樣複製——公開 CA bundle，非私鑰）
- Create: `admin_console/admincore/challenge_store.py`（新寫）
- Test: `admin_console/tests/test_cardauth.py`（自 `SRC/services/csp/tests/test_card_auth.py` 移植聚焦測試）
- Test: `admin_console/tests/test_challenge_store.py`

**Interfaces:**
- Produces: `cardauth.verify_pkcs7_signature(...)` 與其 claims/例外類別——**先 Read 原始檔確認實際簽名、`CardClaims` 欄位（員編欄位預期為 `employee_id`，來自憑證 subject `serialNumber`）與例外類別名，原樣保留**，只做下列適配：
  1. 移除對 ANILA `app.config`/其他 app 模組的 import（若有）；CA bundle 路徑改為 env `CARD_CA_BUNDLE_PATH`，預設 `Path(__file__).parent / "cspki_ca_bundle.pem"`。
  2. `CARD_DEV_SKIP_NONCE_BINDING` 語意原樣保留（dev only；參考包安全注意事項）。
  3. **禁止**弱化或省略 CMS 驗簽、憑證鏈驗證、nonce binding。
- Produces: `ChallengeStore(ttl_sec=120)`：`issue() -> (challenge_token, nonce)`、`consume(token) -> nonce | None`（一次性、過期即無效）。取代 ANILA 的 challenge JWT——單機管理台不需 JWT，省 python-jose 依賴。
- 宿主測試依賴：`cryptography` 與 `asn1crypto`（anaconda 若缺後者：`pip install --user asn1crypto==1.5.1`）。

- [ ] **Step 1: 移植失敗測試**

複製 `SRC/services/csp/tests/test_card_auth.py` → `admin_console/tests/test_cardauth.py`，只改 import 路徑（`admincore.cardauth`）與測試素材路徑引用；測試素材＝mock 寫死的 PKCS#7 簽章（鄒惠翔測試卡，eContent 固定 `b"TBS"`），nonce-binding 與 skip 開關測試照搬。另建 `admin_console/tests/test_challenge_store.py`：

```python
import time

from admincore.challenge_store import ChallengeStore


def test_issue_and_consume_once():
    cs = ChallengeStore()
    token, nonce = cs.issue()
    assert cs.consume(token) == nonce
    assert cs.consume(token) is None          # 一次性（反 replay）


def test_unknown_token():
    assert ChallengeStore().consume("nope") is None


def test_expiry():
    cs = ChallengeStore(ttl_sec=0)
    token, _ = cs.issue()
    time.sleep(0.01)
    assert cs.consume(token) is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd admin_console && python3 -m pytest tests/test_cardauth.py tests/test_challenge_store.py -q`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 移植 `cardauth.py` 與複製 CA bundle**

依 Interfaces 的三條適配規則移植；`cp SRC/.../cspki_ca_bundle.pem admin_console/admincore/`。

- [ ] **Step 4: 實作 `challenge_store.py`**

```python
"""challenge nonce 記憶體存放：取代 ANILA 的 challenge JWT（單機管理台不需 JWT）。"""
from __future__ import annotations

import secrets
import threading
import time


class ChallengeStore:
    def __init__(self, ttl_sec: int = 120):
        self._ttl = ttl_sec
        self._lock = threading.Lock()
        self._items: dict[str, tuple[str, float]] = {}

    def issue(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(24)
        nonce = secrets.token_hex(16)
        with self._lock:
            now = time.monotonic()
            self._items = {k: v for k, v in self._items.items() if v[1] > now}
            self._items[token] = (nonce, now + self._ttl)
        return token, nonce

    def consume(self, token: str) -> str | None:
        """一次性取出（反 replay）；過期或不存在回 None。"""
        with self._lock:
            item = self._items.pop(token, None)
        if item is None or item[1] <= time.monotonic():
            return None
        return item[0]
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd admin_console && python3 -m pytest tests/test_cardauth.py tests/test_challenge_store.py -q`
Expected: 全部 PASS（cardauth 移植測試數以原檔為準）

- [ ] **Step 6: Commit**

```bash
git add admin_console/admincore/cardauth.py admin_console/admincore/cspki_ca_bundle.pem admin_console/admincore/challenge_store.py admin_console/tests/test_cardauth.py admin_console/tests/test_challenge_store.py
git commit -m "feat(admin): 移植中科院憑證卡 CMS 驗簽核心與 challenge store"
```

---

### Task 6: render + app — 頁面與 FastAPI 路由

**Files:**
- Create: `admin_console/admincore/render.py`
- Create: `admin_console/service/__init__.py`（空檔）
- Create: `admin_console/service/app.py`
- Test: `admin_console/tests/test_render.py`
- Test: `admin_console/tests/test_app.py`

**Interfaces:**
- Consumes: Task 1 `SETTINGS/EnvStore/SettingError`、Task 2 `JobManager/JobBusy`、Task 3 `DockerOps`、Task 4 `list_reports/flip_compare`、Task 5 `cardauth.verify_pkcs7_signature`/`ChallengeStore`。
- Produces:
  - `render.render_admin_page(ctx: dict) -> str`。`ctx` 鍵：`settings_rows: list[dict]`（每項 `{spec, env_value, effective_value, dirty}`）、`job: dict|None`、`reports: list[dict]`、`rag_state: str`、`monitoring_state: str`、`smtp_enabled: bool|None`、`saved: bool`、`error: str|None`。
  - `render.render_login_page(password_fallback: bool = False, error: str | None = None) -> str`：登入頁。主體＝「插卡登入」按鈕與狀態區；`password_fallback=True` 時才渲染帳密表單（POST `login`）。頁內嵌 JS 實作 HiPKI popup 協議——**自 `SRC/apps/csp-governance-ui/src/api/caAuth.js` 移植** popup（`http://localhost:16888/popupForm`）、postMessage、tbsPackage/PIN 流程，整合點固定為：(1) `GET api/auth/card/challenge` → `{"challenge_token": str, "nonce": str}`；(2) popup 簽 nonce 回 base64 PKCS#7；(3) 以隱藏 form POST `api/auth/card/verify`（欄位 `challenge_token`、`signed_data`）→ 成功 303 `/`、失敗回登入頁帶錯誤。錯誤文案：驗簽失敗「憑證卡驗證失敗」；員編不在白名單「此卡員編 {employee_id} 不在管理台白名單——請將其加入 .env 的 ADMIN_CARD_SERIALS 後重試」（employee_id 為操作者本人資訊，可顯示）。
  - `app.create_app(env_store, job_manager, dockerops, reports_dir: Path, monitoring_url: str, rag_api_url: str, card_serials: set[str], admin_user: str, admin_password: str, password_fallback: bool, verify_card=None, http_post=None, http_get=None) -> FastAPI`；`verify_card(signed_b64: str, expected_nonce: str) -> CardClaims` 可注入（預設包裝 Task 5 的 `cardauth.verify_pkcs7_signature`；測試注入替身，不需真簽章）。模組層 `app = create_app_from_env()`：讀 `ADMIN_CARD_SERIALS`（CSV→set）、`ENABLE_PASSWORD_FALLBACK`、`ADMIN_USERNAME`/`ADMIN_PASSWORD`；若白名單為空且 fallback 未開 → raise RuntimeError（設定錯誤要大聲）。
  - **登入保護（middleware）**：除 `GET/POST /login`、`GET /api/auth/card/challenge`、`POST /api/auth/card/verify` 外一律需已登入 session cookie（`admin_session`）；未登入時 `/api/*` 回 401 JSON、頁面路徑 303 導向 `/login`。
  - **卡片登入路由**：`GET /api/auth/card/challenge` → `ChallengeStore.issue()`；`POST /api/auth/card/verify`（form）→ `consume(challenge_token)` 取 nonce（None→401）→ `verify_card(signed_data, nonce)`（例外→登入頁「憑證卡驗證失敗」）→ claims 員編 ∈ `card_serials` 否則登入頁帶白名單提示 → 成功建 session、`log_change({"kind": "login", "method": "card", "employee_id": ...})`、303 `/`。
  - **帳密 fallback**：`POST /login` 僅在 `password_fallback=True` 時受理（否則 403）；`secrets.compare_digest` 逐一比對（兩者都比，不短路），成功→session＋`log_change({"kind": "login", "method": "password_fallback"})`、303 `/`；失敗→`time.sleep(0.5)` 後回登入頁「帳號或密碼錯誤」。`POST /logout` 清 session + cookie、303 `/login`。
  - JOB 目錄（app.py 內常數）：

```python
CONTAINER_REPORTS = "/app/data/reports"  # monitoring 容器內報告路徑
JOB_CATALOG = {
    "extended_vv":  {"container": "ISO42001_monitoring", "cmd": ["python3", "scripts/run_extended_vv.py"]},
    "online_vv":    {"container": "ISO42001_monitoring", "cmd": ["python3", "scripts/run_online_vv.py"]},
    "ragas":        {"container": "ISO42001_monitoring", "cmd": ["python3", "scripts/run_ragas_evaluation.py"]},
    "regression_gate": {"container": "ISO42001_monitoring",
                        "params": ["baseline", "current", "tag"]},   # cmd 由 params 組
    "attribution":  {"container": "ISO42001_monitoring", "params": ["vv_report"]},
    "reindex_full": {"container": "ISO42001_rag_api", "cmd": ["python3", "scripts/reindex.py"]},
    "version_snapshot": {"container": "ISO42001_rag_api",
                         "params": ["message", "operator", "version"]},
}
```

  - 路由：
    - `GET /` → 管理台頁（組 ctx → render）
    - `POST /api/settings`（form）→ validate+apply+log_change → 303 redirect `/?saved=1`；SettingError → 303 `/?error=<msg urlencoded>`
    - `POST /api/restart` → dockerops.restart("ISO42001_rag_api") + log_change → `{"ok": true}`
    - `GET /api/rag-health` → httpx GET `{rag_api_url}/health`（2 秒 timeout）→ `{"ok": bool}`
    - `POST /api/jobs/{name}`（form 帶 params）→ 組 cmd → job_manager.start → job 快照；JobBusy → 409；未知 name → 404；params 檔名含 `/` 或不存在於 reports_dir → 400
    - `GET /api/jobs/current` → job 快照或 `{"state": "idle"}`
    - `GET /api/reports` → list_reports
    - `GET /api/reports/compare?base=X&cur=Y` → flip_compare（同樣檔名驗證）
    - `POST /api/alert-test?severity=info|warning|critical` → httpx POST `{monitoring_url}/v1/alerts/test`
  - `http_post/http_get` 可注入（測試給替身；預設用 httpx，2 秒 timeout，失敗回 502 JSON 不炸）。兩者回傳 dict：`{"status_code": int, "ok": bool, "json": dict|None}`（`json` 為回應 body 解析結果，解析失敗為 None）。
  - SMTP 狀態顯示（spec 要求）：`index()` 以 `http_get(f"{monitoring_url}/v1/alerts/summary")` 取 `json.smtp_enabled`，放入 `ctx["smtp_enabled"]`（取不到為 None），meta 列顯示「SMTP：啟用/關閉/未知」。
  - 頁面表單 UX：`api/jobs/*`、`api/alert-test`、`api/reports/compare` 的表單由頁面 JS 攔截（fetch 提交、結果就地顯示），避免瀏覽器跳到 raw JSON；`api/settings` 表單維持原生 POST + 303 redirect。API 端行為（JSON 回應）不因此改變，curl/測試照舊。
  - regression_gate cmd 組法：`["python3", "scripts/run_regression_gate.py", "--baseline", f"{CONTAINER_REPORTS}/{baseline}", "--current", f"{CONTAINER_REPORTS}/{current}", "--tag", tag or "admin-ui"]`；attribution：`["python3", "scripts/run_attribution.py", "--vv-report", f"{CONTAINER_REPORTS}/{vv_report}"]`；version_snapshot：`["python3", "scripts/version_tracker.py", "snapshot", "-m", message, "-o", operator, "-v", version]`（空值以 `""` 傳）。

- [ ] **Step 1: 寫失敗測試（render）**

`admin_console/tests/test_render.py`：

```python
import re

from admincore.envstore import SETTINGS
from admincore.render import render_admin_page

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿️]")


def _ctx(**over):
    rows = [{"spec": s, "env_value": "5" if s["key"] == "TOP_K" else None,
             "effective_value": "3" if s["key"] == "TOP_K" else None,
             "dirty": s["key"] == "TOP_K"} for s in SETTINGS]
    ctx = {"settings_rows": rows, "job": None, "reports": [],
           "rag_state": "running", "monitoring_state": "running",
           "smtp_enabled": False, "saved": False, "error": None}
    ctx.update(over)
    return ctx


def test_page_structure_and_no_emoji():
    html = render_admin_page(_ctx())
    for frag in ("維運管理台", 'id="settings"', 'id="ops"', 'id="reports"',
                 'id="alerts-ops"', "已寫入，待重啟", "TOP_K",
                 'action="api/settings"', 'id="job-panel"',
                 'id="compare-result"', "SMTP：<b>關閉</b>"):
        assert frag in html, f"missing: {frag}"
    assert not _EMOJI_RE.search(html)
    assert "LLM_API_KEY" not in html          # 金鑰絕不出現


def test_running_job_rendered():
    html = render_admin_page(_ctx(job={"name": "ragas", "state": "running",
                                       "started_at": "t", "tail": ["a", "b"]}))
    assert "ragas" in html and "執行中" in html


def test_error_banner():
    html = render_admin_page(_ctx(error="TOP_K 需為整數"))
    assert "TOP_K 需為整數" in html


def test_login_page_card_primary():
    from admincore.render import render_login_page
    html = render_login_page()
    assert "插卡登入" in html and "api/auth/card/challenge" in html
    assert 'name="password"' not in html            # fallback 預設不渲染
    assert not _EMOJI_RE.search(html)


def test_login_page_with_fallback_and_error():
    from admincore.render import render_login_page
    html = render_login_page(password_fallback=True, error="帳號或密碼錯誤")
    assert 'name="username"' in html and 'name="password"' in html
    assert "帳號或密碼錯誤" in html
```

- [ ] **Step 2: 寫失敗測試（app）**

`admin_console/tests/test_app.py`：

```python
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from admincore.dockerops import DockerOps
from admincore.envstore import EnvStore
from admincore.jobs import JobManager
from service.app import create_app

from tests.test_dockerops import FakeClient
from tests.test_jobs import fake_runner_ok


@pytest.fixture()
def client(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOP_K=5\nLLM_API_KEY=zzz\n", encoding="utf-8")
    store = EnvStore(env, tmp_path / "backups")
    jm = JobManager(tmp_path / "data", fake_runner_ok)
    ops = DockerOps(client=FakeClient())
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "vv_report_a.json").write_text(json.dumps(
        {"generated_at": "2026-07-08", "hit_rate": 0.9,
         "per_query": [{"id": "q1", "query": "x", "hit_rate": 1.0}]}), encoding="utf-8")
    calls = {"post": [], "get": []}

    def fake_post(url, **kw):
        calls["post"].append((url, kw))
        return {"status_code": 200, "ok": True, "json": {}}

    def fake_get(url, **kw):
        calls["get"].append((url, kw))
        return {"status_code": 200, "ok": True, "json": {"smtp_enabled": False}}

    class FakeClaims:
        employee_id = "1090868"
        display_name = "測試卡"

    class FakeCardError(Exception):
        pass

    def fake_verify(signed_b64, expected_nonce):
        if signed_b64 == "BAD":
            raise FakeCardError("bad signature")
        if signed_b64 == "STRANGER":
            c2 = FakeClaims(); c2.employee_id = "9999999"
            return c2
        return FakeClaims()

    app = create_app(store, jm, ops, reports, "http://monitoring:8200",
                     "http://rag-api:8000",
                     card_serials={"1090868"},
                     admin_user="u", admin_password="p", password_fallback=True,
                     verify_card=fake_verify,
                     http_post=fake_post, http_get=fake_get)
    c = TestClient(app, follow_redirects=False)
    c._jm, c._env, c._calls, c._tmp = jm, env, calls, tmp_path
    # 預設以測試假帳密登入（fallback 開啟；真帳密只存在 .env，絕不進測試碼）
    r = c.post("/login", data={"username": "u", "password": "p"})
    assert r.status_code == 303
    return c


def _card_login(fresh_client):
    tok = fresh_client.get("/api/auth/card/challenge").json()["challenge_token"]
    return fresh_client.post("/api/auth/card/verify",
                             data={"challenge_token": tok, "signed_data": "GOOD"})


def test_login_required(client):
    fresh = TestClient(client.app, follow_redirects=False)
    assert fresh.get("/").status_code == 303
    assert fresh.get("/").headers["location"] == "/login"
    assert fresh.get("/api/jobs/current").status_code == 401
    assert fresh.get("/login").status_code == 200


def test_card_login_success(client):
    fresh = TestClient(client.app, follow_redirects=False)
    r = _card_login(fresh)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert fresh.get("/api/jobs/current").status_code == 200
    log = (client._tmp / "data" / "changes.jsonl").read_text()
    assert '"card"' in log and '"1090868"' in log


def test_card_login_bad_signature(client):
    fresh = TestClient(client.app, follow_redirects=False)
    tok = fresh.get("/api/auth/card/challenge").json()["challenge_token"]
    r = fresh.post("/api/auth/card/verify",
                   data={"challenge_token": tok, "signed_data": "BAD"})
    assert r.status_code == 200 and "憑證卡驗證失敗" in r.text
    assert fresh.get("/api/jobs/current").status_code == 401


def test_card_login_not_whitelisted_shows_serial(client):
    fresh = TestClient(client.app, follow_redirects=False)
    tok = fresh.get("/api/auth/card/challenge").json()["challenge_token"]
    r = fresh.post("/api/auth/card/verify",
                   data={"challenge_token": tok, "signed_data": "STRANGER"})
    assert r.status_code == 200 and "9999999" in r.text and "ADMIN_CARD_SERIALS" in r.text
    assert fresh.get("/api/jobs/current").status_code == 401


def test_card_challenge_token_single_use(client):
    fresh = TestClient(client.app, follow_redirects=False)
    tok = fresh.get("/api/auth/card/challenge").json()["challenge_token"]
    assert fresh.post("/api/auth/card/verify",
                      data={"challenge_token": tok, "signed_data": "GOOD"}).status_code == 303
    fresh2 = TestClient(client.app, follow_redirects=False)
    r = fresh2.post("/api/auth/card/verify",
                    data={"challenge_token": tok, "signed_data": "GOOD"})
    assert r.status_code == 401   # token 一次性（反 replay）


def test_login_wrong_password(client):
    fresh = TestClient(client.app, follow_redirects=False)
    r = fresh.post("/login", data={"username": "u", "password": "wrong"})
    assert r.status_code == 200 and "帳號或密碼錯誤" in r.text
    assert fresh.get("/api/jobs/current").status_code == 401


def test_password_fallback_disabled(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOP_K=5\n", encoding="utf-8")
    app2 = create_app(EnvStore(env, tmp_path / "b"),
                      JobManager(tmp_path / "d", fake_runner_ok),
                      DockerOps(client=FakeClient()), tmp_path,
                      "http://monitoring:8200", "http://rag-api:8000",
                      card_serials={"1090868"},
                      admin_user="u", admin_password="p", password_fallback=False,
                      verify_card=lambda s, n: None,
                      http_post=lambda u, **k: {"ok": True, "status_code": 200, "json": {}},
                      http_get=lambda u, **k: {"ok": True, "status_code": 200, "json": {}})
    fresh = TestClient(app2, follow_redirects=False)
    assert fresh.post("/login", data={"username": "u", "password": "p"}).status_code == 403


def test_logout(client):
    r = client.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/api/jobs/current").status_code == 401


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "維運管理台" in r.text


def test_settings_save_and_change_log(client):
    r = client.post("/api/settings", data={"TOP_K": "8"})
    assert r.status_code == 303 and r.headers["location"] == "/?saved=1"
    assert "TOP_K=8" in client._env.read_text()
    log = (client._tmp / "data" / "changes.jsonl").read_text().splitlines()
    assert any('"TOP_K"' in l for l in log)


def test_settings_invalid_redirects_with_error(client):
    r = client.post("/api/settings", data={"TOP_K": "abc"})
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert "TOP_K=5" in client._env.read_text()   # 沒寫入


def test_job_start_and_busy(client):
    r = client.post("/api/jobs/extended_vv")
    assert r.status_code == 200 and r.json()["state"] == "running"
    client._jm.wait()
    assert client.get("/api/jobs/current").json()["state"] == "done"


def test_job_bad_name_and_bad_param(client):
    assert client.post("/api/jobs/nope").status_code == 404
    r = client.post("/api/jobs/regression_gate",
                    data={"baseline": "../etc/passwd", "current": "vv_report_a.json", "tag": "t"})
    assert r.status_code == 400


def test_regression_gate_builds_container_paths(client):
    r = client.post("/api/jobs/regression_gate",
                    data={"baseline": "vv_report_a.json", "current": "vv_report_a.json", "tag": "t1"})
    assert r.status_code == 200
    cmd = r.json()["cmd"]
    assert "--baseline" in cmd and "/app/data/reports/vv_report_a.json" in cmd
    client._jm.wait()


def test_reports_and_compare(client):
    assert client.get("/api/reports").json()[0]["file"] == "vv_report_a.json"
    r = client.get("/api/reports/compare",
                   params={"base": "vv_report_a.json", "cur": "vv_report_a.json"})
    assert r.json()["newly_failed"] == []


def test_restart_and_alert_test(client):
    assert client.post("/api/restart").json()["ok"] is True
    assert client.post("/api/alert-test", params={"severity": "warning"}).json()["ok"] is True
    assert any("alerts/test" in u for u, _ in client._calls["post"])
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `cd admin_console && python3 -m pytest tests/test_render.py tests/test_app.py -q`
Expected: FAIL（render/service.app 不存在）

- [ ] **Step 4: 實作 `admincore/render.py`**

單一 `render_admin_page(ctx)`，報告書 token、四區塊、無 emoji。完整程式碼：

```python
"""維運管理台頁面渲染：單頁四區塊，沿用印刷報告書視覺 token。"""
from __future__ import annotations

from html import escape

from .envstore import SETTINGS

_CSS = """
:root { --ink:#1a1f2c; --muted:#5b6578; --line:#c9d1dc; --hairline:#e5eaf1;
        --paper:#fff; --soft:#f6f8fc; --accent:#1e3a8a;
        --mono:"JetBrains Mono","Consolas","Courier New",monospace; }
*,*::before,*::after { box-sizing:border-box; }
body { font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif;
       margin:0; background:var(--soft); color:var(--ink); font-size:15px; line-height:1.7; }
.report { max-width:1080px; margin:0 auto; padding:36px 44px 56px; background:var(--paper);
          border-left:1px solid var(--line); border-right:1px solid var(--line); min-height:100vh; }
h1 { font-size:23px; font-weight:900; margin:0; }
.title-en { font-size:13px; font-weight:500; color:var(--muted); margin-left:10px; }
.report-rule { border:0; border-top:2px solid var(--ink); margin:14px 0 0; }
.meta { display:flex; gap:24px; flex-wrap:wrap; font-size:12.5px; color:var(--muted);
        padding:10px 0; border-bottom:1px solid var(--hairline); }
.meta b { color:var(--ink); }
section { margin-top:32px; }
h2 { font-size:17px; font-weight:900; margin:0 0 10px; padding-bottom:6px;
     border-bottom:1px solid var(--ink); }
h2 .en { font-size:12px; font-weight:500; color:var(--muted); margin-left:8px; }
table { width:100%; border-collapse:collapse; font-size:13px; margin:8px 0 12px; }
th { text-align:left; padding:7px 10px; font-size:12px; font-weight:700;
     border-top:2px solid var(--ink); border-bottom:1px solid var(--ink); }
td { padding:6px 10px; border-bottom:1px solid var(--hairline); vertical-align:middle; }
input,select { padding:6px 8px; border:1px solid var(--line); background:#fff;
               font-family:inherit; font-size:13px; }
input.num { font-family:var(--mono); width:9em; }
button { padding:8px 14px; border:1px solid var(--accent); background:var(--accent);
         color:#fff; font-weight:800; font-size:13px; cursor:pointer; }
button.ghost { background:#fff; color:var(--accent); }
.num { font-family:var(--mono); font-variant-numeric:tabular-nums; }
.badge { display:inline-block; padding:1px 8px; font-size:11px; font-weight:800;
         border:1px solid currentColor; white-space:nowrap; }
.badge.dirty { color:#92400e; }
.badge.ok { color:#166534; }
.note { font-size:12px; color:var(--muted); }
.banner { padding:10px 14px; margin:14px 0; font-size:13px; font-weight:700;
          border:1px solid var(--line); }
.banner.err { color:#991b1b; border-color:#991b1b; }
.banner.ok { color:#166534; }
.jobbox { border:1px solid var(--line); padding:12px 16px; margin:10px 0; }
pre.tail { background:var(--soft); border:1px solid var(--hairline); padding:10px 12px;
           font-size:12px; font-family:var(--mono); max-height:260px; overflow:auto;
           white-space:pre-wrap; margin:8px 0 0; }
.oprow { display:flex; gap:10px; flex-wrap:wrap; align-items:end; margin:10px 0; }
.oprow label { font-size:12px; font-weight:700; color:var(--muted); display:block; }
.footer { margin-top:36px; padding-top:12px; border-top:2px solid var(--ink);
          font-size:11px; color:var(--muted); }
"""

_JS = """
function poll() {
  fetch('api/jobs/current').then(r => r.json()).then(j => {
    var box = document.getElementById('job-panel');
    if (!box) return;
    if (j.state === 'idle') { box.textContent = '目前沒有執行中的作業。'; return; }
    var tail = (j.tail || []).join('\\n');
    box.innerHTML = '';
    var head = document.createElement('div');
    head.textContent = '作業 ' + j.name + ' · 狀態 ' + (j.state === 'running' ? '執行中'
                     : j.state === 'done' ? '完成' : '失敗（exit ' + j.exit_code + '）')
                     + ' · 開始 ' + j.started_at;
    head.style.fontWeight = '800';
    var pre = document.createElement('pre');
    pre.className = 'tail';
    pre.textContent = tail;
    box.appendChild(head); box.appendChild(pre);
    if (j.state === 'running') setTimeout(poll, 2000);
  }).catch(function(){ setTimeout(poll, 4000); });
}
function restartRag() {
  if (!confirm('確定重啟 rag-api 套用設定？服務將中斷約半分鐘。')) return;
  var st = document.getElementById('rag-restart-state');
  st.textContent = '重啟中…';
  fetch('api/restart', {method:'POST'}).then(function(){
    var tries = 0;
    (function chk(){
      fetch('api/rag-health').then(r=>r.json()).then(function(h){
        if (h.ok) { st.textContent = 'rag-api 已恢復，生效值請重新整理頁面確認。'; }
        else if (++tries < 30) setTimeout(chk, 2000);
        else st.textContent = 'rag-api 60 秒未恢復，請查 docker logs ISO42001_rag_api';
      }).catch(function(){ if (++tries < 30) setTimeout(chk, 2000); });
    })();
  });
}
function hookForms() {
  document.querySelectorAll('form[data-ajax="1"]').forEach(function(f){
    f.addEventListener('submit', function(e){
      e.preventDefault();
      var url = f.getAttribute('action');
      if ((f.method || 'post').toUpperCase() === 'GET') {
        var q = new URLSearchParams(new FormData(f)).toString();
        fetch(url + '?' + q).then(function(r){ return r.json(); }).then(showCompare);
      } else {
        fetch(url, {method:'POST', body:new FormData(f)})
          .then(function(r){ return r.json(); })
          .then(function(j){ if (j.error) alert(j.error); poll(); });
      }
    });
  });
}
function showCompare(d) {
  var box = document.getElementById('compare-result');
  if (!box) return;
  if (d.error) { box.textContent = d.error; return; }
  function block(title, arr) {
    var s = title + '（' + arr.length + '）\\n';
    arr.forEach(function(x){ s += '  ' + x.id + ' ' + x.query + '\\n'; });
    return s;
  }
  box.textContent = block('newly_failed 新增失敗', d.newly_failed)
                  + block('newly_passed 新增通過', d.newly_passed)
                  + block('still_failed 持續失敗', d.still_failed)
                  + '基線題數 ' + d.base_n + ' · 本版題數 ' + d.cur_n;
}
document.addEventListener('DOMContentLoaded', function(){ poll(); hookForms(); });
"""


def _job_panel(job: dict | None) -> str:
    """job 面板的伺服器端初始渲染；之後由 JS poll 更新。"""
    if not job:
        return "目前沒有執行中的作業。"
    state_txt = {"running": "執行中", "done": "完成",
                 "failed": f"失敗（exit {job.get('exit_code')}）"}.get(
                     job.get("state"), str(job.get("state")))
    tail = escape("\n".join(job.get("tail") or []))
    return (f'<div style="font-weight:800;">作業 {escape(str(job.get("name", "")))} · '
            f'狀態 {escape(state_txt)} · 開始 {escape(str(job.get("started_at", "")))}</div>'
            f'<pre class="tail">{tail}</pre>')


def _settings_rows_html(rows: list[dict]) -> str:
    out = []
    for r in rows:
        s = r["spec"]
        key, typ = s["key"], s["type"]
        envv = r["env_value"] if r["env_value"] is not None else ""
        effv = r["effective_value"] if r["effective_value"] is not None else "—"
        if typ == "enum":
            opts = "".join(
                f'<option value="{escape(o)}" {"selected" if envv == o else ""}>{escape(o)}</option>'
                for o in s["options"])
            field = f'<select name="{escape(key)}"><option value="">（未設定）</option>{opts}</select>'
        else:
            cls = "num" if typ == "int" else ""
            field = (f'<input class="{cls}" name="{escape(key)}" value="{escape(str(envv))}" '
                     f'placeholder="（未設定）">')
        badge = ('<span class="badge dirty">已寫入，待重啟</span>' if r["dirty"]
                 else '<span class="badge ok">一致</span>')
        note = "改後需 reindex（見下方索引維護）" if s.get("reindex") else ""
        out.append(
            f'<tr><td><code>{escape(key)}</code><div class="note">{escape(s["label"])}'
            f'{("　" + note) if note else ""}</div></td>'
            f'<td>{field}</td>'
            f'<td class="num">{escape(str(effv))}</td>'
            f'<td>{badge}</td></tr>'
        )
    return "".join(out)


def _reports_options(reports: list[dict]) -> str:
    return "".join(f'<option value="{escape(r["file"])}">{escape(r["file"])}</option>'
                   for r in reports)


def _reports_table(reports: list[dict]) -> str:
    if not reports:
        return '<div class="note">尚無報告。跑一次 V&V 或 RAGAS 後會出現在這裡。</div>'
    rows = "".join(
        f'<tr><td><code>{escape(r["file"])}</code></td><td>{escape(r["kind"])}</td>'
        f'<td class="num">{escape(str(r["generated_at"]))}</td>'
        f'<td class="num">{r["hit_rate"] if r["hit_rate"] is not None else "—"}</td>'
        f'<td class="num">{r["n"] if r["n"] is not None else "—"}</td></tr>'
        for r in reports)
    return ('<table><thead><tr><th>檔名</th><th>類型</th><th>產生時間</th>'
            '<th>Hit Rate</th><th>題數</th></tr></thead><tbody>' + rows + '</tbody></table>')


def render_login_page(password_fallback: bool = False, error: str | None = None) -> str:
    err = f'<div class="banner err">{escape(error)}</div>' if error else ""
    pw_form = ""
    if password_fallback:
        pw_form = """
  <div class="note" style="margin-top:20px;border-top:1px solid var(--hairline);padding-top:12px;">
    break-glass 帳密登入（僅讀卡環境故障時使用）</div>
  <form method="post" action="login">
    <label>帳號</label><input name="username" autocomplete="username">
    <label>密碼</label><input name="password" type="password" autocomplete="current-password">
    <button type="submit" class="ghost">帳密登入</button>
  </form>"""
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head><meta charset="UTF-8"><title>ISO 42001 維運管理台 — 登入</title><style>{_CSS}
.login-box {{ max-width:400px; margin:12vh auto 0; border:1px solid var(--ink); padding:26px 30px; background:var(--paper); }}
.login-box label {{ font-size:12px; font-weight:700; color:var(--muted); display:block; margin-top:12px; }}
.login-box input {{ width:100%; }}
.login-box button {{ margin-top:16px; width:100%; }}
#card-status {{ font-size:12.5px; color:var(--muted); margin-top:10px; min-height:1.5em; }}
</style></head>
<body>
<div class="login-box">
  <h1 style="font-size:18px;">維運管理台<span class="title-en">Operations Console</span></h1>
  <hr class="report-rule">
  {err}
  <button id="card-login-btn" type="button" onclick="cardLogin()">插卡登入（中科院憑證卡）</button>
  <div id="card-status"></div>
  <form id="card-verify-form" method="post" action="api/auth/card/verify" style="display:none;">
    <input type="hidden" name="challenge_token"><input type="hidden" name="signed_data">
  </form>
  {pw_form}
</div>
<script>
/* HiPKI popup 協議：自 SRC/apps/csp-governance-ui/src/api/caAuth.js 移植
   （SRC=/home/c1147259/anila-card-login-export-20260708）。
   整合點固定三步——實作時保留 caAuth.js 的 popup 開啟、postMessage 監聽、
   tbsPackage 組裝與 PIN 處理邏輯，僅改端點與提交方式：
   1) fetch('api/auth/card/challenge') 取 challenge_token + nonce
   2) popup http://localhost:16888/popupForm 簽 nonce（cht mock 或真 HiPKI 元件）
   3) 把 challenge_token + 回傳的 base64 簽章填入 #card-verify-form 後 submit()
   狀態訊息寫入 #card-status（開啟 popup 中/等待插卡/驗證中/失敗原因）。 */
function cardLogin() {{ /* 依上述整合點自 caAuth.js 移植實作 */ }}
</script>
</body></html>"""


def render_admin_page(ctx: dict) -> str:
    saved = '<div class="banner ok">設定已寫入 .env——按「重啟 rag-api 套用」後生效。</div>' if ctx.get("saved") else ""
    error = f'<div class="banner err">{escape(ctx["error"])}</div>' if ctx.get("error") else ""
    reports = ctx.get("reports") or []
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>ISO 42001 維運管理台</title>
<style>{_CSS}</style>
</head>
<body>
<div class="report">
  <h1>ISO 42001 維運管理台<span class="title-en">Operations Console</span></h1>
  <hr class="report-rule">
  <div class="meta">
    <span>rag-api：<b>{escape(ctx.get("rag_state", "?"))}</b></span>
    <span>monitoring：<b>{escape(ctx.get("monitoring_state", "?"))}</b></span>
    <span>SMTP：<b>{"啟用" if ctx.get("smtp_enabled") is True else "關閉" if ctx.get("smtp_enabled") is False else "未知"}</b></span>
    <span>入口未設認證——內網開發者/管理員專用，值域治理依 ISO 稽核表單</span>
  </div>
  {saved}{error}

  <section id="settings">
    <h2>Model 設定<span class="en">Settings — 寫入 .env，重啟 rag-api 後生效</span></h2>
    <form method="post" action="api/settings">
      <table>
        <thead><tr><th>鍵</th><th>.env 值</th><th>容器內生效值</th><th>狀態</th></tr></thead>
        <tbody>{_settings_rows_html(ctx["settings_rows"])}</tbody>
      </table>
      <button type="submit">儲存到 .env</button>
      <button type="button" class="ghost" onclick="restartRag()">重啟 rag-api 套用</button>
      <span id="rag-restart-state" class="note"></span>
    </form>
  </section>

  <section id="ops">
    <h2>評估操作<span class="en">Evaluations — docker exec 至 monitoring 容器</span></h2>
    <div class="oprow">
      <form method="post" data-ajax="1" action="api/jobs/extended_vv"><button>Extended V&amp;V</button></form>
      <form method="post" data-ajax="1" action="api/jobs/online_vv"><button>Online V&amp;V</button></form>
      <form method="post" data-ajax="1" action="api/jobs/ragas"><button>RAGAS 評估</button></form>
    </div>
    <form method="post" data-ajax="1" action="api/jobs/regression_gate" class="oprow">
      <div><label>基線報告</label><select name="baseline">{_reports_options(reports)}</select></div>
      <div><label>本版報告</label><select name="current">{_reports_options(reports)}</select></div>
      <div><label>版本標籤</label><input name="tag" value="admin-ui"></div>
      <button>Regression Gate</button>
    </form>
    <form method="post" data-ajax="1" action="api/jobs/attribution" class="oprow">
      <div><label>V&amp;V 報告</label><select name="vv_report">{_reports_options(reports)}</select></div>
      <button>歸因分析</button>
    </form>
    <div class="jobbox"><div id="job-panel">{_job_panel(ctx.get("job"))}</div></div>
    <div class="note">作業全域互斥：同時只跑一個，避免評估互搶資源失真。</div>
  </section>

  <section id="reports">
    <h2>報告檢視<span class="en">Reports</span></h2>
    {_reports_table(reports)}
    <form method="get" data-ajax="1" action="api/reports/compare" class="oprow">
      <div><label>基線</label><select name="base">{_reports_options(reports)}</select></div>
      <div><label>本版</label><select name="cur">{_reports_options(reports)}</select></div>
      <button>Per-query flip 比對</button>
    </form>
    <pre id="compare-result" class="tail"></pre>
  </section>

  <section id="alerts-ops">
    <h2>索引與告警<span class="en">Index &amp; Alerts</span></h2>
    <div class="oprow">
      <form method="post" data-ajax="1" action="api/jobs/reindex_full"><button>全量 Reindex</button></form>
    </div>
    <form method="post" data-ajax="1" action="api/jobs/version_snapshot" class="oprow">
      <div><label>變更說明</label><input name="message"></div>
      <div><label>操作者</label><input name="operator"></div>
      <div><label>版本號</label><input name="version" placeholder="v1.1.1"></div>
      <button>版本快照</button>
    </form>
    <form method="post" data-ajax="1" action="api/alert-test" class="oprow">
      <div><label>等級</label><select name="severity">
        <option value="info">info</option><option value="warning">warning</option>
        <option value="critical">critical</option></select></div>
      <button>發送測試告警</button>
    </form>
    <div class="note">測試告警會走完整管線（alerts.jsonl → SSE → SMTP 若有設）；到儀表板告警區確認收到。</div>
  </section>

  <div class="footer">操作留痕：admin_console/data/changes.jsonl · 作業紀錄：jobs.jsonl · 設定備份：env-backups/
    <form method="post" action="logout" style="display:inline;margin-left:14px;"><button class="ghost" style="padding:2px 10px;font-size:11px;">登出</button></form></div>
</div>
<script>{_JS}</script>
</body>
</html>"""
```

- [ ] **Step 5: 實作 `service/app.py`**

```python
"""維運管理台 FastAPI 服務。依賴全部可注入，宿主測試不需 docker/httpx 實體。

帳密紅線：ADMIN_USERNAME/ADMIN_PASSWORD 只從環境變數讀（來源為 gitignored .env），
絕不硬編碼；比對用 secrets.compare_digest。
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from admincore import cardauth
from admincore.challenge_store import ChallengeStore
from admincore.dockerops import DockerOps
from admincore.envstore import EnvStore, SettingError, SETTINGS, WHITELIST
from admincore.jobs import JobBusy, JobManager
from admincore.render import render_admin_page, render_login_page
from admincore.reports import flip_compare, list_reports

CONTAINER_REPORTS = "/app/data/reports"
RAG_CONTAINER = "ISO42001_rag_api"
MON_CONTAINER = "ISO42001_monitoring"

JOB_CATALOG: dict[str, dict] = {
    "extended_vv":  {"container": MON_CONTAINER, "cmd": ["python3", "scripts/run_extended_vv.py"]},
    "online_vv":    {"container": MON_CONTAINER, "cmd": ["python3", "scripts/run_online_vv.py"]},
    "ragas":        {"container": MON_CONTAINER, "cmd": ["python3", "scripts/run_ragas_evaluation.py"]},
    "regression_gate": {"container": MON_CONTAINER, "params": ["baseline", "current", "tag"]},
    "attribution":  {"container": MON_CONTAINER, "params": ["vv_report"]},
    "reindex_full": {"container": RAG_CONTAINER, "cmd": ["python3", "scripts/reindex.py"]},
    "version_snapshot": {"container": RAG_CONTAINER, "params": ["message", "operator", "version"]},
}


def _default_http_post(url: str, **kw):
    import httpx
    try:
        r = httpx.post(url, timeout=5, **kw)
        try:
            body = r.json()
        except ValueError:
            body = None
        return {"status_code": r.status_code, "ok": r.status_code < 400, "json": body}
    except Exception as e:
        return {"status_code": 502, "ok": False, "json": None, "error": str(e)}


def _default_http_get(url: str, **kw):
    import httpx
    try:
        r = httpx.get(url, timeout=2, **kw)
        try:
            body = r.json()
        except ValueError:
            body = None
        return {"status_code": r.status_code, "ok": r.status_code < 400, "json": body}
    except Exception as e:
        return {"status_code": 502, "ok": False, "json": None, "error": str(e)}


def _safe_report_name(reports_dir: Path, name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and (reports_dir / name).is_file()


def _build_cmd(name: str, form: dict, reports_dir: Path) -> list[str] | None:
    """需要參數的 job 組 cmd；參數不合法回 None。"""
    if name == "regression_gate":
        base, cur = form.get("baseline", ""), form.get("current", "")
        if not (_safe_report_name(reports_dir, base) and _safe_report_name(reports_dir, cur)):
            return None
        return ["python3", "scripts/run_regression_gate.py",
                "--baseline", f"{CONTAINER_REPORTS}/{base}",
                "--current", f"{CONTAINER_REPORTS}/{cur}",
                "--tag", form.get("tag") or "admin-ui"]
    if name == "attribution":
        rep = form.get("vv_report", "")
        if not _safe_report_name(reports_dir, rep):
            return None
        return ["python3", "scripts/run_attribution.py",
                "--vv-report", f"{CONTAINER_REPORTS}/{rep}"]
    if name == "version_snapshot":
        return ["python3", "scripts/version_tracker.py", "snapshot",
                "-m", form.get("message", ""), "-o", form.get("operator", ""),
                "-v", form.get("version", "")]
    return list(JOB_CATALOG[name]["cmd"])


def _default_verify_card(signed_b64: str, expected_nonce: str):
    return cardauth.verify_pkcs7_signature(signed_b64, expected_nonce)
    # 注意：以 Task 5 移植後的實際簽名為準（參數名/順序照原始碼），此處為預設包裝。


def create_app(env_store: EnvStore, job_manager: JobManager, dockerops: DockerOps,
               reports_dir: Path, monitoring_url: str, rag_api_url: str,
               card_serials: set[str], admin_user: str, admin_password: str,
               password_fallback: bool, verify_card=None,
               http_post=None, http_get=None) -> FastAPI:
    app = FastAPI(title="ISO 42001 admin console", docs_url=None, redoc_url=None)
    post = http_post or _default_http_post
    get = http_get or _default_http_get
    verify = verify_card or _default_verify_card
    reports_dir = Path(reports_dir)
    sessions: set[str] = set()   # 記憶體 session：容器重啟即全登出（可接受）
    challenges = ChallengeStore()

    _PUBLIC = {"/login", "/api/auth/card/challenge", "/api/auth/card/verify"}

    def _new_session_response(target: str = "/"):
        token = secrets.token_urlsafe(32)
        sessions.add(token)
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie("admin_session", token, httponly=True, samesite="lax")
        return resp

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        if request.url.path in _PUBLIC or request.cookies.get("admin_session") in sessions:
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "未登入"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        return HTMLResponse(render_login_page(password_fallback=password_fallback))

    @app.get("/api/auth/card/challenge")
    async def card_challenge():
        token, nonce = challenges.issue()
        return {"challenge_token": token, "nonce": nonce}

    @app.post("/api/auth/card/verify")
    async def card_verify(request: Request):
        form = dict(await request.form())
        nonce = challenges.consume(str(form.get("challenge_token", "")))
        if nonce is None:
            return JSONResponse({"error": "challenge 無效或已過期，請重新插卡"}, status_code=401)
        try:
            claims = verify(str(form.get("signed_data", "")), nonce)
        except Exception:
            return HTMLResponse(render_login_page(password_fallback=password_fallback,
                                                  error="憑證卡驗證失敗"))
        emp = str(getattr(claims, "employee_id", "") or "")
        if emp not in card_serials:
            return HTMLResponse(render_login_page(
                password_fallback=password_fallback,
                error=f"此卡員編 {emp} 不在管理台白名單——請將其加入 .env 的 ADMIN_CARD_SERIALS 後重試"))
        job_manager.log_change({"kind": "login", "method": "card", "employee_id": emp})
        return _new_session_response()

    @app.post("/login")
    async def login(request: Request):
        if not password_fallback:
            return JSONResponse({"error": "帳密登入未啟用（break-glass 需設 ENABLE_PASSWORD_FALLBACK=true）"},
                                status_code=403)
        form = dict(await request.form())
        user_ok = secrets.compare_digest(str(form.get("username", "")), admin_user)
        pass_ok = secrets.compare_digest(str(form.get("password", "")), admin_password)
        if not (user_ok and pass_ok):   # 兩者都比完才判定，不短路
            time.sleep(0.5)
            return HTMLResponse(render_login_page(password_fallback=True, error="帳號或密碼錯誤"))
        job_manager.log_change({"kind": "login", "method": "password_fallback"})
        return _new_session_response()

    @app.post("/logout")
    async def logout(request: Request):
        sessions.discard(request.cookies.get("admin_session", ""))
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie("admin_session")
        return resp

    @app.get("/", response_class=HTMLResponse)
    async def index(saved: int = 0, error: str = ""):
        env_vals = env_store.read()
        eff = dockerops.effective_env(RAG_CONTAINER)
        rows = []
        for s in SETTINGS:
            ev, fv = env_vals.get(s["key"]), eff.get(s["key"])
            rows.append({"spec": s, "env_value": ev, "effective_value": fv,
                         "dirty": ev is not None and ev != fv})
        summary = get(f"{monitoring_url}/v1/alerts/summary")
        smtp_enabled = (summary.get("json") or {}).get("smtp_enabled") if summary["ok"] else None
        ctx = {
            "settings_rows": rows,
            "job": job_manager.current(),
            "reports": list_reports(reports_dir),
            "rag_state": dockerops.container_state(RAG_CONTAINER),
            "monitoring_state": dockerops.container_state(MON_CONTAINER),
            "smtp_enabled": smtp_enabled,
            "saved": bool(saved), "error": error or None,
        }
        return HTMLResponse(render_admin_page(ctx))

    @app.post("/api/settings")
    async def save_settings(request: Request):
        form = dict(await request.form())
        updates = {k: v for k, v in form.items() if k in WHITELIST and str(v).strip() != ""}
        try:
            changes = env_store.apply(updates)
        except SettingError as e:
            return RedirectResponse(f"/?error={quote(str(e))}", status_code=303)
        for key, old, new in changes:
            job_manager.log_change({"kind": "setting", "key": key, "old": old, "new": new})
        return RedirectResponse("/?saved=1", status_code=303)

    @app.post("/api/restart")
    async def restart_rag():
        dockerops.restart(RAG_CONTAINER)
        job_manager.log_change({"kind": "restart", "container": RAG_CONTAINER})
        return {"ok": True}

    @app.get("/api/rag-health")
    async def rag_health():
        return {"ok": get(f"{rag_api_url}/health")["ok"]}

    @app.post("/api/jobs/{name}")
    async def start_job(name: str, request: Request):
        if name not in JOB_CATALOG:
            return JSONResponse({"error": f"未知作業 {name}"}, status_code=404)
        form = dict(await request.form())
        cmd = _build_cmd(name, form, reports_dir)
        if cmd is None:
            return JSONResponse({"error": "參數不合法：報告檔不存在或含路徑分隔符"}, status_code=400)
        try:
            job = job_manager.start(name, JOB_CATALOG[name]["container"], cmd,
                                    meta={"form": {k: v for k, v in form.items()}})
        except JobBusy as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return job

    @app.get("/api/jobs/current")
    async def current_job():
        return job_manager.current() or {"state": "idle"}

    @app.get("/api/reports")
    async def reports():
        return list_reports(reports_dir)

    @app.get("/api/reports/compare")
    async def compare(base: str, cur: str):
        if not (_safe_report_name(reports_dir, base) and _safe_report_name(reports_dir, cur)):
            return JSONResponse({"error": "報告檔名不合法"}, status_code=400)
        return flip_compare(reports_dir / base, reports_dir / cur)

    @app.post("/api/alert-test")
    async def alert_test(request: Request, severity: str = "info"):
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = dict(await request.form())
            severity = form.get("severity", severity)
        if severity not in ("info", "warning", "critical"):
            return JSONResponse({"error": "severity 只能是 info/warning/critical"}, status_code=400)
        r = post(f"{monitoring_url}/v1/alerts/test", params={"severity": severity})
        job_manager.log_change({"kind": "alert_test", "severity": severity, "ok": r["ok"]})
        return {"ok": r["ok"]}

    return app


```python
def create_app_from_env() -> FastAPI:
    env_file = Path(os.environ.get("ENV_FILE", "/host_env/.env"))
    data_dir = Path(os.environ.get("ADMIN_DATA_DIR", "/app/data"))
    card_serials = {s.strip() for s in os.environ.get("ADMIN_CARD_SERIALS", "").split(",") if s.strip()}
    password_fallback = os.environ.get("ENABLE_PASSWORD_FALLBACK", "false").lower() in ("1", "true", "yes")
    admin_user = os.environ.get("ADMIN_USERNAME", "")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not card_serials and not password_fallback:
        raise RuntimeError("ADMIN_CARD_SERIALS 為空且 ENABLE_PASSWORD_FALLBACK 未開——無任何登入途徑（設定錯誤要大聲）")
    if password_fallback and (not admin_user or not admin_password):
        raise RuntimeError("啟用帳密 fallback 但 ADMIN_USERNAME/ADMIN_PASSWORD 未設定")
    ops = DockerOps()
    return create_app(
        EnvStore(env_file, data_dir / "env-backups"),
        JobManager(data_dir, ops.exec_stream),
        ops,
        Path(os.environ.get("REPORTS_DIR", "/mon_data/reports")),
        os.environ.get("MONITORING_URL", "http://monitoring:8200"),
        os.environ.get("RAG_API_URL", "http://rag-api:8000"),
        card_serials=card_serials,
        admin_user=admin_user,
        admin_password=admin_password,
        password_fallback=password_fallback,
    )


app = create_app_from_env() if os.environ.get("ADMIN_RUNTIME") == "1" else None
```

（uvicorn 以 `ADMIN_RUNTIME=1 uvicorn service.app:app` 啟動；宿主測試 import 模組時不會嘗試連 docker。）

- [ ] **Step 6: 跑測試確認通過**

Run: `cd admin_console && python3 -m pytest tests/ -q`
Expected: 全部 PASS（約 20 個）

- [ ] **Step 7: Commit**

```bash
git add admin_console/admincore/render.py admin_console/service admin_console/tests
git commit -m "feat(admin): 管理台頁面與 FastAPI 路由（設定/作業/報告/告警）"
```

---

### Task 7: Dockerfile、compose 服務與上線煙霧測試

**Files:**
- Create: `admin_console/Dockerfile`
- Create: `admin_console/requirements.txt`
- Create: `admin_console/data/.gitkeep`
- Create: `admin_console/.gitignore`
- Modify: `docker-compose.yaml`（volumes 區塊前加 admin 服務）

**Interfaces:**
- Consumes: Task 6 的 `service.app:app` 與 `ADMIN_RUNTIME=1`。

- [ ] **Step 1: 建立 requirements 與 Dockerfile**

`admin_console/requirements.txt`：

```
fastapi==0.115.*
uvicorn[standard]==0.32.*
docker==7.*
httpx==0.27.*
cryptography==43.*
asn1crypto==1.5.1
```

`admin_console/Dockerfile`：

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY admincore/ admincore/
COPY service/ service/
ENV ADMIN_RUNTIME=1
EXPOSE 8300
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8300"]
```

`admin_console/.gitignore`：

```
data/*
!data/.gitkeep
```

- [ ] **Step 2: compose 加 admin 服務**

在 `docker-compose.yaml` 頂層 `volumes:` 區塊之前插入：

```yaml
  # ---------- 9. Admin console（維運管理台；內網開發者/管理員入口，無認證，不進 nginx） ----------
  admin:
    build:
      context: ./admin_console
    container_name: ISO42001_admin
    restart: unless-stopped
    ports:
      - "8300:8300"
    environment:
      MONITORING_URL: "http://monitoring:8200"
      RAG_API_URL: "http://rag-api:8000"
      ENV_FILE: /host_env/.env
      REPORTS_DIR: /mon_data/reports
      ADMIN_DATA_DIR: /app/data
      # 憑證卡白名單與 break-glass：值只存在 gitignored .env，不寫死在此。
      # 注意：CARD_DEV_SKIP_NONCE_BINDING 嚴禁出現在這裡（僅限 E2E 臨時 override）。
      ADMIN_CARD_SERIALS: ${ADMIN_CARD_SERIALS}
      ENABLE_PASSWORD_FALLBACK: ${ENABLE_PASSWORD_FALLBACK:-false}
      ADMIN_USERNAME: ${ADMIN_USERNAME:-}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:-}
    volumes:
      # docker sock：重啟 rag-api 與 docker exec 跑評估/索引腳本
      - /var/run/docker.sock:/var/run/docker.sock
      # .env 單檔掛載（envstore 原地覆寫、不 rename，容器才看得到新值）
      - ./.env:/host_env/.env
      # monitoring 報告（唯讀）
      - ./monitoring_addon/data:/mon_data:ro
      # admin 自己的留痕與設定備份
      - ./admin_console/data:/app/data
```

- [ ] **Step 3: 建置啟動**

Run: `docker compose build admin && docker compose up -d admin`
Expected: `Container ISO42001_admin  Started`

- [ ] **Step 4: 煙霧測試**

Run（production-like：`.env` 的 `ENABLE_PASSWORD_FALLBACK=false`，帳密路徑應被拒；有 session 的功能驗證留給 Task 9 的卡片 E2E）:
```bash
sleep 4
curl -s -o /dev/null -w '未登入首頁: %{http_code}\n' http://localhost:8300/                 # 303
curl -s -o /dev/null -w '未登入 API: %{http_code}\n' http://localhost:8300/api/jobs/current # 401
curl -s http://localhost:8300/login | grep -c '插卡登入'                                    # 1
curl -s http://localhost:8300/login | grep -c 'name="password"'                             # 0（fallback 關閉不渲染）
curl -s http://localhost:8300/api/auth/card/challenge | grep -c 'challenge_token'           # 1
curl -s -o /dev/null -w 'fallback 關閉時帳密登入: %{http_code}\n' \
  --data 'username=x&password=y' http://localhost:8300/login                                # 403
```
Expected: 如各行註解

- [ ] **Step 5: Commit**

```bash
git add admin_console/Dockerfile admin_console/requirements.txt admin_console/.gitignore admin_console/data/.gitkeep docker-compose.yaml
git commit -m "feat(admin): Dockerfile 與 compose 服務（:8300，docker sock + .env + 報告掛載）"
```

---

### Task 8: monitoring 告警訊息拆層次

**Files:**
- Modify: `monitoring_addon/monitoring/dashboard_render.py`（`_render_alerts_table`、SSE `buildRow`、CSS）
- Test: `monitoring_addon/tests/test_dashboard_render.py`

**Interfaces:**
- 不變：`.alerts-table tbody`、td class `ts`/`sev-*`、四欄結構、pill 文字格式（JS 契約）。

- [ ] **Step 1: 寫失敗測試**

在 `monitoring_addon/tests/test_dashboard_render.py` 末尾加入：

```python
def test_render_alert_rows_layered():
    p = copy.deepcopy(_PAYLOAD)
    p["alerts"]["recent"] = [{
        "timestamp": "2026-07-08T13:20:32+08:00", "severity": "critical",
        "source": "availability", "title": "system availability down",
        "message": "關鍵依賴連續 3 次探測失敗：['embed-proxy']（/ready 失敗可能為 Triton 後端掛掉）",
    }]
    html = render_dashboard(p)
    assert 'class="src-pill"' in html          # 來源標籤
    assert 'class="alert-main"' in html        # 主訊息
    assert 'class="alert-aux"' in html         # 輔助說明
    assert "system availability down" in html
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd monitoring_addon && python3 -m pytest tests/test_dashboard_render.py::test_render_alert_rows_layered -q`
Expected: FAIL

- [ ] **Step 3: 實作**

(a) `_render_alerts_table` 的列渲染（原第四欄 `<td><strong>…title…</strong><br>…message…</td>`）改為：

```python
            f"<td class='ts'>{escape(ts)}</td>"
            f"<td class='sev-{escape(sev)}'>{escape(sev.upper())}</td>"
            f"<td><span class='src-pill'>{escape(_strip_emoji(a.get('source', '?')))}</span></td>"
            f"<td><div class='alert-main'>{escape(_strip_emoji(a.get('title', '')))}</div>"
            f"<div class='alert-aux'>{escape(_strip_emoji(a.get('message', ''))[:200])}</div></td>"
```

(b) CSS 區塊（alerts 段落）加入：

```css
  .alerts-table .src-pill {{ display:inline-block; padding:1px 8px; font-size:11px; font-weight:800;
                              border:1px solid var(--line); font-family:var(--mono); white-space:nowrap; }}
  .alerts-table .alert-main {{ font-weight:800; }}
  .alerts-table .alert-aux {{ font-size:12px; color:var(--muted); margin-top:2px; }}
```

(c) SSE `buildRow` 同步（原 `tdSrc`/`tdMsg` 組法）改為：

```javascript
    const tdSrc = el('td');
    const pill = el('span', {{ cls: 'src-pill', text: (alert.source || '?').replace(EMOJI_RE, '') }});
    tdSrc.appendChild(pill);
    tr.appendChild(tdSrc);
    const tdMsg = el('td');
    tdMsg.appendChild(el('div', {{ cls: 'alert-main', text: (alert.title || '').replace(EMOJI_RE, '') }}));
    tdMsg.appendChild(el('div', {{ cls: 'alert-aux', text: msg }}));
    tr.appendChild(tdMsg);
```

（原本的 `<code>` source、`<strong>`+`<br>` 寫法移除；`msg` 已在前面去 emoji。）

- [ ] **Step 4: 跑 monitoring 全套測試**

Run: `cd monitoring_addon && python3 -m pytest tests/ -q`
Expected: 全部 PASS（≥115）

- [ ] **Step 5: 重建 monitoring 容器並確認**

Run: `docker compose build monitoring && docker compose up -d --no-deps monitoring && docker restart ISO42001_nginx && sleep 5 && curl -sk https://localhost:8443/monitoring/dashboard | grep -c 'src-pill\|alert-main'`
Expected: ≥ 1（近 24h 有告警時；若當下無告警，改跑 `curl -s -X POST 'http://localhost:8200/v1/alerts/test?severity=info'` 後重抓）

- [ ] **Step 6: Commit**

```bash
git add monitoring_addon/monitoring/dashboard_render.py monitoring_addon/tests/test_dashboard_render.py
git commit -m "feat(monitoring): 告警訊息拆層次（來源標籤+主訊息+輔助說明）"
```

---

### Task 9: Playwright 端到端驗證

**Files:**
- Create（scratchpad，不入版控）：無——直接以 Playwright MCP 操作實站

**Interfaces:**
- Consumes: Task 7 上線的 `http://localhost:8300/`、Task 8 的儀表板、`SRC/cht/` HiPKI mock。

- [ ] **Step 1: 設定回路驗證**

**Step 0：架設卡片測試環境（dev-only，事後必拆）**

1. 啟動 HiPKI mock：`cd ~/anila-card-login-export-20260708/cht && docker build -t hipki-mock . && docker run -d --name hipki-mock -p 16888:16888 hipki-mock`
2. 建 override（寫到 scratchpad，勿入 repo）`admin-e2e-override.yml`：

```yaml
services:
  admin:
    environment:
      CARD_DEV_SKIP_NONCE_BINDING: "1"          # mock 簽章寫死（eContent=b"TBS"），僅 E2E
      ADMIN_CARD_SERIALS: "1090868"             # mock 測試卡員編
```

3. `docker compose -f docker-compose.yaml -f <scratchpad>/admin-e2e-override.yml up -d admin`

**卡片登入流程驗證**（Playwright 開 `http://localhost:8300/`）：
0. 應被導向登入頁（只有「插卡登入」，無密碼欄）。按插卡登入 → popup（mock）→ PIN 輸入 `123456` → 應 303 回首頁、session 生效；另試錯誤 PIN（mock 對非 123456 回 ret_code=1）應顯示失敗訊息。確認 `changes.jsonl` 有 `{"kind":"login","method":"card","employee_id":"1090868"}`。
1. 截圖確認四區塊齊全、視覺與報告書系統一致、無 emoji。
2. 把 `TOP_K` 改為不同值（如 5→6）→ 儲存 → 顯示「設定已寫入」、該列變「已寫入，待重啟」。
3. 按「重啟 rag-api 套用」→ 等狀態顯示恢復 → 重新整理 → `TOP_K` 生效值 = 新值、狀態「一致」。
4. 把 `TOP_K` 改回原值並再走一次重啟（不留實驗值）。

- [ ] **Step 2: 作業與告警驗證**

1. 按「版本快照」（填 message=admin-e2e、operator=admin、version 留空）→ job 面板顯示執行中→完成（exit 0）。
2. 按「發送測試告警」（info）→ 開 `https://localhost:8443/monitoring/dashboard` 確認告警表新列出現、來源 pill 樣式正確。
3. 檢查 `admin_console/data/changes.jsonl` 有 setting/restart/job/alert_test 各類紀錄。

- [ ] **Step 3: 問題修正與收尾**

發現的視覺/行為問題就地修（改 render/app 需重跑 `cd admin_console && python3 -m pytest tests/ -q` 並 `docker compose build admin && docker compose up -d admin`）。修正後 commit：

```bash
git add -A admin_console
git commit -m "fix(admin): E2E 驗證後修正"
```

（無問題則略過。）

- [ ] **Step 4: 拆除 dev-only 環境（必做）**

```bash
docker rm -f hipki-mock
docker compose up -d admin        # 不帶 override，回 production-like
sleep 4
curl -s http://localhost:8300/login | grep -c '插卡登入'                       # 1
curl -s -o /dev/null -w 'fallback 仍關閉: %{http_code}\n' \
  --data 'username=x&password=y' http://localhost:8300/login                   # 403
docker inspect ISO42001_admin --format '{{.Config.Env}}' | grep -c SKIP_NONCE  # 0（旁路已移除）
```
Expected: 如各行註解——`CARD_DEV_SKIP_NONCE_BINDING` 不得殘留在常駐容器。
