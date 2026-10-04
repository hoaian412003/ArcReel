"""VieNeu 设置经 PATCH 保存后必须真的落库，而不只是在本次请求的事务里可见。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.config.service import ConfigService
from lib.db import async_session_factory
from lib.speech import vieneu_service
from server.error_handlers import register_error_handlers
from server.routers import vieneu
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


async def _stored_host() -> str:
    async with async_session_factory() as session:
        return await ConfigService(session).get_setting(vieneu_service.SETTING_HOST, "")


async def test_patched_host_is_visible_to_other_sessions():
    app = FastAPI()
    override_auth(app)
    app.include_router(vieneu.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)

    with TestClient(app) as client:
        response = client.patch("/api/v1/vieneu/settings", json={"host": "vieneu"})
    assert response.json()["host"] == "vieneu"

    try:
        assert await _stored_host() == "vieneu"
    finally:
        async with async_session_factory() as session:
            await ConfigService(session).set_setting(vieneu_service.SETTING_HOST, "")
            await session.commit()
