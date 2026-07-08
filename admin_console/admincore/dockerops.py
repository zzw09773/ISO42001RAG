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
