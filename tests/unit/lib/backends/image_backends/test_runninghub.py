from __future__ import annotations

import json

import httpx
import pytest
import respx

from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.backends.image_backends.runninghub import RunningHubImageBackend


def test_capabilities_cover_text_and_image_generation():
    backend = RunningHubImageBackend(api_key="rh-key")

    assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
    assert backend.max_reference_images == 10


@pytest.mark.asyncio
async def test_generate_uploads_references_submits_and_downloads(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first-image")
    second.write_bytes(b"second-image")
    output = tmp_path / "result.png"

    with respx.mock(assert_all_called=True) as router:
        upload = router.post("https://www.runninghub.ai/task/openapi/upload")

        def upload_response(request: httpx.Request) -> httpx.Response:
            name = "first" if b"first-image" in request.content else "second"
            return httpx.Response(
                200,
                json={"code": 0, "msg": "success", "data": {"fileName": f"uploaded-{name}.png"}},
            )

        upload.side_effect = upload_response
        submit = router.post("https://www.runninghub.ai/task/openapi/ai-app/run").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {"taskId": "image-task-1"}})
        )
        router.post("https://www.runninghub.cn/openapi/v2/query").mock(
            return_value=httpx.Response(
                200,
                json={"status": "SUCCESS", "results": [{"url": "https://output.test/result.png"}]},
            )
        )
        router.get("https://output.test/result.png").mock(
            return_value=httpx.Response(200, content=b"generated-image", headers={"content-type": "image/png"})
        )

        result = await RunningHubImageBackend(api_key="rh-key", poll_interval=0).generate(
            ImageGenerationRequest(
                prompt="keep the subject and change the background",
                output_path=output,
                reference_images=[ReferenceImage(str(first)), ReferenceImage(str(second))],
                aspect_ratio="21:9",
                image_size="2K",
            )
        )

    assert len(upload.calls) == 2
    body = json.loads(submit.calls[0].request.content)
    assert body["apiKey"] == "rh-key"
    assert body["webappId"] == 2104532059371909121
    nodes = {(item["nodeId"], item["fieldName"]): item["fieldValue"] for item in body["nodeInfoList"]}
    assert nodes[("1", "prompt")] == "keep the subject and change the background"
    assert nodes[("2", "image")] == "uploaded-first.png"
    assert nodes[("3", "image")] == "uploaded-second.png"
    assert all(nodes[(node_id, "image")] == "None" for node_id in ("4", "5", "6", "7", "8", "9", "10", "25"))
    assert nodes[("12", "aspectRatio")] == "21:9"
    assert nodes[("12", "resolution")] == "2k"
    assert output.read_bytes() == b"generated-image"
    assert result.image_uri == "https://output.test/result.png"
    assert result.provider == "runninghub"


@pytest.mark.asyncio
async def test_generate_without_references_sets_every_workflow_image_to_none(tmp_path):
    output = tmp_path / "result.png"
    with respx.mock(assert_all_called=True) as router:
        submit = router.post("https://www.runninghub.ai/task/openapi/ai-app/run").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {"taskId": "text-task-1"}})
        )
        router.post("https://www.runninghub.cn/openapi/v2/query").mock(
            return_value=httpx.Response(
                200,
                json={"status": "SUCCESS", "results": [{"url": "https://output.test/text.png"}]},
            )
        )
        router.get("https://output.test/text.png").mock(
            return_value=httpx.Response(200, content=b"text-image", headers={"content-type": "image/png"})
        )

        result = await RunningHubImageBackend(api_key="rh-key", poll_interval=0).generate(
            ImageGenerationRequest(
                prompt="a neon cafe poster",
                output_path=output,
                aspect_ratio="16:9",
                image_size="4K",
            )
        )

    body = json.loads(submit.calls[0].request.content)
    nodes = {(item["nodeId"], item["fieldName"]): item["fieldValue"] for item in body["nodeInfoList"]}
    assert nodes[("1", "prompt")] == "a neon cafe poster"
    assert all(nodes[(node_id, "image")] == "None" for node_id in ("2", "3", "4", "5", "6", "7", "8", "9", "10", "25"))
    assert nodes[("12", "aspectRatio")] == "16:9"
    assert nodes[("12", "resolution")] == "4k"
    assert output.read_bytes() == b"text-image"
    assert result.model == "ai-app-2104532059371909121"


@pytest.mark.asyncio
async def test_generate_rejects_failed_ai_app_submit(tmp_path):
    with respx.mock(assert_all_called=True) as router:
        router.post("https://www.runninghub.ai/task/openapi/ai-app/run").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 804,
                    "msg": "AI App is unavailable",
                    "data": {"taskId": ""},
                },
            )
        )

        with pytest.raises(RuntimeError, match="AI App is unavailable"):
            await RunningHubImageBackend(api_key="rh-key").generate(
                ImageGenerationRequest(
                    prompt="a neon cafe poster",
                    output_path=tmp_path / "result.png",
                )
            )


@pytest.mark.asyncio
async def test_generate_rejects_missing_reference_image(tmp_path):
    backend = RunningHubImageBackend(api_key="rh-key")

    with pytest.raises(ImageCapabilityError) as exc_info:
        await backend.generate(
            ImageGenerationRequest(
                prompt="edit",
                output_path=tmp_path / "out.png",
                reference_images=[ReferenceImage(str(tmp_path / "missing.png"))],
            )
        )

    assert exc_info.value.code == "image_reference_images_unreadable"
    assert exc_info.value.params["names"] == "missing.png"


@pytest.mark.asyncio
async def test_generate_reports_task_and_last_status_when_polling_times_out(tmp_path):
    with respx.mock(assert_all_called=True) as router:
        router.post("https://www.runninghub.ai/task/openapi/ai-app/run").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {"taskId": "stuck-task"}})
        )
        router.post("https://www.runninghub.cn/openapi/v2/query").mock(
            return_value=httpx.Response(200, json={"status": "RUNNING", "results": None})
        )

        with pytest.raises(RuntimeError, match=r"stuck-task.*0.02s.*RUNNING"):
            await RunningHubImageBackend(
                api_key="rh-key",
                poll_interval=0,
                poll_timeout=0.02,
            ).generate(
                ImageGenerationRequest(
                    prompt="a job that never finishes",
                    output_path=tmp_path / "result.png",
                )
            )
