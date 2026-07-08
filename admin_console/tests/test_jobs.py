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
