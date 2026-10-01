"""VieneuAudioBackend — 本地越南语 TTS（VieNeu-TTS v3 Turbo）后端。

复用 OpenAI 兼容 ``/v1/audio/speech`` 协议（VieNeu 自带的 ``apps/openai_speech`` 服务），
差异只有三点：无需 API Key；服务由 :mod:`lib.speech.vieneu_service` 按需托管拉起；
音色目录带性别 / 区域标注。

服务端 ``SpeechRequest`` 的 ``response_format`` 仅 ``pcm`` / ``wav``，且不含 ``speed``，
故固定请求 wav 且不下发语速；``/v1/voices`` 返回
``{"object": "list", "data": [{"id", "name", "description", "gender", "featured", "aliases"}]}``
（出自 VieNeu-TTS 仓库 apps/openai_speech.py），``voice`` 参数使用音色 ``name``（README 示例 ``"Mai Anh"``）。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from lib.backends.audio_backends.base import AudioSynthesisRequest, AudioSynthesisResult, VoiceOption
from lib.backends.audio_backends.openai import OpenAIAudioBackend
from lib.backends.openai_shared import create_openai_client
from lib.backends.providers import PROVIDER_VIENEU
from lib.speech.vieneu_service import DEFAULT_HOST, DEFAULT_PORT, get_vieneu_manager
from lib.speech.wav_header import finalize_streaming_wav

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "vieneu-tts-v3-turbo"
DEFAULT_BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1"
# 本地服务默认不鉴权（VIENEU_API_KEY 未设置时开放）；OpenAI SDK 要求非空 key，给占位值。
_PLACEHOLDER_API_KEY = "not-needed"
_VOICE_LIST_TIMEOUT_SECONDS = 2.0

_NORTH = "north"
_CENTRAL = "central"
_SOUTH = "south"

# VieNeu-TTS v3 Turbo 的 25 个预设音色：性别与区域取自服务端 ``/v1/voices`` 的实测返回；
# 服务在线时仍以服务端目录为准，这份内置目录只在服务未运行时让界面也能按性别选音色。
_VOICE_CATALOG: tuple[VoiceOption, ...] = (
    VoiceOption(id="Adam bựa", label="Adam bựa", gender="male", locale="vi", region=_NORTH),
    VoiceOption(id="Trúc Ly", label="Trúc Ly", gender="female", locale="vi", region=_NORTH),
    VoiceOption(id="Thiện Minh", label="Thiện Minh", gender="male", locale="vi", region=_NORTH),
    VoiceOption(id="Mai Anh", label="Mai Anh", gender="female", locale="vi", region=_NORTH),
    VoiceOption(id="Hải Đăng", label="Hải Đăng", gender="male", locale="vi", region=_NORTH),
    VoiceOption(id="Thùy Dung", label="Thùy Dung", gender="female", locale="vi", region=_SOUTH),
    VoiceOption(id="Thiền Tâm Đức", label="Thiền Tâm Đức", gender="male", locale="vi", region=_NORTH),
    VoiceOption(id="Ngọc Huyền", label="Ngọc Huyền", gender="female", locale="vi", region=_NORTH),
    VoiceOption(id="Quang Sơn", label="Quang Sơn", gender="male", locale="vi", region=_CENTRAL),
    VoiceOption(id="Ngọc Trân", label="Ngọc Trân", gender="female", locale="vi", region=_CENTRAL),
    VoiceOption(id="Minh Đức", label="Minh Đức", gender="male", locale="vi", region=_NORTH),
    VoiceOption(id="Phạm Tuyên", label="Phạm Tuyên", gender="male", locale="vi", region=_NORTH),
    VoiceOption(id="Thái Sơn", label="Thái Sơn", gender="male", locale="vi", region=_SOUTH),
    VoiceOption(id="Xuân Vĩnh", label="Xuân Vĩnh", gender="male", locale="vi", region=_NORTH),
    VoiceOption(id="Thanh Bình", label="Thanh Bình", gender="male", locale="vi", region=_NORTH),
    VoiceOption(id="Ngọc Linh", label="Ngọc Linh", gender="female", locale="vi", region=_NORTH),
    VoiceOption(id="Đoan Trang", label="Đoan Trang", gender="female", locale="vi", region=_NORTH),
    VoiceOption(id="Thục Đoan", label="Thục Đoan", gender="female", locale="vi", region=_SOUTH),
    VoiceOption(id="Minh Triết", label="Minh Triết", gender="male", locale="vi", region=_SOUTH),
    VoiceOption(id="Mỹ Duyên", label="Mỹ Duyên", gender="female", locale="vi", region=_SOUTH),
    VoiceOption(id="Quỳnh Anh", label="Quỳnh Anh", gender="female", locale="vi", region=_NORTH),
    VoiceOption(id="Đức Trí", label="Đức Trí", gender="male", locale="vi", region=_SOUTH),
    VoiceOption(id="Kim Thanh", label="Kim Thanh", gender="female", locale="vi", region=_SOUTH),
    VoiceOption(id="Adam", label="Adam", gender="male", locale="vi", region=_SOUTH),
    VoiceOption(id="Quốc Tuấn", label="Quốc Tuấn", gender="male", locale="vi", region=_NORTH),
)
_CATALOG_BY_ID = {v.id: v for v in _VOICE_CATALOG}

_GENDER_ALIASES: dict[str, str] = {
    "male": "male",
    "m": "male",
    "nam": "male",
    "female": "female",
    "f": "female",
    "nu": "female",
    "nữ": "female",
}


def static_voice_catalog() -> list[VoiceOption]:
    """内置的已核实音色目录（服务不可用时的回落）。"""
    return list(_VOICE_CATALOG)


def parse_server_voices(payload: Any) -> list[VoiceOption]:
    """解析 ``GET /v1/voices``；对形状宽松：兼容 ``{"data": [...]}`` / 裸数组 / 字符串元素。

    服务端缺性别 / 区域时，用内置目录里同名音色的标注补齐。
    """
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    voices: list[VoiceOption] = []
    for item in items:
        if isinstance(item, str):
            name, gender_raw, region = item, None, None
        elif isinstance(item, dict):
            name = item.get("name") or item.get("id")
            gender_raw = item.get("gender")
            region = item.get("region")
        else:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        known = _CATALOG_BY_ID.get(name)
        gender = _GENDER_ALIASES.get(gender_raw.strip().lower()) if isinstance(gender_raw, str) else None
        voices.append(
            VoiceOption(
                id=name,
                label=name,
                gender=gender or (known.gender if known else None),  # type: ignore[arg-type]
                locale="vi",
                region=(region.strip().lower() if isinstance(region, str) and region.strip() else None)
                or (known.region if known else None),
            )
        )
    return voices


def _voices_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/voices"


async def fetch_server_voices(base_url: str) -> list[VoiceOption] | None:
    """向运行中的服务取音色；不可达 / 响应无效返回 None。"""
    try:
        async with httpx.AsyncClient(timeout=_VOICE_LIST_TIMEOUT_SECONDS) as client:
            response = await client.get(_voices_url(base_url))
        response.raise_for_status()
        voices = parse_server_voices(response.json())
    except (httpx.HTTPError, ValueError):
        return None
    return voices or None


class VieneuAudioBackend(OpenAIAudioBackend):
    """VieNeu-TTS 本地后端：显式 ``base_url`` 时直连外部服务，否则走托管服务（首次合成自动拉起）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._explicit_base_url = (base_url or "").strip() or None
        self._api_key = api_key or _PLACEHOLDER_API_KEY
        self._current_base_url = self._explicit_base_url or DEFAULT_BASE_URL
        super().__init__(
            api_key=self._api_key,
            base_url=self._current_base_url,
            model=model or DEFAULT_MODEL,
            provider_name=PROVIDER_VIENEU,
        )

    def list_voices(self) -> list[VoiceOption]:
        # 同步入口：仅在服务已在线时能取到服务端目录，否则回落内置目录（不触发拉起）。
        try:
            response = httpx.get(_voices_url(self._current_base_url), timeout=_VOICE_LIST_TIMEOUT_SECONDS)
            response.raise_for_status()
            voices = parse_server_voices(response.json())
        except (httpx.HTTPError, ValueError):
            voices = []
        return voices or static_voice_catalog()

    def _response_format(self, output_path: Path) -> str:
        return "wav"

    def _use_base_url(self, base_url: str) -> None:
        if base_url != self._current_base_url:
            self._client = create_openai_client(api_key=self._api_key, base_url=base_url, max_retries=0)
            self._current_base_url = base_url

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # 服务端 SpeechRequest 无 speed 字段；language_type / language 同样无对应参数（语种随输入文本）。
        if request.speed is not None:
            logger.debug("VieNeu 不支持语速参数，忽略 speed=%s", request.speed)
            request = replace(request, speed=None)
        if self._explicit_base_url is not None:
            result = await super().synthesize(request)
        else:
            async with get_vieneu_manager().lease() as base_url:
                self._use_base_url(base_url)
                result = await super().synthesize(request)
        # 服务端边合成边输出，WAV 头长度未知；回填后浏览器与 wave 模块才读得出正确时长。
        finalize_streaming_wav(result.output_path)
        return result
