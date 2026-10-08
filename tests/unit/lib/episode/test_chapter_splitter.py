"""章节切分：各语言标题格式、正文提及不当分界、前言并入首章、按章分组成集。"""

from __future__ import annotations

import pytest

from lib.episode.chapter_splitter import group_into_episodes, split_chapters


def _titles(text: str) -> list[str]:
    return [chapter.title for chapter in split_chapters(text).chapters]


@pytest.mark.parametrize(
    ("text", "family", "titles"),
    [
        (
            "第一章 山村\n正文一。\n第二章 下山\n正文二。\n第十二回 终\n末",
            "zh",
            ["第一章 山村", "第二章 下山", "第十二回 终"],
        ),
        (
            "Chương 1: Trọng sinh\nA\nChương thứ hai: Tiếp\nB\nCHƯƠNG III\nC\nChương mười hai\nD",
            "vi",
            ["Chương 1: Trọng sinh", "Chương thứ hai: Tiếp", "CHƯƠNG III", "Chương mười hai"],
        ),
        (
            "# Chapter 1\nx\n## Chapter 2 - b\ny\nChapter IV\nz",
            "en",
            ["Chapter 1", "Chapter 2 - b", "Chapter IV"],
        ),
    ],
    ids=["zh", "vi", "en-markdown"],
)
def test_headings_of_each_language_family_are_recognised(text: str, family: str, titles: list[str]) -> None:
    split = split_chapters(text)

    assert split.family == family
    assert [chapter.title for chapter in split.chapters] == titles
    assert [chapter.number for chapter in split.chapters] == list(range(1, len(titles) + 1))


def test_mentions_inside_prose_and_long_lines_are_not_boundaries() -> None:
    text = (
        "Chương 1: Mở đầu\nChương trình khuyến mãi không phải tiêu đề.\n"
        "Chương 2: Tiếp\nTrong thư có dòng 'Chương 3 sẽ kể về chuyện cũ của ông ấy "
        + "rất dài " * 30
        + "'\nChương 3: Hết\nx"
    )

    assert _titles(text) == ["Chương 1: Mở đầu", "Chương 2: Tiếp", "Chương 3: Hết"]


def test_preface_is_merged_into_the_first_chapter() -> None:
    split = split_chapters("Lời mở đầu của tác giả.\n\nChương 1: A\nnội dung A\nChương 2: B\nnội dung B")

    assert split.preface_chars == len("Lời mở đầu của tác giả.")
    assert split.chapters[0].text.startswith("Lời mở đầu của tác giả.")
    assert "Chương 1: A" in split.chapters[0].text
    assert "Chương 2" not in split.chapters[0].text


def test_the_family_with_most_headings_wins_and_families_are_not_mixed() -> None:
    text = "第一章 甲\na\n第二章 乙\nb\nChapter 1\nstray\n第三章 丙\nc"

    split = split_chapters(text)

    assert split.family == "zh"
    assert len(split.chapters) == 3
    assert "Chapter 1" in split.chapters[1].text


@pytest.mark.parametrize("text", ["没有任何标题的文字。", "第一章 只有一个标题\n正文"])
def test_fewer_than_two_headings_means_no_split(text: str) -> None:
    split = split_chapters(text)

    assert split.chapters == []
    assert split.family is None
    assert split.warnings == ["no_chapter_headings"]


def test_short_long_and_duplicate_chapters_are_reported() -> None:
    long_body = "字" * 5000
    text = f"第一章 甲\n{long_body}\n第二章 乙\n短\n第三章 乙\n短\n第二章 乙\n{'字' * 300}"

    warnings = split_chapters(text).warnings

    assert any(w.startswith("short_chapters:2,3") for w in warnings)
    assert any(w.startswith("long_chapters:1") for w in warnings)
    assert "duplicate_headings" in warnings


def test_grouping_joins_chapters_and_numbers_episodes_from_the_start_episode() -> None:
    chapters = split_chapters("第一章 甲\na\n第二章 乙\nb\n第三章 丙\nc").chapters

    drafts = group_into_episodes(chapters, chapters_per_episode=2, start_episode=5)

    assert [(d.episode, d.title, d.chapters) for d in drafts] == [(5, "第一章 甲", (1, 2)), (6, "第三章 丙", (3,))]
    assert drafts[0].text == "第一章 甲\na\n\n第二章 乙\nb"
