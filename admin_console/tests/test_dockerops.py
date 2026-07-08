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
