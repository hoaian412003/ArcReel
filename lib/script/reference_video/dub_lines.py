"""视频单元越南语配音台词（dub_lines）的读时对齐与预算原语。

``DubLine.index`` 指向 unit 正文里按出现顺序派生的第 ``index`` 条发声（台词与画外音合并计序，
见 :func:`derive_utterances`）；``DubLine.source_text`` 记录它落笔时所对应发声的原文。正文被
改动后现行发声与 ``source_text`` 不再相符，该配音行即过期——对齐逻辑只在此一处，API / UI /
校验器共用。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from lib.script.reference_video.script_preview import derive_utterances
from lib.script.script_models import DubLine, DubLineDraft, Utterance
from lib.speech.speech_rate import estimate_spoken_seconds, speech_rate_units_per_second

DubLineStatus = Literal["current", "stale", "missing", "orphan"]


def dub_word_budget(duration_seconds: int | float, language: str = "vi") -> int:
    """该时长内可念完的阅读单位（越南语为词）上限，按语速单一真相源折算，至少 1。"""
    return max(1, int(float(duration_seconds) * speech_rate_units_per_second(language)))


def dub_spoken_seconds(lines: Sequence[Any], language: str = "vi") -> float:
    """一组配音行的估算念白总时长（秒）；元素为 :class:`DubLine` / 草稿 / 含 ``text_vi`` 的映射。"""
    total = 0.0
    for line in lines:
        text = line.get("text_vi") if isinstance(line, Mapping) else getattr(line, "text_vi", None)
        total += estimate_spoken_seconds(text if isinstance(text, str) else None, language)
    return total


def dub_audio_is_current(line: DubLine) -> bool:
    """已登记的音频仍对应现行台词（音色是否变化由合成任务按现行音色判定）。"""
    return bool(line.audio_path) and line.audio_text == line.text_vi


def unit_utterances(unit_text: str) -> list[Utterance]:
    """unit 正文派生出的发声序列（与配音行 ``index`` 同一份派生）。"""
    return derive_utterances(unit_text)[0]


def _normalize(text: str) -> str:
    return " ".join(text.split())


def dub_line_is_current(unit_text: str, line: DubLine | Mapping[str, Any]) -> bool:
    """配音行是否仍与正文现行发声相符：``index`` 存在且 ``source_text`` 与该条发声原文一致。"""
    index = line.get("index") if isinstance(line, Mapping) else line.index
    source = line.get("source_text") if isinstance(line, Mapping) else line.source_text
    utterances = unit_utterances(unit_text)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(utterances):
        return False
    return _normalize(str(source or "")) == _normalize(utterances[index].text)


@dataclass(frozen=True)
class DubLineAlignment:
    """某个发声序号与其配音行的对齐结果。

    ``current``：有配音行且原文相符；``stale``：有配音行但原文已变；``missing``：该发声没有配音行；
    ``orphan``：配音行指向的序号已不存在于正文（此时 ``utterance`` 为 ``None``）。
    """

    index: int
    status: DubLineStatus
    utterance: Utterance | None
    line: DubLine | None


def align_dub_lines(unit_text: str, dub_lines: Sequence[DubLine]) -> list[DubLineAlignment]:
    """按发声序号逐条对齐正文与配音行，先给每条现行发声一项，再追加指向不存在序号的孤儿行。"""
    utterances = unit_utterances(unit_text)
    by_index: dict[int, DubLine] = {}
    for line in dub_lines:
        by_index.setdefault(line.index, line)
    result: list[DubLineAlignment] = []
    for index, utterance in enumerate(utterances):
        line = by_index.get(index)
        if line is None:
            result.append(DubLineAlignment(index, "missing", utterance, None))
        elif _normalize(line.source_text) == _normalize(utterance.text):
            result.append(DubLineAlignment(index, "current", utterance, line))
        else:
            result.append(DubLineAlignment(index, "stale", utterance, line))
    result.extend(
        DubLineAlignment(index, "orphan", None, line)
        for index, line in sorted(by_index.items())
        if index >= len(utterances)
    )
    return result


def dub_lines_need_regeneration(unit_text: str, dub_lines: Sequence[DubLine]) -> bool:
    """有发声但配音行缺失 / 过期 / 孤儿时为真；无发声且无配音行为假。"""
    return any(item.status != "current" for item in align_dub_lines(unit_text, dub_lines))


def build_dub_lines_from_drafts(
    unit_text: str, drafts: Sequence[DubLineDraft], cues: Sequence[Mapping[str, Any]] = ()
) -> list[dict[str, Any]]:
    """把 LLM 产出的配音行补全 ``source_text``（取现行发声原文），序号越界的行 ``source_text`` 留空。

    导演在 ``cues`` 里给了某条发声的配音性别时以导演为准：模型只看译文上下文，导演才知道这句是女声还是男声。
    """
    utterances = unit_utterances(unit_text)
    cue_gender = {cue.get("index"): cue.get("gender") for cue in cues if cue.get("gender")}
    lines: list[dict[str, Any]] = []
    for draft in drafts:
        source = utterances[draft.index].text if 0 <= draft.index < len(utterances) else ""
        lines.append(
            {
                "index": draft.index,
                "speaker": draft.speaker,
                "gender": cue_gender.get(draft.index, draft.gender),
                "source_text": source,
                "text_vi": draft.text_vi,
            }
        )
    return lines


def fill_dub_line_source_texts(unit: dict[str, Any]) -> None:
    """就地补全 unit 配音行缺失的 ``source_text``（取现行发声原文）；已带非空值的行与结构非法的行不动。

    手写 / Agent 批量编辑只给 ``index`` / ``gender`` / ``text_vi`` 时由此落成可判过期的完整配音行。
    """
    lines = unit.get("dub_lines")
    text = unit.get("text")
    if not isinstance(lines, list) or not isinstance(text, str):
        return
    utterances = unit_utterances(text)
    for line in lines:
        if not isinstance(line, dict) or line.get("source_text"):
            continue
        index = line.get("index")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(utterances):
            line["source_text"] = utterances[index].text


def dub_line_violations(
    unit_text: str, duration_seconds: int | float, lines: Sequence[Mapping[str, Any]]
) -> tuple[list[str], list[str]]:
    """校验一个 unit 的配音行，返回 ``(硬违约, 超预算软提示)``，均为面向模型的中文描述。

    硬违约：每条现行发声恰好一行、序号一一对应且按序、``text_vi`` 非空；软提示：单行词数超过
    该 unit 时长折算的预算。
    """
    utterances = unit_utterances(unit_text)
    hard: list[str] = []
    soft: list[str] = []
    indexes = [line.get("index") for line in lines]
    expected = list(range(len(utterances)))
    if indexes != expected:
        hard.append(f"dub_lines 的 index 必须依次为 {expected}（每条台词/画外音恰好一行），收到 {indexes}")
    for line in lines:
        text = line.get("text_vi")
        if not isinstance(text, str) or not text.strip():
            hard.append(f"dub_lines[index={line.get('index')}] 的 text_vi 不能为空")
    budget = dub_word_budget(duration_seconds)
    total = dub_spoken_seconds(lines)
    if total > float(duration_seconds):
        soft.append(f"越南语念白约 {total:.1f} 秒，超过 unit 时长 {duration_seconds} 秒（词数预算约 {budget}）")
    return hard, soft
