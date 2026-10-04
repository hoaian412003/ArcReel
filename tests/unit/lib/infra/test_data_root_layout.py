"""数据根布局：纯派生、零 I/O；由项目目录求数据根与由数据根求项目目录互逆。"""

from __future__ import annotations

from pathlib import Path

from lib.infra.data_root_layout import DataRootLayout

_ROOT = Path("/nonexistent/data")


def test_for_project_dir_recovers_the_layout_of_its_data_root() -> None:
    layout = DataRootLayout(_ROOT)
    assert DataRootLayout.for_project_dir(layout.projects_dir / "demo") == layout


def test_derivation_does_no_io(tmp_path: Path) -> None:
    """派生不建目录、不要求目录存在。"""
    layout = DataRootLayout(tmp_path / "data")
    _ = (layout.projects_dir, layout.global_assets_dir, layout.trial_runs_dir, layout.log_dir)
    assert not layout.root.exists()
