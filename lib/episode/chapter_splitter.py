"""按章节标题把整本小说切成章（纯正则，不调模型）。

只认「独占一行、较短」的标题，避免正文里「他读到第三章」之类的提及被当成分界。候选标题格式按语言族分组
（中文「第N章 / 回 / 节 / 集」、越南语「Chương / Hồi N」、英文「Chapter N」），取命中最多且至少两处的一族，
各族不混用，混用会把偶然命中的另一族当分界。第一个标题之前的前言并入第一章，不单独成章。
结果只描述怎么切，不落盘；落盘走 :func:`lib.episode.episode_upload.upload_episode_sources`。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from lib.episode.episode_ledger import normalize_source_text

#: 标题行最长字符数：更长的行是正文，不是标题。
MAX_HEADING_CHARS = 80

#: 过短章节（字符数）的提示阈值；过长按中位数倍数提示。
SHORT_CHAPTER_CHARS = 200
LONG_CHAPTER_RATIO = 3.0

_CN_NUM = r"[0-9０-９零〇一二三四五六七八九十百千两]+"
_VI_NUM = (
    r"(?:\d+|[IVXLC]+|một|hai|ba|bốn|tư|năm|sáu|bảy|tám|chín|mười(?:\s+(?:một|hai|ba|bốn|năm|sáu|bảy|tám|chín))?"
    r"|hai\s+mươi(?:\s+\w+)?)"
)
_EN_NUM = r"(?:\d+|[IVXLC]+|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Eleven|Twelve)"
_PREFIX = r"^[ \t　]*(?:#{1,6}[ \t]*)?(?:\*\*)?[ \t]*"

#: 候选标题族：名字 → 编译后的行首正则。命中行即标题行，整行为标题文本。
_FAMILIES: dict[str, re.Pattern[str]] = {
    "zh": re.compile(rf"{_PREFIX}第[ \t]*{_CN_NUM}[ \t]*[章回节集卷][^\n]*$", re.MULTILINE),
    "vi": re.compile(
        rf"{_PREFIX}(?:Chương|CHƯƠNG|Hồi|HỒI)[ \t]*(?:thứ[ \t]+)?{_VI_NUM}\b[^\n]*$", re.MULTILINE | re.IGNORECASE
    ),
    "en": re.compile(rf"{_PREFIX}(?:Chapter|CHAPTER)[ \t]+{_EN_NUM}\b[^\n]*$", re.MULTILINE | re.IGNORECASE),
}


@dataclass(frozen=True)
class Chapter:
    """一章：``number`` 是在整本书中的序号（从 1 起），``heading`` 是标题行原文。"""

    number: int
    heading: str
    text: str

    @property
    def title(self) -> str:
        return re.sub(r"^[#*\s　]+|[*\s　]+$", "", self.heading)

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class EpisodeDraft:
    """一集：由 ``chapters_per_episode`` 章拼成，标题取首章标题。"""

    episode: int
    title: str
    text: str
    chapters: tuple[int, ...]

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class ChapterSplit:
    family: str | None
    chapters: list[Chapter] = field(default_factory=list)
    preface_chars: int = 0
    warnings: list[str] = field(default_factory=list)


def _heading_matches(text: str, family: str) -> list[re.Match[str]]:
    return [m for m in _FAMILIES[family].finditer(text) if len(m.group().strip()) <= MAX_HEADING_CHARS]


def split_chapters(text: str) -> ChapterSplit:
    """把整本小说按章节标题切开；没有至少两处同族标题时返回空章节列表与说明。"""
    body = normalize_source_text(text)
    matches_by_family = {name: _heading_matches(body, name) for name in _FAMILIES}
    family, matches = max(matches_by_family.items(), key=lambda item: len(item[1]))
    if len(matches) < 2:
        return ChapterSplit(
            family=None,
            warnings=["no_chapter_headings"],
        )
    chapters: list[Chapter] = []
    preface = body[: matches[0].start()].strip()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        chunk = body[match.start() : end].strip()
        if index == 0 and preface:
            chunk = f"{preface}\n\n{chunk}"
        chapters.append(Chapter(number=index + 1, heading=match.group().strip(), text=chunk))
    split = ChapterSplit(family=family, chapters=chapters, preface_chars=len(preface))
    split.warnings = _warnings(split)
    return split


def _warnings(split: ChapterSplit) -> list[str]:
    warnings: list[str] = []
    sizes = [chapter.chars for chapter in split.chapters]
    short = [c.number for c in split.chapters if c.chars < SHORT_CHAPTER_CHARS]
    if short:
        warnings.append(f"short_chapters:{','.join(str(n) for n in short[:10])}")
    median = statistics.median(sizes)
    long = [c.number for c in split.chapters if median > 0 and c.chars > median * LONG_CHAPTER_RATIO]
    if long:
        warnings.append(f"long_chapters:{','.join(str(n) for n in long[:10])}")
    headings = [c.heading for c in split.chapters]
    if len(set(headings)) != len(headings):
        warnings.append("duplicate_headings")
    return warnings


def group_into_episodes(
    chapters: list[Chapter],
    *,
    chapters_per_episode: int = 1,
    start_episode: int = 1,
) -> list[EpisodeDraft]:
    """每 ``chapters_per_episode`` 章拼成一集（末集可不足），集号从 ``start_episode`` 起。"""
    size = max(1, chapters_per_episode)
    drafts: list[EpisodeDraft] = []
    for offset in range(0, len(chapters), size):
        group = chapters[offset : offset + size]
        drafts.append(
            EpisodeDraft(
                episode=start_episode + offset // size,
                title=group[0].title,
                text="\n\n".join(chapter.text for chapter in group),
                chapters=tuple(chapter.number for chapter in group),
            )
        )
    return drafts


__all__ = ["Chapter", "ChapterSplit", "EpisodeDraft", "group_into_episodes", "split_chapters"]
