"""数据根布局迁移入口：迁移后旧布局的项目与存量数据（调用记录、用户数据、运行时状态）按当前布局可用，各步骤重跑不改变结果。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.api_call import ApiCall
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.data_root_layout_migration import migrate_data_root_layout
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import run_project_migrations


@pytest.fixture
def projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "data")
    manager.create_project("demo", content_mode="narration")
    return manager


def _write_artifact(projects: ProjectManager, relative: str) -> None:
    path = projects.get_project_path("demo") / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"artifact")


async def _record_call(session_factory: async_sessionmaker[AsyncSession], output_path: str) -> int:
    async with session_factory() as session:
        repo = UsageRepository(session)
        call_id = await repo.start_call(project_name="demo", call_type="image", model="m")
        await repo.finish_call(
            call_id, status="success", settlement=SettlementInput(cost_amount=0.0), output_path=output_path
        )
    return call_id


async def _recorded_output_path(session_factory: async_sessionmaker[AsyncSession], call_id: int) -> str:
    async with session_factory() as session:
        record = await UsageRepository(session).get_record(call_id)
    assert record is not None
    return record["output_path"]


async def _recorded_updated_at(session_factory: async_sessionmaker[AsyncSession], call_id: int) -> datetime | None:
    async with session_factory() as session:
        return await session.scalar(select(ApiCall.updated_at).where(ApiCall.id == call_id))


async def _migrate(projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    await migrate_data_root_layout(projects.data_root, session_factory=session_factory)


async def test_historical_call_records_resolve_artifacts_within_project_after_migration(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_artifact(projects, "characters/婉儿.png")
    _write_artifact(projects, "storyboards/scene_E1S01.png")
    project_dir = projects.get_project_path("demo")
    at_current_location = await _record_call(session_factory, str(project_dir / "characters" / "婉儿.png"))
    # 记录写下后数据根被整体挪过：记录里的前缀已不是当前数据根。
    from_moved_data_root = await _record_call(
        session_factory, str(tmp_path / "old-root" / "demo" / "storyboards" / "scene_E1S01.png")
    )
    already_relative = await _record_call(session_factory, "storyboards/scene_E1S01.png")

    await _migrate(projects, session_factory, tmp_path)

    for call_id in (at_current_location, from_moved_data_root, already_relative):
        recorded = await _recorded_output_path(session_factory, call_id)
        assert not Path(recorded).is_absolute()
        assert (project_dir / recorded).is_file()


async def test_rerunning_migration_leaves_call_records_unchanged(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_artifact(projects, "videos/scene_E1S01.mp4")
    call_id = await _record_call(session_factory, str(projects.get_project_path("demo") / "videos" / "scene_E1S01.mp4"))

    await _migrate(projects, session_factory, tmp_path)
    after_first = await _recorded_output_path(session_factory, call_id)
    updated_after_first = await _recorded_updated_at(session_factory, call_id)
    # 后续步骤失败、完成标记未写时，下次启动各步骤从头重跑。
    DataRootLayout(projects.data_root).layout_migration_marker_path.unlink()
    await _migrate(projects, session_factory, tmp_path)

    assert await _recorded_output_path(session_factory, call_id) == after_first == "videos/scene_E1S01.mp4"
    assert await _recorded_updated_at(session_factory, call_id) == updated_after_first


async def test_moved_root_with_repeated_project_name_does_not_rewrite_ambiguous_path(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_artifact(projects, "scene.png")
    _write_artifact(projects, "data/demo/scene.png")
    stored = str(tmp_path / "old" / "demo" / "data" / "demo" / "scene.png")
    call_id = await _record_call(session_factory, stored)

    await _migrate(projects, session_factory, tmp_path)

    assert await _recorded_output_path(session_factory, call_id) == stored


async def test_path_escaping_the_project_dir_is_left_unchanged(
    tmp_path: Path, projects: ProjectManager, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    stored = f"{projects.get_project_path('demo')}/../other/image.png"
    call_id = await _record_call(session_factory, stored)

    await _migrate(projects, session_factory, tmp_path)

    assert await _recorded_output_path(session_factory, call_id) == stored


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    return root


async def _migrate_data_root(data_root: Path, session_factory: async_sessionmaker[AsyncSession]) -> None:
    await migrate_data_root_layout(data_root, session_factory=session_factory)


def _write_legacy_user_files(data_root: Path, user_id: str, files: dict[str, str]) -> None:
    user_dir = data_root / ".arcreel" / "users" / user_id
    user_dir.mkdir(parents=True)
    for name, body in files.items():
        (user_dir / name).write_text(body, encoding="utf-8")


def _user_file(data_root: Path, user_id: str, name: str) -> str:
    return (DataRootLayout(data_root).users_dir / user_id / name).read_text(encoding="utf-8")


async def test_user_data_stays_readable_after_migration_and_rerun(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_user_files(data_root, "default", {"a.md": "冷色调", "b.md": "暖色调"})
    _write_legacy_user_files(data_root, "u2", {"a.md": "另一位用户"})

    await _migrate_data_root(data_root, session_factory)
    # 后续步骤失败、完成标记未写时，下次启动各步骤从头重跑。
    DataRootLayout(data_root).layout_migration_marker_path.unlink()
    await _migrate_data_root(data_root, session_factory)

    assert _user_file(data_root, "default", "a.md") == "冷色调"
    assert _user_file(data_root, "default", "b.md") == "暖色调"
    assert _user_file(data_root, "u2", "a.md") == "另一位用户"
    assert ProjectManager(data_root).list_projects() == []


async def test_user_data_move_resumes_after_interruption(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_user_files(data_root, "default", {"a.md": "先搬", "b.md": "后搬"})
    # 上次迁移中断在同一用户的数据搬了一半时。
    moved = DataRootLayout(data_root).users_dir / "default"
    moved.mkdir(parents=True)
    (data_root / ".arcreel" / "users" / "default" / "a.md").replace(moved / "a.md")

    await _migrate_data_root(data_root, session_factory)

    assert _user_file(data_root, "default", "a.md") == "先搬"
    assert _user_file(data_root, "default", "b.md") == "后搬"


def _write_legacy_project(data_root: Path, name: str, novel: str = "从前有座山") -> None:
    """旧布局的项目：直接放在数据根下。"""
    scratch = ProjectManager(data_root.parent / "scratch")
    scratch.create_project(name, content_mode="narration")
    project_dir = scratch.get_project_path(name)
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "source" / "novel.txt").write_text(novel, encoding="utf-8")
    project_dir.rename(data_root / name)


def _novel(manager: ProjectManager, name: str) -> str:
    return (manager.get_project_path(name) / "source" / "novel.txt").read_text(encoding="utf-8")


async def test_legacy_projects_are_usable_after_migration(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_project(data_root, "demo")
    _write_legacy_project(data_root, "second", novel="第二个故事")

    await _migrate_data_root(data_root, session_factory)

    manager = ProjectManager(data_root)
    assert manager.list_projects() == ["demo", "second"]
    assert manager.load_project("demo")
    assert _novel(manager, "second") == "第二个故事"


async def test_second_migration_run_does_nothing(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_project(data_root, "demo")
    await _migrate_data_root(data_root, session_factory)
    # 迁移完成之后数据根下再出现的旧布局形态目录不再被搬动。
    _write_legacy_project(data_root, "late")

    await _migrate_data_root(data_root, session_factory)

    assert ProjectManager(data_root).list_projects() == ["demo"]
    assert (data_root / "late" / "project.json").is_file()


async def test_migration_resumes_when_projects_were_already_moved(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_project(data_root, "demo")
    # 上次迁移中断在项目目录已搬完、后续步骤尚未完成时。
    projects_dir = DataRootLayout(data_root).projects_dir
    projects_dir.mkdir()
    (data_root / "demo").rename(projects_dir / "demo")

    await _migrate_data_root(data_root, session_factory)

    assert ProjectManager(data_root).list_projects() == ["demo"]


async def test_project_named_projects_keeps_its_name(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_project(data_root, "projects", novel="与容器同名")
    _write_legacy_project(data_root, "demo")

    await _migrate_data_root(data_root, session_factory)

    manager = ProjectManager(data_root)
    assert manager.list_projects() == ["demo", "projects"]
    assert _novel(manager, "projects") == "与容器同名"


async def test_system_named_entries_are_split_between_projects_and_system_data(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # 名为 users 的旧项目与旧位置的用户数据同时存在：项目搬走后，同一次迁移里用户数据就位。
    _write_legacy_project(data_root, "users", novel="与用户目录同名")
    _write_legacy_user_files(data_root, "default", {"a.md": "偏好"})
    (data_root / "logs").mkdir()
    (data_root / "logs" / "arcreel.log").write_text("line\n", encoding="utf-8")
    (data_root / "notes").mkdir()
    (data_root / "notes" / "todo.txt").write_text("杂项", encoding="utf-8")

    await _migrate_data_root(data_root, session_factory)

    manager = ProjectManager(data_root)
    assert manager.list_projects() == ["users"]
    assert _novel(manager, "users") == "与用户目录同名"
    assert _user_file(data_root, "default", "a.md") == "偏好"
    assert (DataRootLayout(data_root).log_dir / "arcreel.log").read_text(encoding="utf-8") == "line\n"
    assert (manager.projects_dir / "notes" / "todo.txt").read_text(encoding="utf-8") == "杂项"


async def test_failed_migration_is_retried_on_next_run(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_project(data_root, "demo")
    blocking = DataRootLayout(data_root).projects_dir / "demo"
    blocking.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        await _migrate_data_root(data_root, session_factory)
    blocking.rmdir()
    await _migrate_data_root(data_root, session_factory)

    assert ProjectManager(data_root).list_projects() == ["demo"]


async def test_project_left_mid_schema_swap_is_reclaimed_after_migration(
    data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_legacy_project(data_root, "demo", novel="交换窗口里的项目")
    # 旧版本的项目 schema 迁移停在目录交换的两次改名之间：项目目录只剩 rollback 目录。
    (data_root / "demo").rename(data_root / f".demo.v6-rollback-{'a' * 32}")

    await _migrate_data_root(data_root, session_factory)
    run_project_migrations(DataRootLayout(data_root).projects_dir)

    manager = ProjectManager(data_root)
    assert manager.list_projects() == ["demo"]
    assert _novel(manager, "demo") == "交换窗口里的项目"
