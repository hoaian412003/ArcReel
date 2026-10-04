"""RunningHub all-in-one GPT Image 2 AI App backend."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import httpx

from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.backend_runtime import submit_post
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    download_image_to_path,
)
from lib.backends.providers import PROVIDER_RUNNINGHUB
from lib.backends.runninghub_usage import RunningHubUsage, parse_runninghub_usage
from lib.infra.poll_sync_hook import fire_provider_sync

DEFAULT_BASE_URL = "https://www.runninghub.ai"
_QUERY_URL = "https://www.runninghub.ai/openapi/v2/query"
_APP_ID = "2104532059371909121"
DEFAULT_MODEL = f"ai-app-{_APP_ID}"
_UPLOAD_ENDPOINT = "/task/openapi/upload"
_SUBMIT_ENDPOINT = "/task/openapi/ai-app/run"
_PROMPT_NODE = "1"
_IMAGE_NODES = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "25")
_SETTINGS_NODE = "12"
_MAX_REFERENCE_IMAGES = 10
_SUPPORTED_ASPECT_RATIOS = {
    "1:1",
    "1:2",
    "1:3",
    "2:1",
    "2:3",
    "3:1",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "9:21",
    "16:9",
    "21:9",
}
_SUPPORTED_RESOLUTIONS = {"1k", "2k", "4k"}


def _api_error(payload: object, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    message = payload.get("msg") or payload.get("message") or payload.get("errorMessage")
    return str(message or fallback)


def _normalize_base_url(base_url: str | None) -> str:
    return (base_url or DEFAULT_BASE_URL).rstrip("/")


class RunningHubImageBackend:
    """Run the all-in-one GPT Image 2 AI App on RunningHub."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120,
        poll_interval: float = 5,
        poll_timeout: float = 600,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("RunningHub API key is required")
        self._api_key = api_key.strip()
        self._model = model or DEFAULT_MODEL
        if self._model != DEFAULT_MODEL:
            raise ValueError(f"Unsupported RunningHub image model: {self._model}")
        self._base_url = _normalize_base_url(base_url)
        self._http_timeout = http_timeout
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    @property
    def name(self) -> str:
        return PROVIDER_RUNNINGHUB

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    @property
    def max_reference_images(self) -> int:
        return _MAX_REFERENCE_IMAGES

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        refs = list(request.reference_images)
        if len(refs) > _MAX_REFERENCE_IMAGES:
            refs = refs[:_MAX_REFERENCE_IMAGES]

        async with artifact_http_client(timeout=self._http_timeout) as client:
            uploaded = await asyncio.gather(*(self._upload_image(client, Path(ref.path)) for ref in refs))
            task_id = await self._submit(client, self._node_info(request, uploaded), request)
            result_url, usage = await self._poll(client, task_id, request)

        await download_image_to_path(result_url, request.output_path)
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_RUNNINGHUB,
            model=self._model,
            image_uri=result_url,
            provider_coins=usage.coins,
            provider_cost_usd=usage.third_party_usd,
        )

    async def _upload_image(self, client: httpx.AsyncClient, path: Path) -> str:
        if not await asyncio.to_thread(path.is_file):
            raise ImageCapabilityError(
                "image_reference_images_unreadable",
                model=self._model,
                names=path.name or str(path),
            )
        contents = await asyncio.to_thread(path.read_bytes)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = await client.post(
            f"{self._base_url}{_UPLOAD_ENDPOINT}",
            data={"apiKey": self._api_key, "fileType": "input"},
            files={"file": (path.name, contents, mime)},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        filename = data.get("fileName") if isinstance(data, dict) else None
        if payload.get("code") != 0 or not isinstance(filename, str) or not filename:
            raise RuntimeError(_api_error(payload, "RunningHub image upload failed"))
        return filename

    @staticmethod
    def _node_info(request: ImageGenerationRequest, uploaded: list[str]) -> list[dict[str, str]]:
        nodes = [{"nodeId": _PROMPT_NODE, "fieldName": "prompt", "fieldValue": request.prompt}]
        nodes.extend(
            {
                "nodeId": node_id,
                "fieldName": "image",
                "fieldValue": uploaded[index] if index < len(uploaded) else "None",
            }
            for index, node_id in enumerate(_IMAGE_NODES)
        )
        nodes.extend(
            [
                {
                    "nodeId": _SETTINGS_NODE,
                    "fieldName": "resolution",
                    "fieldValue": RunningHubImageBackend._resolution(request.image_size),
                },
                {
                    "nodeId": _SETTINGS_NODE,
                    "fieldName": "aspectRatio",
                    "fieldValue": (
                        request.aspect_ratio if request.aspect_ratio in _SUPPORTED_ASPECT_RATIOS else "empty"
                    ),
                },
            ]
        )
        return nodes

    async def _submit(
        self,
        client: httpx.AsyncClient,
        nodes: list[dict[str, str]],
        request: ImageGenerationRequest,
    ) -> str:
        response = await submit_post(
            lambda: client.post(
                f"{self._base_url}{_SUBMIT_ENDPOINT}",
                json={"apiKey": self._api_key, "webappId": int(_APP_ID), "nodeInfoList": nodes},
            ),
            provider=PROVIDER_RUNNINGHUB,
        )
        data = response.json()
        await self._record(request, "submit", data)
        result = data.get("data", {}) if isinstance(data, dict) else {}
        task_id = result.get("taskId") if isinstance(result, dict) else None
        code = data.get("code") if isinstance(data, dict) else None
        if code != 0 or not isinstance(task_id, (str, int)) or not str(task_id).strip():
            raise RuntimeError(_api_error(data, "RunningHub image submit failed"))
        return str(task_id)

    async def _poll(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: ImageGenerationRequest,
    ) -> tuple[str, RunningHubUsage]:
        last_status = "unknown"
        try:
            async with asyncio.timeout(self._poll_timeout):
                while True:
                    response = await client.post(
                        _QUERY_URL,
                        headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                        json={"taskId": task_id},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    await self._record(request, "poll", payload)
                    status = str(payload.get("status", "")).upper() if isinstance(payload, dict) else ""
                    last_status = status or "unknown"
                    await fire_provider_sync(label="RunningHub", status=last_status)
                    if status == "SUCCESS":
                        results = payload.get("results")
                        if isinstance(results, list):
                            for item in results:
                                if isinstance(item, dict):
                                    url = item.get("url") or item.get("outputUrl")
                                    if isinstance(url, str) and url:
                                        return url, parse_runninghub_usage(payload)
                        raise RuntimeError("RunningHub image task succeeded without an image URL")
                    if status == "FAILED":
                        raise RuntimeError(_api_error(payload, "RunningHub image task failed"))
                    await asyncio.sleep(self._poll_interval)
        except TimeoutError as exc:
            raise RuntimeError(
                f"RunningHub image task {task_id} did not finish within "
                f"{self._poll_timeout:g}s (last status: {last_status})"
            ) from exc

    @staticmethod
    def _resolution(image_size: str | None) -> str:
        normalized = (image_size or "1k").lower()
        return normalized if normalized in _SUPPORTED_RESOLUTIONS else "1k"

    @staticmethod
    async def _record(request: ImageGenerationRequest, stage: str, payload: object) -> None:
        if request.on_provider_response is not None:
            await request.on_provider_response(stage, payload)  # type: ignore[arg-type]
