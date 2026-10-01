"""提示词模版只读 API：系统设置页按类别列出内置模版，展示单个模版的源文与元数据，以及单个片段的正文与引用方。

路由前缀: /api/v1/prompt-templates
"""

from __future__ import annotations

import importlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lib.config.service import ConfigService
from lib.infra.api_errors import NotFoundError, UnprocessableError
from lib.prompts.h3_authoring_template import MAX_TEMPLATE_CHARS
from lib.prompts.h3_authoring_template import TEMPLATE_ID as H3_AUTHORING_TEMPLATE_ID
from lib.prompts.prompt_builders_reference import preview_h3_ref2va_template
from lib.prompts.prompt_templates import PartialEntry, PromptTemplates, TemplateError, TemplateMeta, UserActionTrigger
from lib.prompts.prompt_templates.builtin import builtin_templates
from server.dependencies import get_config_service

router = APIRouter(prefix="/prompt-templates")


def get_prompt_templates() -> PromptTemplates:
    return builtin_templates


_AGENT_SESSION_TRIGGER = UserActionTrigger(kind="user_action", name="agent_session")

Templates = Annotated[PromptTemplates, Depends(get_prompt_templates)]


class PromptTemplateListResponse(BaseModel):
    templates: list[TemplateMeta]


class PromptTemplateDetailResponse(BaseModel):
    template: TemplateMeta
    source: str
    partials: list[PartialEntry]
    """按首次引用顺序；变体族展开为 ``applies_to`` 声明的全部轴值。"""
    output_schema: dict[str, Any] | None = Field(default=None, exclude_if=lambda value: value is None)


@router.get("")
async def list_prompt_templates(templates: Templates) -> PromptTemplateListResponse:
    """Agent 语言规范随 Agent 会话拼进系统提示，展示在 Agent 分栏，不进本列表；详情接口仍可读取。"""
    return PromptTemplateListResponse(
        templates=[item for item in templates.list_templates() if item.invoked_by != _AGENT_SESSION_TRIGGER]
    )


class H3AuthoringOverrideResponse(BaseModel):
    builtin: str
    """内置模版正文，编辑器的起点与「恢复默认」的内容。"""
    override: str | None
    """全局替换正文；没有时为 None。"""
    max_chars: int


class H3AuthoringBody(BaseModel):
    body: str


class H3AuthoringPreviewResponse(BaseModel):
    prompt: str


def _checked_h3_body(body: str) -> str:
    """校验 H3 提示词编写模版的替换正文，返回示例渲染结果；违约抛 422。"""
    if not body.strip() or len(body) > MAX_TEMPLATE_CHARS:
        raise UnprocessableError("h3_authoring_template_size_invalid", max=MAX_TEMPLATE_CHARS)
    try:
        return preview_h3_ref2va_template(body)
    except TemplateError as exc:
        raise UnprocessableError("h3_authoring_template_invalid", detail=str(exc)) from exc


# 以下三个 override 路由须注册在模版详情之前：模版详情的 ``{template_id:path}`` 会吞下任意子路径。
@router.get("/overrides/h3-authoring")
async def get_h3_authoring_override(
    templates: Templates, svc: Annotated[ConfigService, Depends(get_config_service)]
) -> H3AuthoringOverrideResponse:
    builtin, _ = templates.read_source(H3_AUTHORING_TEMPLATE_ID)
    return H3AuthoringOverrideResponse(
        builtin=builtin, override=await svc.get_h3_authoring_template(), max_chars=MAX_TEMPLATE_CHARS
    )


@router.put("/overrides/h3-authoring")
async def put_h3_authoring_override(
    req: H3AuthoringBody, svc: Annotated[ConfigService, Depends(get_config_service)]
) -> H3AuthoringPreviewResponse:
    rendered = _checked_h3_body(req.body)
    await svc.set_h3_authoring_template(req.body)
    return H3AuthoringPreviewResponse(prompt=rendered)


@router.delete("/overrides/h3-authoring")
async def delete_h3_authoring_override(svc: Annotated[ConfigService, Depends(get_config_service)]) -> None:
    await svc.set_h3_authoring_template("")


@router.post("/overrides/h3-authoring/preview")
async def preview_h3_authoring_override(req: H3AuthoringBody) -> H3AuthoringPreviewResponse:
    """不落盘：校验正文并返回用示例数据渲染出的完整提示词。"""
    return H3AuthoringPreviewResponse(prompt=_checked_h3_body(req.body))


@router.get("/partials/{name:path}")
async def get_prompt_partial(name: str, templates: Templates) -> PartialEntry:
    try:
        return templates.read_partial(name)
    except TemplateError:
        raise NotFoundError("prompt_partial_not_found", id=name) from None


@router.get("/{template_id:path}")
async def get_prompt_template(template_id: str, templates: Templates) -> PromptTemplateDetailResponse:
    metadata = next((item for item in templates.list_templates() if item.id == template_id), None)
    if metadata is None:
        raise NotFoundError("prompt_template_not_found", id=template_id)
    source, partials = templates.read_source(template_id)
    return PromptTemplateDetailResponse(
        template=metadata,
        source=source,
        partials=partials,
        output_schema=_output_json_schema(metadata.output_schema),
    )


def _output_json_schema(spec: str | None) -> dict[str, Any] | None:
    """把 frontmatter 的 ``module:Class`` 解析为该 pydantic 模型的 JSON schema。"""
    if spec is None:
        return None
    module_name, _, class_name = spec.partition(":")
    model = getattr(importlib.import_module(module_name), class_name)
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"output_schema 不是 pydantic 模型: {spec}")
    return model.model_json_schema()
