"""画布路由：节点 / 连线增删、上传成节点、运行时冻结输入并挂出输出节点后入队。"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.dependencies import require_project_migration_ok
from server.error_handlers import register_error_handlers
from server.routers import canvas
from tests.auth_deps import AUTH_DEPENDENCIES


class _FakePM:
    file_lock = ProjectManager.file_lock

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir

    def load_project(self, _name):
        return {"generation_mode": "storyboard"}

    def get_project_path(self, name):
        if name != "demo":
            raise FileNotFoundError(name)
        return self.project_dir


class _FakeQueue:
    def __init__(self):
        self.calls = []

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": "task-1"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    queue = _FakeQueue()
    monkeypatch.setattr(canvas, "get_project_manager", lambda: _FakePM(tmp_path))
    monkeypatch.setattr(canvas, "get_generation_queue", lambda: queue)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.dependency_overrides[require_project_migration_ok] = lambda: None
    app.include_router(canvas.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), queue, tmp_path


def _node(client, node_type, **data):
    res = client.post(
        "/api/v1/projects/demo/canvas/nodes", json={"type": node_type, "position": {"x": 0, "y": 0}, "data": data}
    )
    assert res.status_code == 200, res.text
    return res.json()["node"]


def test_empty_canvas_and_unknown_project(env):
    client, _, _ = env
    body = client.get("/api/v1/projects/demo/canvas").json()
    assert (body["revision"], body["nodes"], body["edges"], body["episodes"]) == (0, [], [], [])
    assert client.get("/api/v1/projects/nope/canvas").status_code == 404


def test_revision_increments_per_write(env):
    client, _, _ = env
    _node(client, "text", text="a")
    _node(client, "text", text="b")
    assert client.get("/api/v1/projects/demo/canvas").json()["revision"] == 2


def test_invalid_connection_is_unprocessable(env):
    client, _, _ = env
    text = _node(client, "text", text="a")
    gen = _node(client, "image_generate")
    res = client.post(
        "/api/v1/projects/demo/canvas/edges",
        json={"source": text["id"], "source_handle": "text", "target": gen["id"], "target_handle": "refs"},
    )
    assert res.status_code == 422


def test_upload_creates_image_node(env):
    client, _, root = env
    res = client.post(
        "/api/v1/projects/demo/canvas/images?x=10&y=20", files={"file": ("ref.png", b"\x89PNG", "image/png")}
    )
    assert res.status_code == 200
    node = res.json()["node"]
    assert node["type"] == "image"
    assert node["position"] == {"x": 10.0, "y": 20.0}
    assert (root / node["data"]["path"]).read_bytes() == b"\x89PNG"
    bad = client.post(
        "/api/v1/projects/demo/canvas/images", files={"file": ("x.exe", b"x", "application/octet-stream")}
    )
    assert bad.status_code == 400


def test_run_requires_prompt(env):
    client, queue, _ = env
    gen = _node(client, "image_generate")
    assert client.post(f"/api/v1/projects/demo/canvas/nodes/{gen['id']}/run").status_code == 422
    assert queue.calls == []


def test_run_freezes_inputs_creates_outputs_and_enqueues(env):
    client, queue, _ = env
    text = _node(client, "text", text="a red fox")
    ref = client.post("/api/v1/projects/demo/canvas/images", files={"file": ("a.png", b"png", "image/png")}).json()[
        "node"
    ]
    gen = _node(client, "image_generate", aspect_ratio="16:9")
    for src, handle, target_handle in ((text, "text", "prompt"), (ref, "image", "refs")):
        client.post(
            "/api/v1/projects/demo/canvas/edges",
            json={"source": src["id"], "source_handle": handle, "target": gen["id"], "target_handle": target_handle},
        )

    res = client.post(f"/api/v1/projects/demo/canvas/nodes/{gen['id']}/run")

    assert res.status_code == 200, res.text
    body = res.json()
    call = queue.calls[0]
    assert call["task_type"] == "canvas_image"
    assert call["resource_id"] == body["image_node_id"]
    assert call["payload"]["prompt"] == "a red fox"
    assert call["payload"]["references"] == [ref["data"]["path"]]
    assert call["payload"]["aspect_ratio"] == "16:9"
    graph = client.get("/api/v1/projects/demo/canvas").json()
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id[body["image_node_id"]]["data"]["status"] == "queued"
    assert by_id[body["text_node_id"]]["data"]["text"] == "a red fox"
    assert by_id[gen["id"]]["data"]["status"] == "queued"
    # 运行中的节点不能重复运行，也不能删除。
    assert client.post(f"/api/v1/projects/demo/canvas/nodes/{gen['id']}/run").status_code == 409
    assert client.delete(f"/api/v1/projects/demo/canvas/nodes/{gen['id']}").status_code == 409


def test_enqueue_failure_rolls_back_outputs(env, monkeypatch):
    client, queue, _ = env

    async def boom(**_):
        raise RuntimeError("queue down")

    monkeypatch.setattr(queue, "enqueue_task", boom)
    text = _node(client, "text", text="x")
    gen = _node(client, "image_generate")
    client.post(
        "/api/v1/projects/demo/canvas/edges",
        json={"source": text["id"], "source_handle": "text", "target": gen["id"], "target_handle": "prompt"},
    )
    with pytest.raises(RuntimeError):
        client.post(f"/api/v1/projects/demo/canvas/nodes/{gen['id']}/run")
    graph = client.get("/api/v1/projects/demo/canvas").json()
    assert {n["id"] for n in graph["nodes"]} == {text["id"], gen["id"]}
    assert next(n for n in graph["nodes"] if n["id"] == gen["id"])["data"]["status"] == "failed"


def test_view_selection_is_sorted_and_persisted(env):
    client, _, _ = env
    res = client.put("/api/v1/projects/demo/canvas/view", json={"episodes": [3, 1, 3]})
    assert res.json()["episodes"] == [1, 3]
    assert client.get("/api/v1/projects/demo/canvas").json()["view"] == {"episodes": [1, 3]}


def test_edges_to_derived_nodes_validate_by_kind(env):
    client, _, _ = env
    writer = _node(client, "h3_writer")
    unit = "p:unit:1:E1U1"
    ok = client.post(
        "/api/v1/projects/demo/canvas/edges",
        json={"source": unit, "source_handle": "unit", "target": writer["id"], "target_handle": "units"},
    )
    assert ok.status_code == 200
    bad = client.post(
        "/api/v1/projects/demo/canvas/edges",
        json={"source": unit, "source_handle": "text", "target": writer["id"], "target_handle": "units"},
    )
    assert bad.status_code == 422


def test_episode_group_layout_is_stored_as_override(env):
    client, _, root = env
    res = client.post("/api/v1/projects/demo/canvas/layout", json={"positions": {"p:episode:2": {"x": 5, "y": 6}}})
    assert res.status_code == 200
    from lib.canvas.store import load_canvas

    assert load_canvas(root)["overrides"] == {"p:episode:2": {"x": 5.0, "y": 6.0}}


def test_node_model_choice_becomes_a_provider_override_in_the_task(env):
    client, queue, _ = env
    text = _node(client, "text", text="a fox")
    gen = _node(client, "image_generate", model="gemini-aistudio/imagen-4")
    client.post(
        "/api/v1/projects/demo/canvas/edges",
        json={"source": text["id"], "source_handle": "text", "target": gen["id"], "target_handle": "prompt"},
    )
    assert client.post(f"/api/v1/projects/demo/canvas/nodes/{gen['id']}/run").status_code == 200
    payload = queue.calls[0]["payload"]
    assert (payload["image_provider"], payload["image_model"]) == ("gemini-aistudio", "imagen-4")


def test_a_malformed_model_choice_is_ignored(env):
    client, queue, _ = env
    text = _node(client, "text", text="a fox")
    gen = _node(client, "image_generate", model="nonsense")
    client.post(
        "/api/v1/projects/demo/canvas/edges",
        json={"source": text["id"], "source_handle": "text", "target": gen["id"], "target_handle": "prompt"},
    )
    client.post(f"/api/v1/projects/demo/canvas/nodes/{gen['id']}/run")
    assert "image_provider" not in queue.calls[0]["payload"]


def test_template_save_use_and_delete(env):
    client, _, _ = env
    text = _node(client, "text", text="a fox")
    gen = _node(client, "image_generate")
    client.post(
        "/api/v1/projects/demo/canvas/edges",
        json={"source": text["id"], "source_handle": "text", "target": gen["id"], "target_handle": "prompt"},
    )
    saved = client.post(
        "/api/v1/projects/demo/canvas/templates", json={"name": "fox", "node_ids": [text["id"], gen["id"]]}
    )
    assert saved.status_code == 200
    template_id = saved.json()["template"]["id"]
    assert client.get("/api/v1/projects/demo/canvas").json()["templates"] == [
        {"id": template_id, "name": "fox", "node_count": 2}
    ]

    used = client.post(
        f"/api/v1/projects/demo/canvas/templates/{template_id}/use", json={"position": {"x": 900, "y": 0}}
    )
    assert used.status_code == 200
    assert len(client.get("/api/v1/projects/demo/canvas").json()["nodes"]) == 4

    assert client.delete(f"/api/v1/projects/demo/canvas/templates/{template_id}").status_code == 200
    assert client.delete(f"/api/v1/projects/demo/canvas/templates/{template_id}").status_code == 404
    assert (
        client.post("/api/v1/projects/demo/canvas/templates", json={"name": "x", "node_ids": ["nope"]}).status_code
        == 422
    )


def test_branch_endpoint_returns_the_plan(env):
    client, _, _ = env
    text = _node(client, "text", text="a fox")
    first = _node(client, "image_generate")
    second = _node(client, "image_generate")
    for src, handle, target, th in (
        (text, "text", first, "prompt"),
        (first, "image", second, "refs"),
        (text, "text", second, "prompt"),
    ):
        client.post(
            "/api/v1/projects/demo/canvas/edges",
            json={"source": src["id"], "source_handle": handle, "target": target["id"], "target_handle": th},
        )
    plan = client.get(f"/api/v1/projects/demo/canvas/nodes/{second['id']}/branch").json()["nodes"]
    assert [item["id"] for item in plan] == [first["id"], second["id"]]


def test_video_and_audio_runs_enqueue_their_own_task_types(env):
    client, queue, _ = env
    text = _node(client, "text", text="a duel at dawn")
    video = _node(client, "video_generate", duration_seconds=6, aspect_ratio="16:9")
    audio = _node(client, "audio_generate", voice="Ethan")
    for target, handle in ((video, "prompt"), (audio, "text")):
        client.post(
            "/api/v1/projects/demo/canvas/edges",
            json={"source": text["id"], "source_handle": "text", "target": target["id"], "target_handle": handle},
        )
    assert client.post(f"/api/v1/projects/demo/canvas/nodes/{video['id']}/run").status_code == 200
    assert client.post(f"/api/v1/projects/demo/canvas/nodes/{audio['id']}/run").status_code == 200

    clip_call, voice_call = queue.calls
    assert (clip_call["task_type"], clip_call["media_type"]) == ("canvas_video", "video")
    assert (clip_call["payload"]["duration_seconds"], clip_call["payload"]["aspect_ratio"]) == (6, "16:9")
    assert (voice_call["task_type"], voice_call["media_type"]) == ("canvas_audio", "audio")
    assert voice_call["payload"]["voice"] == "Ethan"
    types = sorted(n["type"] for n in client.get("/api/v1/projects/demo/canvas").json()["nodes"])
    assert types == ["audio", "audio_generate", "text", "text", "text", "video", "video_generate"]
