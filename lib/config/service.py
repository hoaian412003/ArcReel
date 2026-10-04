from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.infra.schema_guards import is_int
from lib.prompts.h3_authoring_template import SETTING_KEY as H3_AUTHORING_TEMPLATE_SETTING
from lib.prompts.h3_authoring_template import parse_h3_authoring_template
from lib.prompts.h3_guide_preset import CUSTOM_SETTING_KEY as H3_GUIDE_CUSTOM_SETTING
from lib.prompts.h3_guide_preset import DEFAULT_H3_GUIDE_PRESET, parse_h3_guide_custom, parse_h3_guide_preset
from lib.prompts.h3_guide_preset import SETTING_KEY as H3_GUIDE_PRESET_SETTING

_DEFAULT_VIDEO_BACKEND = "gemini-aistudio/veo-3.1-lite-generate-preview"
_DEFAULT_IMAGE_BACKEND = "gemini-aistudio/gemini-3.1-flash-image-preview"
_DEFAULT_TEXT_BACKEND = "gemini-aistudio/gemini-3-flash-preview"
_DEFAULT_AUDIO_BACKEND = "dashscope/qwen3-tts-flash"
# 旁白默认音色（DashScope 预设）；可被 project.json 顶层 narration_voice 或全局 setting 覆盖
# （与 video_backend 等同走顶层 key，非 settings 子字典）。
_DEFAULT_NARRATION_VOICE = "Cherry"
DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS = 3600
MIN_VIDEO_POLL_TIMEOUT_SECONDS = 60

# 参考上传副本的保守通用请求体上限（ArcReel 侧安全策略常量，非任一供应商的真实字节限；
# 被动 413 兜底负责自我纠正）。可经 per-provider 配置 key 覆盖。
# 与 lib/references/reference_compression.DEFAULT_* 数值一致（单测断言对齐）。
_DEFAULT_REFERENCE_TOTAL_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_REFERENCE_SINGLE_MAX_BYTES = 4 * 1024 * 1024

# 写入层校验为正整数的容量键（与 CapacityTable 的三条 lane 一一对应）。
# 其余 number 字段（image_rpm / video_rpm / request_gap）语义不同（允许小数），不在此列。
_MAX_WORKERS_KEYS = frozenset({"image_max_workers", "video_max_workers", "audio_max_workers"})
_MAX_WORKERS_CODE = "max_workers_must_be_positive_integer"


class ProviderConfigValueError(ValueError):
    """provider 配置值校验失败。

    携带 i18n message code + 渲染参数（key 而非译文），router 层 ``_t(exc.code, **exc.params)``
    泛化翻译为 user-facing 文案，无需感知具体校验规则。
    """

    def __init__(self, provider: str, key: str, value: str, *, code: str) -> None:
        super().__init__(f"invalid value for {provider}.{key}: {value!r} ({code})")
        self.provider = provider
        self.key = key
        self.value = value
        self.code = code
        self.params: dict[str, str] = {"field": key, "value": value}


@dataclass
class ProviderStatus:
    name: str
    display_name: str
    description: str
    status: Literal["ready", "unconfigured", "error"]
    media_types: list[str]
    capabilities: list[str]
    required_keys: list[str]
    configured_keys: list[str]
    missing_keys: list[str]
    models: dict[str, dict] | None = None  # model_id -> ModelInfo dict representation


class ConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self._provider_repo = ProviderConfigRepository(session)
        self._setting_repo = SystemSettingRepository(session)

    async def get_provider_config(self, provider: str) -> dict[str, str]:
        self._validate_provider(provider)
        return await self._provider_repo.get_all(provider)

    async def set_provider_config(
        self,
        provider: str,
        key: str,
        value: str,
        *,
        flush: bool = True,
    ) -> None:
        self._validate_provider(provider)
        self._validate_value(provider, key, value)
        if key in _MAX_WORKERS_KEYS:
            # 入库统一规范化为 ASCII 数字串（int() 接受 " 5 " / "+5" / "1_0" / 全角数字等
            # 非规范形态），保证任何读取方拿到的都是可直接解析、可在 number 输入框回显的值
            value = str(int(value))
        meta = PROVIDER_REGISTRY[provider]
        is_secret = key in meta.secret_keys
        await self._provider_repo.set(provider, key, value, is_secret=is_secret, flush=flush)

    async def delete_provider_config(
        self,
        provider: str,
        key: str,
        *,
        flush: bool = True,
    ) -> None:
        self._validate_provider(provider)
        await self._provider_repo.delete(provider, key, flush=flush)

    async def get_all_providers_status(self) -> list[ProviderStatus]:
        all_configured = await self._provider_repo.get_all_configured_keys_bulk()
        cred_repo = CredentialRepository(self._provider_repo.session)
        active_creds = await cred_repo.get_active_credentials_bulk()
        statuses = []
        for name, meta in PROVIDER_REGISTRY.items():
            has_active = name in active_creds
            configured = all_configured.get(name, [])
            # 无必填 key 的本地供应商（如 vieneu）无需凭证即就绪。
            if has_active or not meta.required_keys:
                status: Literal["ready", "unconfigured", "error"] = "ready"
                missing: list[str] = []
            else:
                status = "unconfigured"
                missing = list(meta.required_keys)
            # 先按 __dict__ 排除 pricing（其费率含 tuple 键，非 JSON 可序列化且响应不消费；
            # 用 __dict__ 而非 asdict 以免递归转换 pricing 后又被丢弃），再 deepcopy 其余可变容器
            # 字段（list/dict），避免返回值与全局 PROVIDER_REGISTRY 共享引用被调用方意外改写。
            models_dict = {
                mid: deepcopy({k: v for k, v in mi.__dict__.items() if k != "pricing"})
                for mid, mi in meta.models.items()
            }
            statuses.append(
                ProviderStatus(
                    name=name,
                    display_name=meta.display_name,
                    description=meta.description,
                    status=status,
                    media_types=list(meta.media_types),
                    capabilities=list(meta.capabilities),
                    required_keys=list(meta.required_keys),
                    configured_keys=configured,
                    missing_keys=missing,
                    models=models_dict,
                )
            )
        return statuses

    async def get_all_provider_configs(self) -> dict[str, dict[str, str]]:
        """Get raw config for ALL providers in a single query."""
        return await self._provider_repo.get_all_configs_bulk()

    async def get_provider_config_masked(self, provider: str) -> dict[str, dict]:
        self._validate_provider(provider)
        return await self._provider_repo.get_all_masked(provider)

    async def get_setting(self, key: str, default: str = "") -> str:
        return await self._setting_repo.get(key, default)

    async def get_all_settings(self) -> dict[str, str]:
        """Get all system settings in a single query."""
        return await self._setting_repo.get_all()

    async def set_setting(self, key: str, value: str) -> None:
        await self._setting_repo.set(key, value)

    async def get_video_poll_timeout_seconds(self) -> int:
        raw = await self._setting_repo.get(
            "video_poll_timeout_seconds",
            str(DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS),
        )
        return self.parse_video_poll_timeout_seconds(raw)

    @staticmethod
    def parse_video_poll_timeout_seconds(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
        return value if value >= MIN_VIDEO_POLL_TIMEOUT_SECONDS else DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS

    async def set_video_poll_timeout_seconds(self, value: int) -> None:
        if not is_int(value, minimum=MIN_VIDEO_POLL_TIMEOUT_SECONDS):
            raise ValueError(
                f"video poll timeout must be an integer of at least {MIN_VIDEO_POLL_TIMEOUT_SECONDS} seconds"
            )
        await self._setting_repo.set("video_poll_timeout_seconds", str(value))

    async def get_default_video_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_video_backend", _DEFAULT_VIDEO_BACKEND)
        return self._parse_backend(raw, _DEFAULT_VIDEO_BACKEND)

    async def get_default_text_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_text_backend", _DEFAULT_TEXT_BACKEND)
        return self._parse_backend(raw, _DEFAULT_TEXT_BACKEND)

    async def get_default_audio_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_audio_backend", _DEFAULT_AUDIO_BACKEND)
        return self._parse_backend(raw, _DEFAULT_AUDIO_BACKEND)

    async def get_h3_guide_preset(self) -> str:
        """H3 写作指南预设（全局 setting）。未设置/损坏值回落缺省预设。"""
        raw = await self._setting_repo.get(H3_GUIDE_PRESET_SETTING, "")
        return parse_h3_guide_preset(raw) or DEFAULT_H3_GUIDE_PRESET

    async def get_h3_guide_custom(self) -> str | None:
        """H3 自定义写作指南正文（全局 setting）。未设置/损坏值返回 None。"""
        return parse_h3_guide_custom(await self._setting_repo.get(H3_GUIDE_CUSTOM_SETTING, ""))

    async def get_h3_authoring_template(self) -> str | None:
        """H3 提示词编写模版的全局替换正文。未设置/损坏值返回 None。"""
        return parse_h3_authoring_template(await self._setting_repo.get(H3_AUTHORING_TEMPLATE_SETTING, ""))

    async def set_h3_authoring_template(self, body: str) -> None:
        """写入（空串 = 清除）全局替换正文；调用方负责先校验。"""
        await self._setting_repo.set(H3_AUTHORING_TEMPLATE_SETTING, body)

    async def get_narration_voice(self) -> str:
        # 空白 setting 视为未配置，与项目级覆盖的 strip 语义一致，避免空音色进 TTS 请求
        raw = await self._setting_repo.get("narration_voice", "")
        voice = raw.strip()
        return voice or _DEFAULT_NARRATION_VOICE

    async def get_narration_speed(self) -> float | None:
        """旁白语速（全局 setting）。未设置/损坏值返回 None，由各 audio backend 按自身能力处理。"""
        raw = await self._setting_repo.get("narration_speed", "")
        return self.parse_narration_speed(raw)

    @staticmethod
    def parse_narration_speed(raw: str) -> float | None:
        """把存储态语速字符串解析为有效正有限数；空白/非数值/非正/inf/nan 一律视为未设置。"""
        text = raw.strip()
        if not text:
            return None
        try:
            speed = float(text)
        except ValueError:
            return None
        if not math.isfinite(speed) or speed <= 0:
            return None
        return speed

    @staticmethod
    def _validate_provider(provider: str) -> None:
        if provider not in PROVIDER_REGISTRY:
            raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def _validate_value(provider: str, key: str, value: str) -> None:
        """容量键写入校验：正整数（≥1）。留空即不写该 key（回退默认），不会走到这里。

        0 不是合法用户输入——它仅作 CapacityTable 内部「不支持该 lane」哨兵，由 lane 投影
        在内存里产生，绝不写回。坏值一旦入库，容量 reload 只能逐 key 回退默认值，配置变更
        静默失效，因此在写入口拦下。
        """
        if key not in _MAX_WORKERS_KEYS:
            return
        try:
            parsed = int(value)
        except ValueError:
            raise ProviderConfigValueError(provider, key, value, code=_MAX_WORKERS_CODE) from None
        if parsed < 1:
            raise ProviderConfigValueError(provider, key, value, code=_MAX_WORKERS_CODE)

    @staticmethod
    def _parse_backend(raw: str, fallback: str) -> tuple[str, str]:
        if "/" in raw:
            provider_id, model_id = raw.split("/", 1)
            return provider_id, model_id
        parts = fallback.split("/", 1)
        return parts[0], parts[1]


async def read_video_poll_timeout_seconds() -> int:
    """自开一个 session 读一次全局视频轮询超时。

    worker 派发与端点测试连接共用这一份：两条路径都在「即将进入一次真实生成」的时点读一次，
    各自另写一遍就会在超时含义上分叉。口径是「派发时读一次」，不是每次轮询各读各的。
    """
    from lib.db import safe_session_factory

    async with safe_session_factory() as session:
        return await ConfigService(session).get_video_poll_timeout_seconds()
