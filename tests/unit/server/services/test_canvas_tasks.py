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
        _, image = add_run_outputs(graph, gen["id"], "a fox")
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
    }

    result = await canvas_tasks.execute_canvas_image_task("demo", ids["image"], payload, task_id="t1")

    assert result["file_path"] == f"canvas/images/{ids['image']}.png"
    image = _data(root, ids["image"])
    assert (image["status"], image["path"], image["version"]) == ("done", result["file_path"], 3)
    assert _data(root, ids["gen"])["status"] == "done"
    _, resource_type, resource_id, kwargs = generator.calls[0]
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
