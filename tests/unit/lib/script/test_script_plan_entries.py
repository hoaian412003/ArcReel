"""``lib.script.script_plan_entries``：脚本规划条目的读取归一与到正式脚本内容层的投影。"""

from __future__ import annotations

import pytest

from lib.script.script_plan_entries import entry_id_field, plan_entries_from_document, plan_entry_content, plan_variant


def drama_plan_entry(scene_id: str = "E1S01", **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "scene_id": scene_id,
        "duration_seconds": 8,
        "segment_break": False,
        "characters_in_scene": ["主角"],
        "scenes": ["酒馆"],
        "props": [],
        "scene_description": "主角推门而入",
        "utterances": [{"kind": "dialogue", "speaker": "主角", "text": "来一杯"}],
        "source_text": "他推开了酒馆的门。",
    }
    entry.update(overrides)
    return entry


def narration_plan_entry(segment_id: str = "E1S01", **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "segment_id": segment_id,
        "novel_text": "他推开了酒馆的门。",
        "duration_seconds": 6,
        "segment_break": False,
        "characters_in_segment": ["主角"],
        "scenes": ["酒馆"],
        "props": [],
    }
    entry.update(overrides)
    return entry


def reference_plan_entry(unit_id: str = "E1U01", **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "unit_id": unit_id,
        "text": "@[主角] 推门而入",
        "duration_seconds": 4,
        "source_text": "他推开了酒馆的门。",
    }
    entry.update(overrides)
    return entry


PLAN_FACTORIES = {
    "drama": drama_plan_entry,
    "narration": narration_plan_entry,
    "reference_video": reference_plan_entry,
}


class TestPlanVariant:
    @pytest.mark.parametrize(
        ("kind", "id_field"), [("drama", "scene_id"), ("narration", "segment_id"), ("reference_video", "unit_id")]
    )
    def test_entry_id_field_follows_the_skeleton(self, kind: str, id_field: str) -> None:
        assert entry_id_field(kind) == id_field

    def test_unknown_plan_kind_fails_loud(self) -> None:
        with pytest.raises(ValueError, match="未知的脚本规划变体"):
            plan_variant("ad")


class TestPlanEntriesFromDocument:
    @pytest.mark.parametrize("kind", ["drama", "narration", "reference_video"])
    def test_reads_each_variant_items_key(self, kind: str) -> None:
        entry = PLAN_FACTORIES[kind]()
        document = {plan_variant(kind).plan_items_key: [entry]}
        assert len(plan_entries_from_document(kind, document)) == 1

    @pytest.mark.parametrize("kind", ["narration", "reference_video"])
    def test_defaulted_fields_are_filled_like_the_generator_sees_them(self, kind: str) -> None:
        """带草稿模型的两个变体经模型归一：转换与迁移消费的正是归一后的条目。"""
        omitted = {"narration": "segment_break", "reference_video": "source_text"}[kind]
        entry = PLAN_FACTORIES[kind]()
        entry.pop(omitted)
        [normalized] = plan_entries_from_document(kind, {plan_variant(kind).plan_items_key: [entry]})
        assert omitted in normalized

    @pytest.mark.parametrize("kind", ["narration", "reference_video"])
    def test_entries_the_draft_model_rejects_yield_nothing(self, kind: str) -> None:
        """归一失败即「没有可读的条目」，不另造一类错误。"""
        assert plan_entries_from_document(kind, {plan_variant(kind).plan_items_key: [{"乱写": 1}]}) == []

    @pytest.mark.parametrize("document", [None, [], {"scenes": "坏形状"}, {}])
    def test_malformed_document_yields_no_entries(self, document: object) -> None:
        assert plan_entries_from_document("drama", document) == []


class TestPlanEntryContent:
    def test_drama_passes_every_field_through_including_scene_description(self) -> None:
        entry = drama_plan_entry("E1S01", scene_description="雨夜天台，阿离背身而立")
        content = plan_entry_content("drama", entry)
        assert content == entry
        assert content["scene_description"] == "雨夜天台，阿离背身而立"
        assert content is not entry

    def test_narration_passes_every_field_through(self) -> None:
        entry = narration_plan_entry("E1S01")
        assert plan_entry_content("narration", entry) == entry

    def test_reference_video_keeps_only_script_fields(self) -> None:
        entry = reference_plan_entry("E1U01", references=[{"name": "主角"}])
        content = plan_entry_content("reference_video", entry)
        assert content == {
            "unit_id": "E1U01",
            "text": entry["text"],
            "duration_seconds": 4,
            "source_text": entry["source_text"],
        }

    def test_reference_video_carries_dub_lines_but_omits_an_empty_list(self) -> None:
        line = {"index": 0, "speaker": "主角", "gender": "male", "source_text": "来一杯", "text_vi": "Cho tôi một ly."}
        [with_dub] = plan_entries_from_document(
            "reference_video", {"units": [reference_plan_entry("E1U01", dub_lines=[line])]}
        )
        assert plan_entry_content("reference_video", with_dub)["dub_lines"] == [{**line, "start_seconds": None}]
        [without_dub] = plan_entries_from_document("reference_video", {"units": [reference_plan_entry("E1U01")]})
        assert "dub_lines" not in plan_entry_content("reference_video", without_dub)

    def test_unknown_plan_kind_fails_loud(self) -> None:
        with pytest.raises(ValueError, match="未知的脚本规划变体"):
            plan_entry_content("ad", {})


def _directed_reference_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "unit_id": "E1U01",
        "text": "@[张三] 起身",
        "duration_seconds": 8,
        "source_text": "张三起身。",
        "dub_lines": [],
        "unit_type": "tension_build",
        "brief": "张三起身，铺垫危险。",
        "beats": [{"at_pct": 0, "action": "起身"}, {"at_pct": 80, "action": "回头"}],
        "performance": [],
        "must_not_show": "撞击本身",
        "end_frame": "白光铺满画面",
        "continuity_in": "",
        "continuity_out": "",
    }
    entry.update(overrides)
    return entry


def test_reference_projection_carries_non_empty_director_fields():
    """导演说明随单元进入正式脚本，Step 2 与解析预览据此读写；空值字段不落盘。"""
    content = plan_entry_content("reference_video", _directed_reference_entry())
    assert content["unit_type"] == "tension_build"
    assert content["must_not_show"] == "撞击本身"
    assert [beat["at_pct"] for beat in content["beats"]] == [0, 80]  # type: ignore[index]
    assert not {"performance", "continuity_in", "continuity_out", "dub_lines"} & set(content)


def test_reference_projection_of_a_legacy_entry_keeps_its_stored_shape():
    legacy = {"unit_id": "E1U01", "text": "x", "duration_seconds": 8, "source_text": "s"}
    assert plan_entry_content("reference_video", legacy) == legacy
