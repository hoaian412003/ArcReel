"""分集源文路由：上传落盘 + 账本登记 + 入队资产分析（队列入口替身，其余走真实 ProjectManager）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.generation.generation_queue_client import WorkerOfflineError
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import episode_sources as router_mod
from tests.auth_deps import AUTH_DEPENDENCIES

_URL = "/api/v1/projects/demo/episodes/{episode}/source"


def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content_mode: str) -> TestClient:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "title": "T",
                "content_mode": content_mode,
                "generation_mode": "storyboard",
                "characters": {},
                "scenes": {},
                "props": {},
                "episodes": [],
            }
        ),
        encoding="utf-8",
    )
    manager = ProjectManager(tmp_path)
    monkeypatch.setattr(router_mod, "get_project_manager", lambda: manager)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="test", role="admin")
    return TestClient(app)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return _make_client(tmp_path, monkeypatch, "drama")


@pytest.fixture
def enqueue(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value={"task_id": "task-1", "deduped": False})
    monkeypatch.setattr(router_mod, "enqueue_task_only", mock)
    return mock


def test_pasted_text_registers_the_episode_and_queues_asset_analysis(
    client: TestClient, tmp_path: Path, enqueue: AsyncMock
) -> None:
    resp = client.put(_URL.format(episode=1), data={"content": "李恒在山村得到古玉。", "title": "开端"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "episode": 1,
        "title": "开端",
        "created": True,
        "stale": False,
        "characters": 10,
        "analysis": {"status": "queued", "task_id": "task-1", "deduped": False},
    }
    assert (tmp_path / "projects" / "demo" / "source" / "episode_1.txt").read_text(
        encoding="utf-8"
    ) == "李恒在山村得到古玉。"
    kwargs = enqueue.await_args.kwargs
    assert (kwargs["task_type"], kwargs["media_type"], kwargs["source"]) == ("text_asset_analysis", "text", "webui")


def test_uploaded_file_is_stripped_of_its_bom(client: TestClient, tmp_path: Path, enqueue: AsyncMock) -> None:
    with_bom = "\ufeff第一集的内容。".encode()

    resp = client.put(_URL.format(episode=2), files={"file": ("ep2.txt", with_bom, "text/plain")})

    assert resp.status_code == 200, resp.text
    assert (tmp_path / "projects" / "demo" / "source" / "episode_2.txt").read_text(encoding="utf-8") == "第一集的内容。"


def test_offline_worker_does_not_fail_the_upload(client: TestClient, tmp_path: Path, enqueue: AsyncMock) -> None:
    enqueue.side_effect = WorkerOfflineError("queue worker is offline")

    resp = client.put(_URL.format(episode=1), data={"content": "正文"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["analysis"] == {"status": "worker_offline", "task_id": None}
    assert (tmp_path / "projects" / "demo" / "source" / "episode_1.txt").exists()


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({}, 400),
        ({"data": {"content": "正文"}, "files": {"file": ("a.txt", b"x", "text/plain")}}, 400),
        ({"data": {"content": "   "}}, 400),
        ({"files": {"file": ("a.exe", b"x", "application/octet-stream")}}, 400),
    ],
    ids=["no-input", "both-inputs", "blank-text", "unsupported-format"],
)
def test_bad_requests_are_rejected_without_queuing(
    client: TestClient, tmp_path: Path, enqueue: AsyncMock, kwargs: dict, status: int
) -> None:
    resp = client.put(_URL.format(episode=1), **kwargs)

    assert resp.status_code == status
    enqueue.assert_not_awaited()
    assert not (tmp_path / "projects" / "demo" / "source" / "episode_1.txt").exists()


def test_non_drama_project_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enqueue: AsyncMock) -> None:
    narration_client = _make_client(tmp_path, monkeypatch, "narration")

    resp = narration_client.put(_URL.format(episode=1), data={"content": "正文"})

    assert resp.status_code == 400
    enqueue.assert_not_awaited()


def test_unknown_project_is_404(client: TestClient, enqueue: AsyncMock) -> None:
    resp = client.put("/api/v1/projects/missing/episodes/1/source", data={"content": "正文"})

    assert resp.status_code == 404


def test_analyze_endpoint_requeues_analysis(client: TestClient, enqueue: AsyncMock) -> None:
    resp = client.post("/api/v1/projects/demo/assets/analyze")

    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "task_id": "task-1", "deduped": False}


def test_delete_removes_an_uploaded_episode(client: TestClient, tmp_path: Path, enqueue: AsyncMock) -> None:
    client.put(_URL.format(episode=1), data={"content": "正文"})

    resp = client.delete("/api/v1/projects/demo/episodes/1")

    assert resp.status_code == 200, resp.text
    assert resp.json()["removed_files"] == 1
    assert not (tmp_path / "projects" / "demo" / "source" / "episode_1.txt").exists()


def test_delete_with_products_asks_for_confirmation_then_proceeds(
    client: TestClient, tmp_path: Path, enqueue: AsyncMock
) -> None:
    client.put(_URL.format(episode=1), data={"content": "正文"})
    script = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"
    script.parent.mkdir()
    script.write_text("{}", encoding="utf-8")

    refused = client.delete("/api/v1/projects/demo/episodes/1")
    confirmed = client.delete("/api/v1/projects/demo/episodes/1", params={"confirm_products": "true"})

    assert refused.status_code == 409
    assert confirmed.status_code == 200
    assert not script.exists()


def test_delete_of_a_missing_episode_is_404(client: TestClient) -> None:
    assert client.delete("/api/v1/projects/demo/episodes/5").status_code == 404


_NOVEL = (
    "Lời mở đầu.\n\nChương 1: Trọng sinh\nNội dung một.\nChương 2: Tiếp\nNội dung hai.\nChương 3: Hết\nNội dung ba."
)
_SPLIT = "/api/v1/projects/demo/episodes/split-novel"


def _put_novel(tmp_path: Path, text: str = _NOVEL, name: str = "novel.txt") -> None:
    source = tmp_path / "projects" / "demo" / "source"
    source.mkdir(exist_ok=True)
    (source / name).write_text(text, encoding="utf-8")


def test_split_preview_lists_the_episodes_without_writing_anything(
    client: TestClient, tmp_path: Path, enqueue: AsyncMock
) -> None:
    _put_novel(tmp_path)

    resp = client.post(_SPLIT, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is False
    assert body["pattern"] == "vi"
    assert [(e["episode"], e["title"]) for e in body["episodes"]] == [
        (1, "Chương 1: Trọng sinh"),
        (2, "Chương 2: Tiếp"),
        (3, "Chương 3: Hết"),
    ]
    enqueue.assert_not_awaited()
    assert not (tmp_path / "projects" / "demo" / "source" / "episode_1.txt").exists()


def test_split_commit_creates_every_episode_and_queues_asset_analysis_once(
    client: TestClient, tmp_path: Path, enqueue: AsyncMock
) -> None:
    _put_novel(tmp_path)

    resp = client.post(_SPLIT, json={"preview": False, "chapters_per_episode": 2})

    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] is True
    source = tmp_path / "projects" / "demo" / "source"
    assert sorted(p.name for p in source.glob("episode_*.txt")) == ["episode_1.txt", "episode_2.txt"]
    assert "Nội dung hai" in (source / "episode_1.txt").read_text(encoding="utf-8")
    assert "Nội dung ba" in (source / "episode_2.txt").read_text(encoding="utf-8")
    assert (source / "novel.txt").exists()
    enqueue.assert_awaited_once()


def test_split_refuses_to_overwrite_existing_episodes_unless_replace_is_set(
    client: TestClient, tmp_path: Path, enqueue: AsyncMock
) -> None:
    _put_novel(tmp_path)
    client.put(_URL.format(episode=2), data={"content": "tập thủ công"})

    refused = client.post(_SPLIT, json={"preview": False})
    assert not (tmp_path / "projects" / "demo" / "source" / "episode_1.txt").exists()
    replaced = client.post(_SPLIT, json={"preview": False, "replace": True})

    assert refused.status_code == 409
    assert replaced.status_code == 200
    assert "Nội dung hai" in (tmp_path / "projects" / "demo" / "source" / "episode_2.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("payload", "text", "status"),
    [
        ({}, "Không có tiêu đề chương nào.", 400),
        ({"source": "missing.txt"}, _NOVEL, 404),
    ],
    ids=["no-headings", "unknown-source"],
)
def test_split_rejects_unusable_input(
    client: TestClient, tmp_path: Path, enqueue: AsyncMock, payload: dict, text: str, status: int
) -> None:
    _put_novel(tmp_path, text)

    assert client.post(_SPLIT, json=payload).status_code == status


def test_split_needs_a_named_source_when_there_are_several(
    client: TestClient, tmp_path: Path, enqueue: AsyncMock
) -> None:
    _put_novel(tmp_path)
    _put_novel(tmp_path, _NOVEL, name="other.txt")

    assert client.post(_SPLIT, json={}).status_code == 400
    assert client.post(_SPLIT, json={"source": "other.txt"}).status_code == 200


def test_split_is_refused_for_non_drama_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    narration_client = _make_client(tmp_path, monkeypatch, "narration")
    _put_novel(tmp_path)

    assert narration_client.post(_SPLIT, json={}).status_code == 400
