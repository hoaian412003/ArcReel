"""角色配音性别（gender）：资产规格、创建 / 覆盖写与 Agent 可编辑白名单。"""

from __future__ import annotations

import pytest

from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import ProjectManager


@pytest.fixture
def gender_pm(tmp_path):
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo")
    return manager


def test_character_spec_declares_gender_as_a_string_field_agents_may_edit() -> None:
    spec = ASSET_SPECS["character"]
    assert "gender" in spec.extra_string_fields
    assert "gender" in spec.agent_editable_extra_fields
    assert "gender" not in ASSET_SPECS["scene"].extra_string_fields


def test_add_character_persists_gender_and_defaults_to_unset(gender_pm) -> None:
    gender_pm.add_character("demo", "李白", "诗人", gender="male")
    gender_pm.add_character("demo", "路人", "x")
    characters = gender_pm.load_project("demo")["characters"]
    assert characters["李白"]["gender"] == "male"
    assert characters["路人"]["gender"] == ""


def test_overwriting_a_character_keeps_the_registered_gender_unless_replaced(gender_pm) -> None:
    gender_pm.add_project_character("demo", "李白", "诗人", gender="male")
    gender_pm.add_project_character("demo", "李白", "新描述")
    assert gender_pm.load_project("demo")["characters"]["李白"]["gender"] == "male"
    gender_pm.add_project_character("demo", "李白", "新描述", gender="female")
    assert gender_pm.load_project("demo")["characters"]["李白"]["gender"] == "female"


def test_add_project_character_rejects_unknown_gender(gender_pm) -> None:
    with pytest.raises(ValueError, match="gender"):
        gender_pm.add_project_character("demo", "李白", "诗人", gender="other")


def test_upsert_assets_accepts_gender_from_the_agent(gender_pm) -> None:
    gender_pm.upsert_assets("demo", "characters", {"李白": {"description": "诗人", "gender": "male"}})
    assert gender_pm.load_project("demo")["characters"]["李白"]["gender"] == "male"
