"""VieNeu 本地服务路由：状态 / 启停 / 音色 / 设置。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.speech import vieneu_service
from lib.speech.vieneu_service import VieneuServiceError, VieneuStatus
from server.dependencies import get_config_service
from server.error_handlers import register_error_handlers
from server.i18n import get_translator
from server.routers import vieneu
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.factories import make_translator
from tests.http_capture import capture_http


class _FakeManager:
    """路由层只依赖 ensure_running / stop / status；状态机本身在 test_vieneu_service 覆盖。"""

    def __init__(self) -> None:
        self.state = "stopped"
        self.fail: str | None = None

    def status(self) -> VieneuStatus:
        ready = self.state == "ready"
        return VieneuStatus(
            state=self.state,
            pid=77 if ready else None,
            last_error=self.fail,
            base_url="http://127.0.0.1:8000/v1" if ready else None,
            managed=ready,
            logs=["line"],
        )

    async def ensure_running(self) -> str:
        if self.fail:
            self.state = "error"
            raise VieneuServiceError(self.fail)
        self.state = "ready"
        return "http://127.0.0.1:8000/v1"

    async def stop(self) -> None:
        self.state = "stopped"


class _MemorySettings:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get_all_settings(self) -> dict[str, str]:
        return dict(self.data)

    async def set_setting(self, key: str, value: str) -> None:
        self.data[key] = value


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    fake = _FakeManager()
    monkeypatch.setattr(vieneu, "get_vieneu_manager", lambda: fake)
    return fake


@pytest.fixture
def settings_store() -> _MemorySettings:
    return _MemorySettings()


@pytest.fixture
def vieneu_client(manager: _FakeManager, settings_store: _MemorySettings) -> Iterator[TestClient]:
    app = FastAPI()
    register_error_handlers(app)
    override_auth(app)
    app.dependency_overrides[get_translator] = lambda: make_translator("zh")
    app.dependency_overrides[get_config_service] = lambda: settings_store
    app.include_router(vieneu.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as c:
        yield c


def test_status_stopped(vieneu_client: TestClient):
    body = vieneu_client.get("/api/v1/vieneu/status").json()
    assert body == {
        "state": "stopped",
        "pid": None,
        "last_error": None,
        "base_url": None,
        "managed": False,
        "logs": ["line"],
    }


def test_start_then_stop(vieneu_client: TestClient):
    started = vieneu_client.post("/api/v1/vieneu/start")
    assert started.status_code == 200
    assert started.json()["state"] == "ready"
    assert started.json()["pid"] == 77

    stopped = vieneu_client.post("/api/v1/vieneu/stop")
    assert stopped.json()["state"] == "stopped"


def test_start_failure_is_503_with_detail(vieneu_client: TestClient, manager: _FakeManager):
    manager.fail = "端口被占用"
    resp = vieneu_client.post("/api/v1/vieneu/start")
    assert resp.status_code == 503
    assert "端口被占用" in resp.json()["detail"]


def test_voices_catalog_when_not_running(vieneu_client: TestClient):
    body = vieneu_client.get("/api/v1/vieneu/voices").json()
    assert body["source"] == "catalog"
    mai = next(v for v in body["voices"] if v["id"] == "Mai Anh")
    assert mai == {"id": "Mai Anh", "label": "Mai Anh", "gender": "female", "locale": "vi", "region": "north"}
    assert len(body["voices"]) == 25
    assert {v["gender"] for v in body["voices"]} == {"male", "female"}


def test_voices_from_server_when_ready(vieneu_client: TestClient, manager: _FakeManager):
    manager.state = "ready"
    with capture_http() as router:
        router.get("http://127.0.0.1:8000/v1/voices").respond(
            200, json={"object": "list", "data": [{"id": "a", "name": "Đoan Trang", "gender": "female"}]}
        )
        body = vieneu_client.get("/api/v1/vieneu/voices").json()
    assert body["source"] == "server"
    assert [(v["id"], v["gender"]) for v in body["voices"]] == [("Đoan Trang", "female")]


def test_voices_fall_back_when_server_unreachable(vieneu_client: TestClient, manager: _FakeManager):
    manager.state = "ready"
    with capture_http() as router:
        router.get("http://127.0.0.1:8000/v1/voices").respond(500)
        body = vieneu_client.get("/api/v1/vieneu/voices").json()
    assert body["source"] == "catalog"


def test_settings_roundtrip(vieneu_client: TestClient, settings_store: _MemorySettings):
    defaults = vieneu_client.get("/api/v1/vieneu/settings").json()
    assert defaults["port"] == 8000
    assert defaults["device"] == "auto"

    patched = vieneu_client.patch(
        "/api/v1/vieneu/settings",
        json={"port": 9001, "device": "cuda", "int8": True, "workdir": "/opt/vieneu", "enabled": False},
    ).json()

    assert (patched["port"], patched["device"], patched["int8"], patched["enabled"]) == (9001, "cuda", True, False)
    assert settings_store.data[vieneu_service.SETTING_PORT] == "9001"
    assert settings_store.data[vieneu_service.SETTING_ENABLED] == "false"
    assert settings_store.data[vieneu_service.SETTING_INT8] == "true"


def test_settings_reject_bad_input(vieneu_client: TestClient):
    assert vieneu_client.patch("/api/v1/vieneu/settings", json={"port": 70000}).status_code == 422
    assert vieneu_client.patch("/api/v1/vieneu/settings", json={"device": "tpu"}).status_code == 422
    assert vieneu_client.patch("/api/v1/vieneu/settings", json={"command": "'unterminated"}).status_code == 422
