from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.prompts.prompt_builders_reference import preview_h3_ref2va_template
from lib.prompts.prompt_templates.builtin import builtin_templates
from server.dependencies import get_config_service
from server.error_handlers import register_error_handlers
from server.routers import prompt_templates as prompt_templates_router
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

TEMPLATE_ID = "text/reference_video_prompt_authoring_h3_ref2va"
BUILTIN, _ = builtin_templates.read_source(TEMPLATE_ID)


def _client(stored: str | None = None) -> tuple[TestClient, MagicMock]:
    svc = MagicMock()
    svc.get_h3_authoring_template = AsyncMock(return_value=stored)
    svc.set_h3_authoring_template = AsyncMock()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(prompt_templates_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_config_service] = lambda: svc
    override_auth(app)
    return TestClient(app), svc


def test_get_returns_builtin_source_and_stored_override():
    client, _ = _client(stored="custom body")
    res = client.get("/api/v1/prompt-templates/overrides/h3-authoring")
    assert res.status_code == 200
    assert res.json()["builtin"] == BUILTIN
    assert res.json()["override"] == "custom body"


def test_put_saves_a_valid_body_and_returns_the_sample_render():
    client, svc = _client()
    body = BUILTIN.replace("# 角色与任务", "# 自定义角色与任务")
    res = client.put("/api/v1/prompt-templates/overrides/h3-authoring", json={"body": body})
    assert res.status_code == 200
    assert "自定义角色与任务" in res.json()["prompt"]
    svc.set_h3_authoring_template.assert_awaited_once_with(body)


def test_put_rejects_invalid_bodies_without_saving():
    client, svc = _client()
    for body in (
        BUILTIN.replace('{{ partial("shared/writing_syntax") }}', ""),
        BUILTIN + "{{ not_a_slot }}",
        BUILTIN + "{% set x = 1 %}",
        "   ",
    ):
        res = client.put("/api/v1/prompt-templates/overrides/h3-authoring", json={"body": body})
        assert res.status_code == 422
    svc.set_h3_authoring_template.assert_not_awaited()


def test_preview_does_not_save():
    client, svc = _client()
    res = client.post("/api/v1/prompt-templates/overrides/h3-authoring/preview", json={"body": BUILTIN})
    assert res.status_code == 200
    assert res.json()["prompt"] == preview_h3_ref2va_template()
    svc.set_h3_authoring_template.assert_not_awaited()


def test_delete_clears_the_override():
    client, svc = _client("x")
    assert client.delete("/api/v1/prompt-templates/overrides/h3-authoring").status_code == 200
    svc.set_h3_authoring_template.assert_awaited_once_with("")
