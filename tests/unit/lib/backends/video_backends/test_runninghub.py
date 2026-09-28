from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx
import pytest

from lib.backends.video_backend_contract import VideoAudioMode, VideoGenerationRequest
from lib.backends.video_backends.runninghub import RunningHubVideoBackend


def test_capabilities_match_storyboard_ai_app():
    caps = RunningHubVideoBackend.video_capabilities_for_model("ai-app-2104162779616120834")

    assert caps.text_to_video is False
    assert caps.max_reference_images == 9
    assert caps.audio_track is VideoAudioMode.ALWAYS_ON


@pytest.mark.asyncio
async def test_generate_uploads_ordered_images_and_runs_ai_app(monkeypatch, tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    captured_submit: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/task/openapi/upload":
            source = "first" if b"first" in request.content else "second"
            return httpx.Response(200, json={"code": 0, "data": {"fileName": f"uploaded-{source}.png"}})
        if request.url.path == "/task/openapi/ai-app/run":
            captured_submit.update(json.loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {"taskId": "task-1"}})
        if request.url.path == "/openapi/v2/query":
            return httpx.Response(
                200,
                json={"status": "SUCCESS", "results": [{"url": "https://cdn.example/final.mp4"}]},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    @asynccontextmanager
    async def fake_http_client(**_kwargs):
        yield client

    downloaded: dict = {}

    async def fake_download(url, output_path, *, label):
        downloaded.update(url=url, output_path=output_path, label=label)

    monkeypatch.setattr("lib.backends.video_backends.runninghub.artifact_http_client", fake_http_client)
    monkeypatch.setattr("lib.backends.video_backends.runninghub.download_resumable_video", fake_download)

    output = tmp_path / "result.mp4"
    result = await RunningHubVideoBackend(api_key="rh-key").generate(
        VideoGenerationRequest(
            prompt="four cinematic shots",
            output_path=output,
            duration_seconds=12,
            reference_images=[first, second],
        )
    )
    await client.aclose()

    assert captured_submit["webappId"] == 2104162779616120834
    nodes = {(item["nodeId"], item["fieldName"]): item["fieldValue"] for item in captured_submit["nodeInfoList"]}
    assert nodes[("138", "value")] == "four cinematic shots"
    assert nodes[("132", "value")] == "12"
    assert nodes[("139", "image")] == "uploaded-first.png"
    assert nodes[("147", "image")] == "uploaded-second.png"
    assert nodes[("159", "image")] == "None"
    assert downloaded == {"url": "https://cdn.example/final.mp4", "output_path": output, "label": "RunningHub"}
    assert result.task_id == "task-1"
    assert result.provenance == {"runninghub_webapp_id": "2104162779616120834"}
