"""编剧剧本：模型约束、读写、按场次编辑、审阅指纹与可读渲染。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.script import screenplay


def _scene(**overrides: Any) -> dict[str, Any]:
    return {
        "heading": "内景 · 府门 · 日",
        "summary": "老管家送来侯爷的亲笔信",
        "estimated_seconds": 12,
        "text": "老管家递上书信。\n**老管家**（恭敬）：夫人，这是侯爷的亲笔信。",
        **overrides,
    }


def _draft(scenes: list[dict[str, Any]] | None = None) -> screenplay.ScreenplayDraft:
    return screenplay.ScreenplayDraft.model_validate(
        {
            "title": "边关来信",
            "logline": "一封信带来未解之谜",
            "scenes": scenes
            if scenes is not None
            else [_scene(), _scene(heading="外景 · 官道 · 夜", estimated_seconds=8)],
        }
    )


def test_total_seconds_is_the_sum_of_scene_estimates(tmp_path: Path) -> None:
    data = screenplay.write_screenplay(tmp_path, 1, _draft())

    assert screenplay.total_seconds(data) == 20
    assert screenplay.screenplay_summary(data)["scene_seconds"] == [12, 8]


@pytest.mark.parametrize(
    "scene",
    [
        _scene(estimated_seconds=0),
        _scene(estimated_seconds=601),
        _scene(heading="  "),
        _scene(text=""),
        {**_scene(), "extra": 1},
    ],
)
def test_scene_constraints_reject_unusable_values(scene: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        _draft([scene])


def test_a_screenplay_needs_at_least_one_scene() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        _draft([])


def test_missing_file_reads_as_none(tmp_path: Path) -> None:
    assert screenplay.load_screenplay(tmp_path, 1) is None
    assert screenplay.review_status(None) == "none"


def test_written_screenplay_round_trips_and_waits_for_review(tmp_path: Path) -> None:
    written = screenplay.write_screenplay(tmp_path, 1, _draft())

    loaded = screenplay.load_screenplay(tmp_path, 1)

    assert loaded == written
    assert loaded is not None
    assert loaded["approval"] is None
    assert screenplay.review_status(loaded) == "pending_review"
    assert screenplay.screenplay_path(tmp_path, 1) == tmp_path / "drafts" / "episode_1" / "screenplay.json"


def test_corrupt_file_raises_a_structured_error(tmp_path: Path) -> None:
    path = screenplay.screenplay_path(tmp_path, 1)
    path.parent.mkdir(parents=True)
    path.write_text('{"title": "x"}', encoding="utf-8")

    with pytest.raises(screenplay.ScreenplayError) as excinfo:
        screenplay.load_screenplay(tmp_path, 1)

    assert excinfo.value.code == "screenplay_corrupt"


def test_approval_binds_to_the_content_fingerprint(tmp_path: Path) -> None:
    written = screenplay.write_screenplay(tmp_path, 1, _draft())

    approved = screenplay.approve_screenplay(
        tmp_path, 1, expected_fingerprint=screenplay.screenplay_fingerprint(written)
    )

    assert screenplay.review_status(approved) == "approved"
    assert screenplay.review_status(screenplay.load_screenplay(tmp_path, 1)) == "approved"


def test_approving_a_version_the_reviewer_did_not_see_is_refused(tmp_path: Path) -> None:
    screenplay.write_screenplay(tmp_path, 1, _draft())

    with pytest.raises(screenplay.ScreenplayError) as excinfo:
        screenplay.approve_screenplay(tmp_path, 1, expected_fingerprint="stale")

    assert excinfo.value.code == "screenplay_revision_conflict"


def test_approving_without_a_screenplay_is_refused(tmp_path: Path) -> None:
    with pytest.raises(screenplay.ScreenplayError) as excinfo:
        screenplay.approve_screenplay(tmp_path, 1)

    assert excinfo.value.code == "screenplay_missing"


def test_any_content_edit_invalidates_the_review(tmp_path: Path) -> None:
    written = screenplay.write_screenplay(tmp_path, 1, _draft())
    screenplay.approve_screenplay(tmp_path, 1)

    edited = screenplay.edit_scenes(tmp_path, 1, [{"op": "update", "index": 0, "fields": {"estimated_seconds": 15}}])

    assert edited["scenes"][0]["estimated_seconds"] == 15
    assert screenplay.review_status(edited) == "pending_review"
    assert screenplay.total_seconds(edited) == 23
    assert screenplay.screenplay_fingerprint(edited) != screenplay.screenplay_fingerprint(written)


def test_a_no_op_edit_keeps_the_review(tmp_path: Path) -> None:
    screenplay.write_screenplay(tmp_path, 1, _draft())
    screenplay.approve_screenplay(tmp_path, 1)

    edited = screenplay.edit_scenes(tmp_path, 1, [{"op": "update", "index": 0, "fields": {"estimated_seconds": 12}}])

    assert screenplay.review_status(edited) == "approved"


def test_edit_operations_apply_in_order(tmp_path: Path) -> None:
    screenplay.write_screenplay(tmp_path, 1, _draft())

    edited = screenplay.edit_scenes(
        tmp_path,
        1,
        [
            {"op": "insert", "index": 1, "scene": _scene(heading="内景 · 书房 · 夜")},
            {"op": "delete", "index": 0},
            {"op": "set_meta", "fields": {"title": "新标题"}},
        ],
    )

    assert [scene["heading"] for scene in edited["scenes"]] == ["内景 · 书房 · 夜", "外景 · 官道 · 夜"]
    assert edited["title"] == "新标题"


@pytest.mark.parametrize(
    "operations",
    [
        [{"op": "update", "index": 5, "fields": {"text": "x"}}],
        [{"op": "update", "index": 0, "fields": {"unknown": "x"}}],
        [{"op": "update", "index": 0, "fields": {"estimated_seconds": 0}}],
        [{"op": "insert", "index": 9, "scene": _scene()}],
        [{"op": "insert", "index": 0, "scene": {"heading": "x"}}],
        [{"op": "delete", "index": 0}, {"op": "delete", "index": 0}],
        [{"op": "move", "index": 0}],
        [{"op": "set_meta", "fields": {"scenes": []}}],
    ],
)
def test_an_invalid_operation_rejects_the_whole_batch(tmp_path: Path, operations: list[dict[str, Any]]) -> None:
    before = screenplay.write_screenplay(tmp_path, 1, _draft())

    with pytest.raises(screenplay.ScreenplayError):
        screenplay.edit_scenes(tmp_path, 1, [{"op": "set_meta", "fields": {"title": "不应落盘"}}, *operations])

    assert screenplay.load_screenplay(tmp_path, 1) == before


def test_edit_with_a_stale_fingerprint_is_refused(tmp_path: Path) -> None:
    screenplay.write_screenplay(tmp_path, 1, _draft())

    with pytest.raises(screenplay.ScreenplayError) as excinfo:
        screenplay.edit_scenes(
            tmp_path, 1, [{"op": "set_meta", "fields": {"title": "x"}}], expected_fingerprint="stale"
        )

    assert excinfo.value.code == "screenplay_revision_conflict"


def test_markdown_lists_every_scene_with_its_duration_and_the_total(tmp_path: Path) -> None:
    data = screenplay.write_screenplay(tmp_path, 1, _draft())

    markdown = screenplay.render_markdown(data)

    assert markdown.startswith("# 边关来信")
    assert "第 1 场　内景 · 府门 · 日" in markdown
    assert "第 2 场　外景 · 官道 · 夜" in markdown
    assert "约 12 秒" in markdown
    assert "约 8 秒" in markdown
    assert markdown.rstrip().endswith("**本集预计总时长**：约 20 秒")


def test_a_screenplay_remembers_which_source_it_was_written_from(tmp_path: Path) -> None:
    data = screenplay.write_screenplay(tmp_path, 1, _draft(), source_text="第一版源文")

    assert screenplay.matches_source(data, "第一版源文")
    assert not screenplay.matches_source(data, "分集重划后的源文")


def test_a_screenplay_without_a_recorded_source_is_not_judged_against_it(tmp_path: Path) -> None:
    data = screenplay.write_screenplay(tmp_path, 1, _draft())

    assert screenplay.matches_source(data, "任意源文")


def test_source_digest_survives_edits_and_approval(tmp_path: Path) -> None:
    screenplay.write_screenplay(tmp_path, 1, _draft(), source_text="第一版源文")
    screenplay.approve_screenplay(tmp_path, 1)

    edited = screenplay.edit_scenes(tmp_path, 1, [{"op": "set_meta", "fields": {"title": "新标题"}}])

    assert screenplay.matches_source(edited, "第一版源文")
    assert not screenplay.matches_source(edited, "别的源文")
