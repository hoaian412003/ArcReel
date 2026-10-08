"""``lib.script.reference_video.dub_lines``：配音行与正文发声的读时对齐、预算与校验原语。"""

from __future__ import annotations

from lib.script.reference_video.dub_lines import (
    align_dub_lines,
    build_dub_lines_from_drafts,
    dub_line_is_current,
    dub_line_violations,
    dub_lines_need_regeneration,
    dub_word_budget,
    fill_dub_line_source_texts,
)
from lib.script.script_models import DubLine, DubLineDraft

TEXT = "镜头1：@[甲] 推门。\n@[甲]{你好}\n{夜深了}"


def _line(index: int, source_text: str, text_vi: str = "Xin chào.") -> DubLine:
    return DubLine(index=index, speaker=None, gender="male", source_text=source_text, text_vi=text_vi)


class TestAlignment:
    def test_current_line_matches_the_derived_utterance_text(self) -> None:
        assert dub_line_is_current(TEXT, _line(0, "你好"))
        assert dub_line_is_current(TEXT, _line(1, "夜深了"))

    def test_changed_source_or_out_of_range_index_is_not_current(self) -> None:
        assert not dub_line_is_current(TEXT, _line(0, "再见"))
        assert not dub_line_is_current(TEXT, _line(2, "你好"))
        assert not dub_line_is_current(TEXT, {"index": 0, "source_text": "再见"})

    def test_align_reports_per_index_status(self) -> None:
        text = "@[甲]{你好}\n{夜深了}\n{天亮了}"
        result = align_dub_lines(text, [_line(0, "你好"), _line(1, "旧的旁白"), _line(5, "孤儿")])
        assert [(item.index, item.status) for item in result] == [
            (0, "current"),
            (1, "stale"),
            (2, "missing"),
            (5, "orphan"),
        ]

    def test_need_regeneration_only_when_something_is_not_current(self) -> None:
        assert not dub_lines_need_regeneration("无台词的画面", [])
        assert dub_lines_need_regeneration("@[甲]{你好}", [])
        assert not dub_lines_need_regeneration("@[甲]{你好}", [_line(0, "你好")])
        assert dub_lines_need_regeneration("@[甲]{你好}", [_line(0, "你好"), _line(3, "x")])


class TestBuildAndFill:
    def test_drafts_get_source_text_from_the_derived_utterance(self) -> None:
        drafts = [
            DubLineDraft(index=0, speaker="甲", gender="female", text_vi="Chào anh."),
            DubLineDraft(index=9, speaker=None, gender="male", text_vi="Trời tối rồi."),
        ]
        lines = build_dub_lines_from_drafts(TEXT, drafts)
        assert [line["source_text"] for line in lines] == ["你好", ""]
        assert lines[0]["gender"] == "female"

    def test_directors_cue_gender_overrides_the_models_guess(self) -> None:
        drafts = [
            DubLineDraft(index=0, speaker="甲", gender="male", text_vi="Chào anh."),
            DubLineDraft(index=1, speaker=None, gender="male", text_vi="Trời tối rồi."),
        ]
        cues = [{"index": 1, "start_seconds": 5.0, "end_seconds": 8.0, "gender": "female"}]

        lines = build_dub_lines_from_drafts(TEXT, drafts, cues)

        assert [line["gender"] for line in lines] == ["male", "female"]

    def test_fill_only_completes_missing_source_text(self) -> None:
        unit = {
            "text": TEXT,
            "dub_lines": [
                {"index": 0, "gender": "male", "text_vi": "a"},
                {"index": 1, "gender": "male", "text_vi": "b", "source_text": "人工锚"},
                {"index": 7, "gender": "male", "text_vi": "c"},
            ],
        }
        fill_dub_line_source_texts(unit)
        assert [line.get("source_text") for line in unit["dub_lines"]] == ["你好", "人工锚", None]


class TestViolations:
    def test_requires_one_ordered_line_per_utterance(self) -> None:
        hard, _soft = dub_line_violations(TEXT, 8, [{"index": 0, "text_vi": "Chào."}])
        assert hard
        hard, _soft = dub_line_violations(
            TEXT, 8, [{"index": 0, "text_vi": "Chào."}, {"index": 1, "text_vi": "Khuya rồi."}]
        )
        assert hard == []

    def test_blank_translation_is_a_hard_violation(self) -> None:
        hard, _soft = dub_line_violations(TEXT, 8, [{"index": 0, "text_vi": " "}, {"index": 1, "text_vi": "ok"}])
        assert any("text_vi" in message for message in hard)

    def test_over_budget_is_only_a_soft_warning(self) -> None:
        long_line = " ".join(["từ"] * 30)
        hard, soft = dub_line_violations("@[甲]{你好}", 4, [{"index": 0, "text_vi": long_line}])
        assert hard == []
        assert soft

    def test_word_budget_follows_the_vietnamese_speech_rate(self) -> None:
        assert dub_word_budget(8) == 20
        assert dub_word_budget(0) == 1
