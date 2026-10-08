"""起始帧规划：清单校验与按场景装入联合图。"""

import re

import pytest

from lib.keyframes.models import CELLS_PER_SHEET
from lib.keyframes.planning import KeyframePlanViolation, PlannedCell, build_board, plan_fingerprint

UNITS = [
    {"unit_id": "E1U01", "text": "@[车内] @[甲] 看向 @[乙]。", "direction": "d1"},
    {"unit_id": "E1U02", "text": "@[车内] @[乙] 回头。", "direction": "d2"},
    {"unit_id": "E1U03", "text": "@[车内] @[甲] 站起。", "direction": "d3"},
    {"unit_id": "E1U04", "text": "@[站台] @[乙] 走远。", "direction": "d4"},
    {"unit_id": "E1U05", "text": "只有旁白", "direction": "d5"},
]
CHARACTERS = ["甲", "乙"]
SCENES = ["车内", "站台"]


def _cell(label: str, scene: str, units: list[str], characters: list[str]) -> PlannedCell:
    return PlannedCell(
        label=label, scene=scene, staging="甲左乙右", framing="近景", characters=characters, unit_ids=units
    )


def _build(planned: list[PlannedCell]):
    return build_board(1, planned, units=UNITS, characters=CHARACTERS, scenes=SCENES)


def test_cells_are_numbered_in_sheet_order_and_grouped_by_scene() -> None:
    result = _build(
        [
            _cell("甲近景", "车内", ["E1U01"], ["甲", "乙"]),
            _cell("站台", "站台", ["E1U04"], ["乙"]),
            _cell("乙近景", "车内", ["E1U02"], ["乙"]),
        ]
    )

    sheets = result.board.sheets
    assert [(s.id, s.scene, [c.id for c in s.cells]) for s in sheets] == [
        ("S1", "车内", ["K01", "K02"]),
        ("S2", "站台", ["K03"]),
    ]
    assert [c.label for c in sheets[0].cells] == ["甲近景", "乙近景"]


def test_a_scene_with_more_cells_than_a_sheet_holds_spills_onto_the_next_sheet() -> None:
    count = CELLS_PER_SHEET + 1
    units = [{"unit_id": f"U{i}", "text": "@[车内] @[甲]", "direction": ""} for i in range(count)]
    planned = [
        PlannedCell(label=f"格{i}", scene="车内", staging="s", framing="f", characters=["甲"], unit_ids=[f"U{i}"])
        for i in range(count)
    ]

    result = build_board(1, planned, units=units, characters=CHARACTERS, scenes=SCENES)

    assert [len(s.cells) for s in result.board.sheets] == [CELLS_PER_SHEET, 1]
    assert [s.scene for s in result.board.sheets] == ["车内", "车内"]


def test_units_without_a_cell_are_reported_as_uncovered() -> None:
    result = _build([_cell("甲近景", "车内", ["E1U01"], ["甲"])])

    assert result.uncovered_unit_ids == ["E1U02", "E1U03", "E1U04", "E1U05"]


def test_a_unit_may_not_belong_to_two_cells() -> None:
    with pytest.raises(KeyframePlanViolation, match="一个单元只用一格"):
        _build([_cell("a", "车内", ["E1U01"], ["甲"]), _cell("b", "车内", ["E1U01"], ["甲"])])


@pytest.mark.parametrize(
    ("planned", "needle"),
    [
        (_cell("x", "不存在", ["E1U01"], ["甲"]), "场景「不存在」没有登记"),
        (_cell("x", "车内", ["E1U01"], ["丙"]), "出镜角色['丙']没有登记"),
        (_cell("x", "车内", ["E9"], ["甲"]), "不存在的单元 E9"),
    ],
)
def test_unregistered_names_and_unknown_units_are_rejected(planned: PlannedCell, needle: str) -> None:
    with pytest.raises(KeyframePlanViolation, match=re.escape(needle)):
        _build([planned])


def test_a_character_the_unit_text_does_not_reference_is_rejected() -> None:
    with pytest.raises(KeyframePlanViolation, match="没有 @ 引用他们"):
        _build([_cell("x", "车内", ["E1U02"], ["甲", "乙"])])


def test_every_problem_is_reported_together() -> None:
    with pytest.raises(KeyframePlanViolation) as caught:
        _build([_cell("x", "不存在", ["E9"], ["丙"])])

    assert len(caught.value.problems) == 3


def test_plan_fingerprint_changes_when_text_or_direction_changes() -> None:
    base = plan_fingerprint(UNITS)
    edited_text = [{**UNITS[0], "text": "改了"}, *UNITS[1:]]
    edited_direction = [{**UNITS[0], "direction": "改了"}, *UNITS[1:]]

    assert plan_fingerprint(UNITS) == base
    assert plan_fingerprint(edited_text) != base
    assert plan_fingerprint(edited_direction) != base
