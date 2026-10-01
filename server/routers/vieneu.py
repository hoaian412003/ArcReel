"""VieNeu-TTS 本地服务路由：状态 / 启停 / 音色 / 设置。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lib.backends.audio_backends.base import VoiceOption
from lib.backends.audio_backends.vieneu import fetch_server_voices, static_voice_catalog
from lib.config.service import ConfigService
from lib.infra.api_errors import UnprocessableError
from lib.speech import vieneu_service as svc_mod
from lib.speech.vieneu_service import VieneuConfig, VieneuServiceError, VieneuStatus, get_vieneu_manager
from server.dependencies import get_config_service
from server.i18n import Translator

router = APIRouter()


class VieneuStatusResponse(BaseModel):
    state: Literal["stopped", "starting", "ready", "error"]
    pid: int | None
    last_error: str | None
    base_url: str | None
    managed: bool
    logs: list[str]


class VieneuVoice(BaseModel):
    id: str
    label: str
    gender: Literal["male", "female"] | None = None
    locale: str | None = None
    region: str | None = None


class VieneuVoicesResponse(BaseModel):
    #: server = 来自运行中的服务；catalog = 内置已核实目录（服务未运行 / 不可达）
    source: Literal["server", "catalog"]
    voices: list[VieneuVoice]


class VieneuSettings(BaseModel):
    enabled: bool = True
    command: str = ""
    workdir: str = ""
    host: str = ""
    port: int = Field(svc_mod.DEFAULT_PORT, ge=1, le=65535)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    int8: bool = False
    startup_timeout_seconds: int = Field(svc_mod.DEFAULT_STARTUP_TIMEOUT_SECONDS, ge=5, le=3600)
    idle_minutes: int = Field(svc_mod.DEFAULT_IDLE_MINUTES, ge=0, le=1440)


class VieneuSettingsPatch(BaseModel):
    enabled: bool | None = None
    command: str | None = None
    workdir: str | None = None
    host: str | None = None
    port: int | None = Field(None, ge=1, le=65535)
    device: Literal["auto", "cpu", "cuda"] | None = None
    int8: bool | None = None
    startup_timeout_seconds: int | None = Field(None, ge=5, le=3600)
    idle_minutes: int | None = Field(None, ge=0, le=1440)


_SETTING_KEYS: dict[str, str] = {
    "enabled": svc_mod.SETTING_ENABLED,
    "command": svc_mod.SETTING_COMMAND,
    "workdir": svc_mod.SETTING_WORKDIR,
    "host": svc_mod.SETTING_HOST,
    "port": svc_mod.SETTING_PORT,
    "device": svc_mod.SETTING_DEVICE,
    "int8": svc_mod.SETTING_INT8,
    "startup_timeout_seconds": svc_mod.SETTING_STARTUP_TIMEOUT,
    "idle_minutes": svc_mod.SETTING_IDLE_MINUTES,
}


def _status_response(status: VieneuStatus) -> VieneuStatusResponse:
    return VieneuStatusResponse(
        state=status.state,
        pid=status.pid,
        last_error=status.last_error,
        base_url=status.base_url,
        managed=status.managed,
        logs=status.logs[-50:],
    )


def _voice(option: VoiceOption) -> VieneuVoice:
    return VieneuVoice(
        id=option.id, label=option.label, gender=option.gender, locale=option.locale, region=option.region
    )


@router.get("/vieneu/status", response_model=VieneuStatusResponse)
async def get_vieneu_status() -> VieneuStatusResponse:
    return _status_response(get_vieneu_manager().status())


@router.post("/vieneu/start", response_model=VieneuStatusResponse)
async def start_vieneu(_t: Translator) -> VieneuStatusResponse:
    """拉起（或复用）本地服务并等待就绪；失败返回 503 与错误详情。"""
    manager = get_vieneu_manager()
    try:
        await manager.ensure_running()
    except VieneuServiceError as exc:
        raise HTTPException(status_code=503, detail=_t("vieneu_start_failed", err_msg=str(exc))) from exc
    return _status_response(manager.status())


@router.post("/vieneu/stop", response_model=VieneuStatusResponse)
async def stop_vieneu() -> VieneuStatusResponse:
    manager = get_vieneu_manager()
    await manager.stop()
    return _status_response(manager.status())


@router.get("/vieneu/voices", response_model=VieneuVoicesResponse)
async def list_vieneu_voices() -> VieneuVoicesResponse:
    """带性别 / 区域标注的音色；服务在线取服务端目录，否则回落内置目录。不会触发服务启动。"""
    manager = get_vieneu_manager()
    status = manager.status()
    if status.state == "ready" and status.base_url:
        fetched = await fetch_server_voices(status.base_url)
        if fetched:
            return VieneuVoicesResponse(source="server", voices=[_voice(v) for v in fetched])
    return VieneuVoicesResponse(source="catalog", voices=[_voice(v) for v in static_voice_catalog()])


def _settings_from(config: VieneuConfig, raw_command: str) -> VieneuSettings:
    return VieneuSettings(
        enabled=config.enabled,
        command=raw_command,
        workdir=config.workdir,
        host=config.host,
        port=config.port,
        device=config.device,  # type: ignore[arg-type]
        int8=config.int8,
        startup_timeout_seconds=int(config.startup_timeout_seconds),
        idle_minutes=int(config.idle_minutes),
    )


@router.get("/vieneu/settings", response_model=VieneuSettings)
async def get_vieneu_settings(svc: ConfigService = Depends(get_config_service)) -> VieneuSettings:
    all_settings = await svc.get_all_settings()
    return _settings_from(VieneuConfig.from_settings(all_settings), all_settings.get(svc_mod.SETTING_COMMAND, ""))


@router.patch("/vieneu/settings", response_model=VieneuSettings)
async def patch_vieneu_settings(
    patch: VieneuSettingsPatch,
    svc: ConfigService = Depends(get_config_service),
) -> VieneuSettings:
    """更新 vieneu_* 系统设置；下次启动服务时生效（运行中的进程不受影响）。"""
    import shlex

    values = patch.model_dump(exclude_none=True)
    if "command" in values:
        values["command"] = values["command"].strip()
        try:
            shlex.split(values["command"])
        except ValueError as exc:
            raise UnprocessableError("vieneu_command_invalid") from exc
    for field_name, value in values.items():
        stored = str(value).lower() if isinstance(value, bool) else str(value)
        await svc.set_setting(_SETTING_KEYS[field_name], stored)
    all_settings = await svc.get_all_settings()
    return _settings_from(VieneuConfig.from_settings(all_settings), all_settings.get(svc_mod.SETTING_COMMAND, ""))
