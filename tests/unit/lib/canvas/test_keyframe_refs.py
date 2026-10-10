"""联合图节点上用户接的画布参考图。"""

from __future__ import annotations

from pathlib import Path

from lib.canvas.graph import make_node
from lib.canvas.keyframe_refs import canvas_sheet_references
from lib.canvas.store import load_canvas, mutate_canvas
from lib.project.project_manager import ProjectManager


def test_images_wired_into_the_sheet_node_become_extra_references(tmp_path: Path) -> None:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    project_path = pm.get_project_path("demo")
    (project_path / "han.png").write_bytes(b"x")

    def _build(graph: dict) -> None:
        generator = make_node("image_generate", {"x": 0, "y": 0}, {"ep": 1, "step": "kf_gen", "sheet": "S1"})
        asset = make_node("asset", {"x": 0, "y": 0}, {"name": "Hán Thăng", "note": "Hán Thăng, 35 tuổi."})
        image = make_node("image", {"x": 0, "y": 0}, server_data={"path": "han.png"})
        graph["nodes"] += [generator, asset, image]
        graph["edges"] += [
            {
                "id": "e1",
                "source": image["id"],
                "sourceHandle": "image",
                "target": asset["id"],
                "targetHandle": "image",
            },
            {
                "id": "e2",
                "source": asset["id"],
                "sourceHandle": "asset",
                "target": generator["id"],
                "targetHandle": "image1",
            },
        ]

    mutate_canvas(pm, project_path, _build)
    assert load_canvas(project_path)["nodes"]

    refs = canvas_sheet_references(project_path, 1, "S1")

    assert [(path.name, label) for path, label in refs] == [("han.png", "Hán Thăng, 35 tuổi.")]
    assert canvas_sheet_references(project_path, 1, "S2") == []
