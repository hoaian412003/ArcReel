"""参考生视频 script_plan 导演规划的机械校验。"""

from typing import Any

import pytest

from lib.script.reference_video.director_validation import (
    DIRECTOR_PLAN_LABEL,
    has_director_content,
    validate_director_plan,
    validate_unit_direction,
)
from lib.script.reference_video.draft_validation import DraftViolation


def _plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "episode_intent": "让观众先喜欢上他，再察觉他的轻率",
        "arc": [{"beat": "从容的开场", "units": [1]}, {"beat": "危险逼近", "units": [2]}],
        "tone": "从轻松到紧绷",
        "motifs": ["白光"],
    }
    plan.update(overrides)
    return plan


def _unit(**overrides: Any) -> dict[str, Any]:
    unit: dict[str, Any] = {
        "unit_type": "tension_build",
        "brief": "他想念父母，掉头回家给他们惊喜；左侧一辆车突然冲来，事故即将发生。",
        "beats": [
            {"at_pct": 0, "action": "他看一眼仪表盘，嘴角放松"},
            {"at_pct": 25, "action": "他看后视镜，把方向盘打向回家的路"},
            {"at_pct": 80, "action": "左侧车窗外一道白光急速变亮"},
        ],
        "performance": [{"character": "陈汉升", "note": "先温和，最后一下猛地偏头"}],
        "must_not_show": "撞击本身",
        "end_frame": "刺眼白光从左侧铺满画面",
        "continuity_in": "车内，雨夜",
        "continuity_out": "白光铺满画面，镜头震动",
    }
    unit.update(overrides)
    return unit


def _unit_problems(unit: dict[str, Any], *, is_last: bool = False) -> str:
    with pytest.raises(DraftViolation) as exc:
        validate_unit_direction("unit E1U02", unit, is_last=is_last)
    assert exc.value.code == "director_unit_invalid"
    assert exc.value.label == "unit E1U02"
    return str(exc.value)


def test_complete_plan_and_unit_pass():
    assert validate_director_plan(_plan(), unit_count=2) is None
    assert validate_unit_direction("unit E1U02", _unit(), is_last=False) is None


def test_missing_plan_is_reported_at_episode_level():
    with pytest.raises(DraftViolation) as exc:
        validate_director_plan(None, unit_count=2)
    assert exc.value.code == "director_plan_missing"
    assert exc.value.label == DIRECTOR_PLAN_LABEL


def test_thin_plan_lists_every_problem():
    with pytest.raises(DraftViolation) as exc:
        validate_director_plan(
            _plan(episode_intent=" ", tone="", arc=[{"beat": "唯一一拍", "units": [3]}]), unit_count=2
        )
    message = str(exc.value)
    assert exc.value.code == "director_plan_invalid"
    assert "episode_intent 为空" in message
    assert "tone 为空" in message
    assert "arc 只有 1 拍" in message
    assert "不存在的 unit 序号 [3]" in message


def test_arc_beat_without_units_is_rejected():
    with pytest.raises(DraftViolation, match="没有指明承载它的 unit"):
        validate_director_plan(_plan(arc=[{"beat": "开场", "units": [1]}, {"beat": "转折", "units": []}]), 2)


def test_build_up_unit_must_name_what_it_holds_back():
    assert "须写 must_not_show" in _unit_problems(_unit(must_not_show=""))


def test_non_build_up_unit_may_omit_must_not_show():
    assert validate_unit_direction("unit E1U02", _unit(unit_type="romance", must_not_show=""), is_last=False) is None


def test_end_frame_is_required_except_on_the_last_unit():
    assert "end_frame 为空" in _unit_problems(_unit(end_frame=""))
    assert validate_unit_direction("unit E1U02", _unit(end_frame=""), is_last=True) is None


@pytest.mark.parametrize(
    ("beats", "expected"),
    [
        ([{"at_pct": 0, "action": "起身"}], "须为 2-5 个"),
        ([{"at_pct": 50, "action": "起身"}, {"at_pct": 50, "action": "出门"}], "严格递增"),
        ([{"at_pct": 0, "action": "起身"}, {"at_pct": 60, "action": " "}], "action 为空"),
    ],
)
def test_beats_count_order_and_content(beats: list[dict[str, Any]], expected: str):
    assert expected in _unit_problems(_unit(beats=beats))


def test_empty_unit_reports_all_gaps_in_one_violation():
    message = _unit_problems({"unit_type": None, "brief": "紧张", "beats": []})
    for fragment in ("unit_type 为空", "brief 过短", "beats 有 0 个节点", "continuity_in 为空", "continuity_out 为空"):
        assert fragment in message


def test_director_content_detection_ignores_empty_defaults():
    empty_unit = {"unit_type": None, "brief": "", "beats": [], "performance": [], "end_frame": ""}
    assert not has_director_content(None, [empty_unit])
    assert has_director_content(None, [{**empty_unit, "brief": "有内容"}])
    assert has_director_content(_plan(), [empty_unit])
