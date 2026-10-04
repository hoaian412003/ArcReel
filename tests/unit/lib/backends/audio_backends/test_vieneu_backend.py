"""VieneuAudioBackend：注册 / 装配 / 合成请求形状 / 音色目录回落。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from lib.backends.audio_backends import AudioCapability, AudioSynthesisRequest, create_backend, get_registered_backends
from lib.backends.audio_backends import vieneu as vieneu_module
from lib.backends.audio_backends.vieneu import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    VieneuAudioBackend,
    fetch_server_voices,
    parse_server_voices,
    static_voice_catalog,
)
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.config.registry import PROVIDER_REGISTRY
from lib.speech.vieneu_service import VieneuServiceError
from tests.http_capture import capture_http, only_request, request_json


class _StubManager:
    """最小管理器替身：只提供 lease()，产出固定 base_url（真实管理器另有状态机测试）。"""

    def __init__(self, base_url: str, *, error: Exception | None = None) -> None:
        self.base_url = base_url
        self.error = error
        self.leases = 0

    @asynccontextmanager
    async def lease(self):
        if self.error:
            raise self.error
        self.leases += 1
        yield self.base_url


class TestRegistryWiring:
    def test_registered_in_audio_registry(self):
        assert "vieneu" in get_registered_backends()

    def test_provider_meta_is_keyless_free_and_runs_four_videos_at_a_time(self):
        meta = PROVIDER_REGISTRY["vieneu"]
        assert meta.required_keys == []
        assert meta.secret_keys == []
        assert "base_url" in meta.optional_keys
        # 配音每个视频一个任务，同时最多 4 个在跑；服务端一次只合成一路，其余在其队列里排队
        assert meta.default_concurrency == {"audio": 4}
        assert meta.media_types == ["audio"]
        model = meta.models[DEFAULT_MODEL]
        assert model.default is True
        assert model.capabilities == ["text_to_speech"]

    def test_pricing_is_zero_through_the_ledger_path(self):
        from lib.billing.pricing.strategies import PricingParams, calculate_pricing

        pricing = PROVIDER_REGISTRY["vieneu"].models[DEFAULT_MODEL].pricing
        assert pricing is not None
        amount, _currency = calculate_pricing(
            pricing, PricingParams(call_type="audio", model=DEFAULT_MODEL, usage_tokens=123456)
        )
        assert amount == 0

    def test_spec_builds_backend_without_api_key(self):
        spec = get_provider_spec("vieneu", "audio")
        backend = spec.build_backend(
            LoadedConfig(credentials={}, provider_meta=PROVIDER_REGISTRY["vieneu"], rate_limiter=None), DEFAULT_MODEL
        )
        assert isinstance(backend, VieneuAudioBackend)
        assert backend.name == "vieneu"
        assert backend.model == DEFAULT_MODEL
        assert backend.capabilities == {AudioCapability.TEXT_TO_SPEECH}

    def test_create_backend_defaults(self):
        backend = create_backend("vieneu")
        assert backend.model == DEFAULT_MODEL


class TestSynthesize:
    async def test_explicit_base_url_skips_managed_service(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        stub = _StubManager("http://unused/v1", error=AssertionError("不应启动托管服务"))
        monkeypatch.setattr(vieneu_module, "get_vieneu_manager", lambda: stub)
        out = tmp_path / "o.wav"
        with capture_http() as router:
            route = router.post("http://tts.lan:9000/v1/audio/speech").respond(200, content=b"RIFFwav")
            backend = VieneuAudioBackend(base_url="http://tts.lan:9000/v1")
            result = await backend.synthesize(
                AudioSynthesisRequest(text="Xin chào", output_path=out, voice="Mai Anh", speed=1.2, language="vi")
            )

        body = request_json(only_request(route))
        assert body["model"] == DEFAULT_MODEL
        assert body["input"] == "Xin chào"
        assert body["voice"] == "Mai Anh"
        assert body["response_format"] == "wav"
        assert "speed" not in body, "服务端 SpeechRequest 无 speed 字段"
        assert out.read_bytes() == b"RIFFwav"
        assert (result.provider, result.characters) == ("vieneu", len("Xin chào"))

    async def test_managed_path_leases_service_and_targets_its_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        stub = _StubManager("http://127.0.0.1:8123/v1")
        monkeypatch.setattr(vieneu_module, "get_vieneu_manager", lambda: stub)
        out = tmp_path / "o.mp3"  # 扩展名不影响请求格式：服务端仅支持 wav / pcm
        with capture_http() as router:
            route = router.post("http://127.0.0.1:8123/v1/audio/speech").respond(200, content=b"RIFFwav")
            backend = VieneuAudioBackend()
            await backend.synthesize(AudioSynthesisRequest(text="a", output_path=out, voice="Hải Đăng"))

        assert only_request(route) is not None
        assert request_json(route.calls.last.request)["response_format"] == "wav"
        assert stub.leases == 1

    async def test_service_start_failure_propagates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            vieneu_module, "get_vieneu_manager", lambda: _StubManager("x", error=VieneuServiceError("没起来"))
        )
        backend = VieneuAudioBackend()
        with pytest.raises(VieneuServiceError, match="没起来"):
            await backend.synthesize(AudioSynthesisRequest(text="a", output_path=tmp_path / "o.wav", voice="v"))
        assert not (tmp_path / "o.wav").exists()


class TestVoices:
    def test_static_catalog_is_annotated_and_unique(self):
        voices = static_voice_catalog()
        assert len({v.id for v in voices}) == len(voices)
        by_id = {v.id: v for v in voices}
        assert by_id["Hải Đăng"].gender == "male"
        assert by_id["Mai Anh"].gender == "female"
        assert by_id["Quang Sơn"].region == "central"
        assert all(v.gender in ("male", "female") for v in voices)

    def test_list_voices_falls_back_to_catalog_when_server_down(self):
        with capture_http() as router:
            router.get(f"{DEFAULT_BASE_URL}/voices").mock(side_effect=httpx.ConnectError("down"))
            voices = VieneuAudioBackend().list_voices()
        assert voices == static_voice_catalog()

    def test_list_voices_uses_server_directory_when_up(self):
        payload = {"object": "list", "data": [{"id": "x1", "name": "Minh Đức", "gender": "male"}]}
        with capture_http() as router:
            router.get(f"{DEFAULT_BASE_URL}/voices").respond(200, json=payload)
            voices = VieneuAudioBackend().list_voices()
        assert [(v.id, v.gender) for v in voices] == [("Minh Đức", "male")]

    def test_parse_normalizes_gender_and_merges_catalog_region(self):
        voices = parse_server_voices(
            {"data": [{"name": "Quang Sơn", "gender": "Nam"}, {"id": "Ngọc Linh", "gender": "female"}, "Thái Sơn", {}]}
        )
        by_id = {v.id: v for v in voices}
        assert by_id["Quang Sơn"].gender == "male"
        assert by_id["Quang Sơn"].region == "central"  # 服务端未给区域时取内置标注
        assert by_id["Ngọc Linh"].gender == "female"
        # 服务端没给性别时取内置目录里已核实的标注
        assert by_id["Thái Sơn"].gender == "male"
        assert by_id["Thái Sơn"].region == "south"
        assert len(voices) == 3

    def test_parse_rejects_unknown_shape(self):
        assert parse_server_voices({"error": "x"}) == []

    async def test_fetch_server_voices_none_on_failure(self):
        with capture_http() as router:
            router.get("http://h/v1/voices").respond(500)
            assert await fetch_server_voices("http://h/v1") is None


class _FakeClock:
    """记录等了多久的假时钟：重试测试不真等。"""

    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self._now += delay


def _busy() -> httpx.Response:
    return httpx.Response(429, json={"error": {"message": "server busy: queue full", "type": "invalid_request_error"}})


class TestBusyServer:
    """服务端一次只合成一路，其余请求排队；队列满时返回 429。几个视频同时配音时应当等，而不是整条任务失败。"""

    @staticmethod
    def _backend(clock: _FakeClock, *, backoff: tuple[int, ...] = (2, 3, 5)) -> VieneuAudioBackend:
        return VieneuAudioBackend(
            base_url="http://vieneu:8000/v1", busy_backoff_seconds=backoff, clock=clock, jitter=lambda lo, hi: 0.0
        )

    @staticmethod
    def _request(tmp_path: Path) -> AudioSynthesisRequest:
        return AudioSynthesisRequest(text="Xin chào", output_path=tmp_path / "o.wav", voice="Mai Anh")

    async def test_waits_and_tries_again_while_the_server_says_busy(self, tmp_path: Path):
        clock = _FakeClock()
        with capture_http() as router:
            route = router.post("http://vieneu:8000/v1/audio/speech")
            route.side_effect = [_busy(), _busy(), httpx.Response(200, content=b"RIFFaudio")]
            result = await self._backend(clock).synthesize(self._request(tmp_path))

        assert route.call_count == 3
        assert (tmp_path / "o.wav").read_bytes() == b"RIFFaudio"
        assert result.provider == "vieneu"
        assert clock.sleeps == [2.0, 3.0]

    async def test_gives_up_once_the_waiting_budget_is_spent(self, tmp_path: Path):
        from openai import RateLimitError

        clock = _FakeClock()
        with capture_http() as router:
            route = router.post("http://vieneu:8000/v1/audio/speech")
            route.side_effect = lambda request: _busy()
            with pytest.raises(RateLimitError):
                await self._backend(clock, backoff=(1, 1)).synthesize(self._request(tmp_path))

        assert route.call_count == 3  # 首次 + 两次重试
        assert clock.sleeps == [1.0, 1.0]

    async def test_other_transient_errors_get_only_a_few_quick_retries(self, tmp_path: Path):
        clock = _FakeClock()
        with capture_http() as router:
            route = router.post("http://vieneu:8000/v1/audio/speech")
            route.side_effect = lambda request: httpx.Response(500, json={"error": {"message": "boom"}})
            with pytest.raises(Exception, match="boom"):
                await self._backend(clock, backoff=(1,) * 20).synthesize(self._request(tmp_path))

        assert route.call_count == 4  # 首次 + 3 次快速重试，不会跟着排队等 20 次
        assert len(clock.sleeps) == 3

    async def test_a_client_error_is_not_retried(self, tmp_path: Path):
        clock = _FakeClock()
        with capture_http() as router:
            route = router.post("http://vieneu:8000/v1/audio/speech")
            route.side_effect = lambda request: httpx.Response(
                400, json={"error": {"message": "bad voice", "type": "invalid_request_error"}}
            )
            with pytest.raises(Exception, match="bad voice"):
                await self._backend(clock).synthesize(self._request(tmp_path))

        assert route.call_count == 1
        assert clock.sleeps == []
