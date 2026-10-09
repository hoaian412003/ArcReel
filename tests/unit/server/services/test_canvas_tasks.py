"""画布图片任务执行：成功写回产物路径，失败写回错误，节点被删时不报错。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.canvas.graph import add_node, add_run_outputs, make_node
from lib.canvas.store import load_canvas, mutate_canvas
from lib.project.project_manager import ProjectManager
from server.services.tasks import canvas_tasks


class _FakePM:
    file_lock = ProjectManager.file_lock

    def __init__(self, root: Path):
        self.root = root

    def load_project(self, _name):
        return {}

    def get_project_path(self, _name):
        return self.root


class _Generator:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls = []

    async def generate_image_async(self, prompt, resource_type, resource_id, **kwargs):
        self.calls.append((prompt, resource_type, resource_id, kwargs))
        if self.fail:
            raise self.fail
        return Path("x"), 3


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    pm = _FakePM(tmp_path)
    monkeypatch.setattr(canvas_tasks, "get_project_manager", lambda: pm)
    ids = {}

    def _seed(graph):
        gen = add_node(graph, make_node("image_generate", {"x": 0, "y": 0}))
        image = add_run_outputs(graph, gen["id"], "a fox")
        ids.update(gen=gen["id"], image=image["id"])

    mutate_canvas(pm, tmp_path, _seed)
    (tmp_path / "canvas").mkdir(exist_ok=True)
    (tmp_path / "ref.png").write_bytes(b"png")

    def _install(generator, max_refs=0):
        context = SimpleNamespace(
            generator=generator, image=SimpleNamespace(max_reference_images=max_refs, resolution="1K")
        )

        async def _resolve(*_a, **_k):
            return context

        monkeypatch.setattr(canvas_tasks, "resolve_generation_context", _resolve)

    return pm, tmp_path, ids, _install


def _data(root, node_id):
    return next(n for n in load_canvas(root)["nodes"] if n["id"] == node_id)["data"]


async def test_success_writes_path_and_version(seeded):
    _, root, ids, install = seeded
    generator = _Generator()
    install(generator, max_refs=1)
    payload = {
        "prompt": "a fox",
        "generator_id": ids["gen"],
        "aspect_ratio": "16:9",
        "references": ["ref.png", "../escape.png", "missing.png"],
        "reference_labels": ["hero.png", "x", "y"],
    }

    result = await canvas_tasks.execute_canvas_image_task("demo", ids["image"], payload, task_id="t1")

    assert result["file_path"] == f"canvas/images/{ids['image']}.png"
    image = _data(root, ids["image"])
    assert (image["status"], image["path"], image["version"]) == ("done", result["file_path"], 3)
    assert _data(root, ids["gen"])["status"] == "done"
    sent_prompt, resource_type, resource_id, kwargs = generator.calls[0]
    assert sent_prompt == "Reference images (cite them in the prompt as 图N):\n图1: hero.png\n\na fox"
    assert (resource_type, resource_id, kwargs["aspect_ratio"]) == ("canvas_images", ids["image"], "16:9")
    assert kwargs["reference_images"] == [root / "ref.png"]


async def test_failure_marks_nodes_failed_and_reraises(seeded):
    _, root, ids, install = seeded
    install(_Generator(fail=RuntimeError("provider exploded")))

    with pytest.raises(RuntimeError):
        await canvas_tasks.execute_canvas_image_task(
            "demo", ids["image"], {"prompt": "p", "generator_id": ids["gen"]}, task_id="t1"
        )

    assert _data(root, ids["image"])["status"] == "failed"
    assert "provider exploded" in _data(root, ids["image"])["error"]
    assert _data(root, ids["gen"])["status"] == "failed"


async def test_deleted_node_does_not_break_the_task(seeded):
    pm, root, ids, install = seeded
    install(_Generator())
    mutate_canvas(pm, root, lambda g: g.update(nodes=[n for n in g["nodes"] if n["id"] != ids["image"]]))

    result = await canvas_tasks.execute_canvas_image_task(
        "demo", ids["image"], {"prompt": "p", "generator_id": ids["gen"]}, task_id="t1"
    )

    assert result["version"] == 3


class _Media:
    """视频 / 语音生成器替身：记下调用，按需失败。"""

    def __init__(self):
        self.video_calls = []
        self.audio_calls = []

    async def generate_video_async(self, prompt, resource_type, resource_id, **kwargs):
        self.video_calls.append((prompt, resource_type, resource_id, kwargs))
        return Path("x"), 2, None, None

    async def generate_audio_async(self, text, resource_id, voice, **kwargs):
        self.audio_calls.append((text, resource_id, voice, kwargs))
        return Path("y"), 4


def _install_media(monkeypatch, media):
    context = SimpleNamespace(
        generator=media,
        video=SimpleNamespace(resolution="720p"),
        audio=SimpleNamespace(narration_voice="Cherry", narration_speed=None),
    )
    lanes = []

    async def _resolve(*_a, **kwargs):
        lanes.append(kwargs)
        return context

    monkeypatch.setattr(canvas_tasks, "resolve_generation_context", _resolve)
    return lanes


async def test_video_task_uses_the_first_reference_as_start_frame_and_writes_back(seeded, monkeypatch):
    _, root, ids, _ = seeded
    media = _Media()
    lanes = _install_media(monkeypatch, media)
    payload = {
        "prompt": "a duel",
        "generator_id": ids["gen"],
        "references": ["ref.png"],
        "aspect_ratio": "16:9",
        "duration_seconds": 6,
    }

    result = await canvas_tasks.execute_canvas_video_task("demo", ids["image"], payload, task_id="t1")

    assert result["file_path"] == f"canvas/videos/{ids['image']}.mp4"
    prompt, resource_type, resource_id, kwargs = media.video_calls[0]
    assert (prompt, resource_type, resource_id) == ("a duel", "canvas_videos", ids["image"])
    assert kwargs["start_image"] == root / "ref.png"
    assert (kwargs["aspect_ratio"], kwargs["duration_seconds"], kwargs["resolution"]) == ("16:9", 6, "720p")
    assert lanes[0]["video"].generation_type == "i2v"
    assert _data(root, ids["image"])["status"] == "done"
    assert _data(root, ids["image"])["version"] == 2


async def test_video_task_without_a_start_frame_is_text_to_video(seeded, monkeypatch):
    _, _, ids, _ = seeded
    media = _Media()
    lanes = _install_media(monkeypatch, media)

    await canvas_tasks.execute_canvas_video_task("demo", ids["image"], {"prompt": "p", "generator_id": ids["gen"]})

    assert media.video_calls[0][3]["start_image"] is None
    assert lanes[0]["video"].generation_type is None


async def test_audio_task_defaults_to_the_project_voice_and_lands_in_the_tts_folder(seeded, monkeypatch):
    _, root, ids, _ = seeded
    media = _Media()
    _install_media(monkeypatch, media)

    result = await canvas_tasks.execute_canvas_audio_task(
        "demo", ids["image"], {"prompt": "hello", "generator_id": ids["gen"]}, task_id="t1"
    )

    assert media.audio_calls[0][:3] == ("hello", ids["image"], "Cherry")
    assert result["file_path"] == f"audio/segment_{ids['image']}.wav"
    assert _data(root, ids["image"])["path"] == result["file_path"]

    await canvas_tasks.execute_canvas_audio_task(
        "demo", ids["image"], {"prompt": "hi", "voice": "Ethan", "generator_id": ids["gen"]}
    )
    assert media.audio_calls[1][2] == "Ethan"


class _ReportingGenerator(_Generator):
    """模拟供应商轮询：先报排队，再报运行，最后成功；中间读一次节点状态。"""

    def __init__(self, root, node_id):
        super().__init__()
        self.root = root
        self.node_id = node_id
        self.seen: list[str] = []

    async def generate_image_async(self, prompt, resource_type, resource_id, **kwargs):
        from lib.infra.poll_sync_hook import fire_provider_sync

        for status in ("QUEUED", "QUEUED", "RUNNING", "SUCCESS"):
            await fire_provider_sync(label="RunningHub", status=status)
            self.seen.append(_data(self.root, self.node_id)["status"])
        return Path("x"), 3


async def test_provider_reported_queueing_shows_on_the_node_until_it_starts_running(seeded):
    _, root, ids, install = seeded
    generator = _ReportingGenerator(root, ids["image"])
    install(generator)

    await canvas_tasks.execute_canvas_image_task(
        "demo", ids["image"], {"prompt": "a fox", "generator_id": ids["gen"]}, task_id="t1"
    )

    assert generator.seen == ["queued", "queued", "running", "running"]
    final = _data(root, ids["image"])
    assert final["status"] == "done"
    assert {"submitted_at", "started_at", "finished_at", "status_at"} <= set(final)
