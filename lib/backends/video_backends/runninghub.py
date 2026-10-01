"""RunningHub AI App 视频后端。

当前内置模型对应 RunningHub AI App ``2104162779616120834``：一个 MiniMax H3
Ref2VA workflow，接收提示词、1–15 秒时长和最多九张有序参考图。
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import ProviderJobIdPersistenceMixin, download_resumable_video, submit_post
from lib.backends.providers import PROVIDER_RUNNINGHUB
from lib.backends.runninghub_usage import parse_runninghub_usage
from lib.backends.video_backend_contract import (
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.runninghub.ai"
_QUERY_URL = "https://www.runninghub.ai/openapi/v2/query"
_APP_ID = "2104162779616120834"
_MODEL = f"ai-app-{_APP_ID}"
_IMAGE_NODES = ("139", "147", "159", "160", "222", "287", "290", "292", "294")
_PROMPT_NODE = "138"
_DURATION_NODE = "132"
_POLL_INTERVAL_SECONDS = 5
_HTTP_TIMEOUT_SECONDS = 120


def _api_error(payload: object, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    message = payload.get("msg") or payload.get("message") or payload.get("errorMessage")
    return str(message or fallback)


class RunningHubVideoBackend(ProviderJobIdPersistenceMixin):
    """Chạy MiniMax H3 storyboard AI App trên RunningHub."""

    DEFAULT_MODEL = _MODEL

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = _HTTP_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("RunningHub API key is required")
        self._api_key = api_key.strip()
        self._model = model or self.DEFAULT_MODEL
        if self._model != self.DEFAULT_MODEL:
            raise ValueError(f"Unsupported RunningHub video model: {self._model}")
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_RUNNINGHUB

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        if model != _MODEL:
            raise ValueError(f"Unsupported RunningHub video model: {model}")
        return VideoCapabilities(
            text_to_video=False,
            max_reference_images=len(_IMAGE_NODES),
            audio_track=VideoAudioMode.ALWAYS_ON,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        images = [Path(path) for path in (request.reference_images or [])]
        if not images:
            raise VideoCapabilityError("video_reference_images_required", model=self._model)
        if len(images) > len(_IMAGE_NODES):
            raise VideoCapabilityError(
                "video_reference_images_exceeded",
                model=self._model,
                count=len(images),
                limit=len(_IMAGE_NODES),
            )
        if not 1 <= request.duration_seconds <= 15:
            raise VideoCapabilityError(
                "video_duration_not_supported",
                model=self._model,
                duration=request.duration_seconds,
                supported="1-15",
            )

        async with artifact_http_client(timeout=self._http_timeout) as client:
            uploaded = await asyncio.gather(*(self._upload_image(client, path) for path in images))
            nodes = self._node_info(request, uploaded)
            task_id = await self._submit(client, nodes, request)
            await self._persist_provider_job_id(
                request,
                task_id,
                provider=PROVIDER_RUNNINGHUB,
                endpoint=self._base_url,
            )
            return await self._poll_and_download(client, task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        async with artifact_http_client(timeout=self._http_timeout) as client:
            return await self._poll_and_download(client, job_id, request)

    async def _upload_image(self, client: httpx.AsyncClient, path: Path) -> str:
        if not await asyncio.to_thread(path.is_file):
            raise VideoCapabilityError(
                "video_reference_images_unreadable",
                model=self._model,
                names=path.name or str(path),
            )
        contents = await asyncio.to_thread(path.read_bytes)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = await client.post(
            f"{self._base_url}/task/openapi/upload",
            data={"apiKey": self._api_key, "fileType": "input"},
            files={"file": (path.name, contents, mime)},
        )
        response.raise_for_status()
        payload = response.json()
        filename = payload.get("data", {}).get("fileName") if isinstance(payload, dict) else None
        if payload.get("code") != 0 or not isinstance(filename, str) or not filename:
            raise RuntimeError(_api_error(payload, "RunningHub image upload failed"))
        return filename

    @staticmethod
    def _node_info(request: VideoGenerationRequest, uploaded: list[str]) -> list[dict[str, str]]:
        nodes = [
            {"nodeId": _PROMPT_NODE, "fieldName": "value", "fieldValue": request.prompt},
            {"nodeId": _DURATION_NODE, "fieldName": "value", "fieldValue": str(request.duration_seconds)},
        ]
        nodes.extend(
            {
                "nodeId": node_id,
                "fieldName": "image",
                "fieldValue": uploaded[index] if index < len(uploaded) else "None",
            }
            for index, node_id in enumerate(_IMAGE_NODES)
        )
        return nodes

    async def _record(self, request: VideoGenerationRequest, stage: str, payload: object) -> None:
        if request.on_provider_response is not None:
            await request.on_provider_response(stage, payload)  # type: ignore[arg-type]

    async def _submit(
        self,
        client: httpx.AsyncClient,
        nodes: list[dict[str, str]],
        request: VideoGenerationRequest,
    ) -> str:
        response = await submit_post(
            lambda: client.post(
                f"{self._base_url}/task/openapi/ai-app/run",
                json={"apiKey": self._api_key, "webappId": int(_APP_ID), "nodeInfoList": nodes},
            ),
            provider=PROVIDER_RUNNINGHUB,
            request=request,
        )
        payload = response.json()
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        task_id = data.get("taskId") if isinstance(data, dict) else None
        if payload.get("code") != 0 or not isinstance(task_id, (str, int)):
            raise RuntimeError(_api_error(payload, "RunningHub AI App submit failed"))
        return str(task_id)

    async def _poll(self, client: httpx.AsyncClient, task_id: str, request: VideoGenerationRequest) -> dict[str, Any]:
        response = await client.post(
            _QUERY_URL,
            json={"taskId": task_id},
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        await self._record(request, "poll", payload)
        if not isinstance(payload, dict):
            raise RuntimeError("RunningHub poll returned invalid JSON")
        return payload

    async def _poll_and_download(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
    ) -> VideoGenerationResult:
        async with asyncio.timeout(request.poll_timeout_seconds):
            while True:
                payload = await self._poll(client, task_id, request)
                status = str(payload.get("status", "")).upper()
                if status == "SUCCESS":
                    break
                if status == "FAILED":
                    raise RuntimeError(_api_error(payload, "RunningHub AI App task failed"))
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        results = payload.get("results")
        if not isinstance(results, list):
            raise RuntimeError("RunningHub task succeeded without results")
        video_url = next(
            (
                item.get("url") or item.get("outputUrl")
                for item in results
                if isinstance(item, dict) and (item.get("url") or item.get("outputUrl"))
            ),
            None,
        )
        if not isinstance(video_url, str):
            raise RuntimeError("RunningHub task succeeded without a video URL")
        await download_resumable_video(video_url, request.output_path, label="RunningHub")
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_RUNNINGHUB,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_url,
            task_id=task_id,
            generate_audio=True,
            provider_coins=parse_runninghub_usage(payload).coins,
            provenance={"runninghub_webapp_id": _APP_ID},
        )
