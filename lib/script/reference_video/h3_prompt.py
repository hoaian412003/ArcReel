"""MiniMax H3 全参考（Ref2VA）提示词：六段载荷的指纹、机械校验与标签解析渲染。

六段载荷（:class:`lib.script.script_models.H3Prompt`）是单元 ``text`` 的派生物：``text`` 仍是
引用与发声的内容契约（参考图、声音绑定、时长准入都从它派生），六段只负责把同一份内容写成 H3
提示词指南要求的英文结构。两者之间靠三条机械契约保持一致：

- 指纹：载荷记下写入时的 ``text`` 指纹，``text`` 被改动后载荷即过期，须重新生成；
- 占位：提示词编写不写数字标签，资产以 ``@[名称]`` 占位，参考图以 ``<Picture @[名称]>``、
  参考音频以 ``<Audio @[名称]>`` 占位，生成时按实际随请求发出的顺序解析成 ``<Subject N>`` /
  ``<Picture N>`` / ``<Audio N>``；
- 台词：``text`` 里的角色台词逐字出现在 ``detailed_description`` 的 ``<d>`` 中，画外音不进
  ``<d>``（旁白只经 TTS 与后期配音交付）。

写作规范出处：MiniMax H3 提示词写作指南（https://github.com/MiniMax-AI/MiniMax-H3 ，
``skills/h3-prompt-writing``）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Any

from lib.keyframes.consume import with_opening_frame
from lib.project.asset_types import asset_name_comparison_key
from lib.prompts.video_prompt_profile import h3_prompt_language
from lib.script.draft_violation import DraftViolation
from lib.script.reference_video.script_preview import derive_utterances
from lib.script.reference_video.text_parser import extract_mentions, resolve_references
from lib.script.script_models import H3Prompt, ReferenceResource

#: 六段的固定顺序与字段名，输出格式要求逐字保留。
SECTION_FIELDS: tuple[str, ...] = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)

#: H3 端点的提示词长度上限（字符）。与 ``minimax-h3.json`` 的 ``max_prompt_chars`` 同值。
H3_MAX_PROMPT_CHARS = 7000

#: 「重新生成 H3 提示词」时追加给模型的附加指令：单元正文已认可，只写载荷。
KEEP_TEXT_INSTRUCTION = (
    "本次只需要重写每个 unit 的 h3_prompt：`text` 请逐字原样返回、一个字都不要改，"
    "h3_prompt 依据这份 `text` 书写，并严格遵守上面的全部约束。"
)

_PICTURE_TOKEN = re.compile(r"<Picture @\[([^\[\]]+)\]>")
_AUDIO_TOKEN = re.compile(r"<Audio @\[([^\[\]]+)\]>")
_SUBJECT_TOKEN = re.compile(r"@\[([^\[\]]+)\]")
_NUMERIC_LABEL = re.compile(r"<(?:Subject|Picture|Audio|Video) \d+>")
_DIALOGUE_BLOCK = re.compile(r"<d>\s*\[[^\]]*\]\s*(.*?)</d>", re.DOTALL)
_DIALOGUE_ANY = re.compile(r"<d>.*?</d>", re.DOTALL)
_QUOTED_TEXT = re.compile(r'"[^"\n]*"')
_SHOT_MARKER = re.compile(r"\[Shot (\d+)\]([^\[]{0,40})")
_SHOT_TIME = re.compile(r"^\s*At (\d{2}):(\d{2})\.(\d{3})\b")
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")

#: 提示词编写不得直接写数字标签；出现即说明编号没有交给渲染期。
CODE_RAW_LABEL = "h3_raw_label"
CODE_UNKNOWN_REFERENCE = "h3_unknown_reference"
CODE_SUBJECT_UNDEFINED = "h3_subject_undefined"
CODE_SHOT_STRUCTURE = "h3_shot_structure"
CODE_DIALOGUE_MISMATCH = "h3_dialogue_mismatch"
CODE_NOT_ENGLISH = "h3_not_english"
CODE_TOO_LONG = "h3_too_long"


class H3PromptUnavailable(ValueError):
    """单元没有可下发的 H3 提示词。``code`` 是机读原因，``params`` 供界面文案插值。"""

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        super().__init__(f"{code}: {params}" if params else code)


UNAVAILABLE_MISSING = "h3_prompt_missing"
UNAVAILABLE_STALE = "h3_prompt_stale"
UNAVAILABLE_UNRESOLVED = "h3_prompt_unresolved_reference"
UNAVAILABLE_TOO_LONG = "h3_prompt_too_long"


def source_text_hash(text: str) -> str:
    """单元 ``text`` 的指纹：NFC 归一并去首尾空白后取 SHA-256 前 16 位。"""
    normalized = unicodedata.normalize("NFC", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def bind_h3_prompt(text: str, sections: dict[str, str]) -> H3Prompt:
    """把六段正文与其所依据的 ``text`` 指纹绑成载荷。"""
    return H3Prompt.model_validate(
        {**{field: sections[field] for field in SECTION_FIELDS}, "source_text_hash": source_text_hash(text)}
    )


def load_h3_prompt(unit: dict[str, Any]) -> H3Prompt:
    """取单元当前有效的 H3 载荷；缺失或已随 ``text`` 过期时抛 :class:`H3PromptUnavailable`。"""
    raw = unit.get("h3_prompt")
    if not isinstance(raw, dict):
        raise H3PromptUnavailable(UNAVAILABLE_MISSING)
    try:
        payload = H3Prompt.model_validate(raw)
    except ValueError as exc:
        raise H3PromptUnavailable(UNAVAILABLE_MISSING) from exc
    text = unit.get("text")
    if payload.source_text_hash != source_text_hash(text if isinstance(text, str) else ""):
        raise H3PromptUnavailable(UNAVAILABLE_STALE)
    return payload


def _key(name: str) -> str:
    return asset_name_comparison_key(name)


def _base_speaker(name: str) -> str:
    """``本体/衍生`` 形态的引用取本体名：声音属于身份，衍生共享本体的参考音频。"""
    return name.split("/", 1)[0]


def _referenced_names(sections: dict[str, str]) -> list[tuple[str, str]]:
    """六段中出现的全部占位引用：``(所在段, 名称)``，含 Picture / Audio 占位内的名称。"""
    found: list[tuple[str, str]] = []
    for field, body in sections.items():
        found.extend((field, name) for name in _SUBJECT_TOKEN.findall(body))
    return found


def _english_residue(body: str) -> str:
    """去掉允许保留原语言的部分（``<d>`` 台词、双引号内的画面文字）与占位引用后的残余正文。"""
    residue = _DIALOGUE_ANY.sub(" ", body)
    residue = _QUOTED_TEXT.sub(" ", residue)
    return _SUBJECT_TOKEN.sub(" ", residue)


def _normalize_line(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _dialogue_lines(text: str) -> list[str]:
    utterances, _warnings = derive_utterances(text)
    return [_normalize_line(item.text) for item in utterances if item.kind == "dialogue"]


def _voiceover_lines(text: str) -> list[str]:
    utterances, _warnings = derive_utterances(text)
    return [_normalize_line(item.text) for item in utterances if item.kind == "voiceover"]


def _check_shots(label: str, detailed: str, duration_seconds: int) -> None:
    markers = [(int(match.group(1)), match.group(2)) for match in _SHOT_MARKER.finditer(detailed)]
    if not markers:
        raise DraftViolation(
            f"{label} 的 detailed_description 没有 [Shot N] 分镜标记；按 [Shot 1]、[Shot 2] At MM:SS.mmm, … 逐镜书写",
            code=CODE_SHOT_STRUCTURE,
            label=label,
        )
    if [number for number, _rest in markers] != list(range(1, len(markers) + 1)):
        raise DraftViolation(
            f"{label} 的分镜编号须从 1 起连续递增，现为 {[number for number, _ in markers]}",
            code=CODE_SHOT_STRUCTURE,
            label=label,
        )
    previous_ms = 0
    for number, rest in markers:
        match = _SHOT_TIME.match(rest)
        if number == 1:
            if match:
                raise DraftViolation(f"{label} 的 [Shot 1] 不写时间戳", code=CODE_SHOT_STRUCTURE, label=label)
            continue
        if not match:
            raise DraftViolation(
                f"{label} 的 [Shot {number}] 须写作 `[Shot {number}] At MM:SS.mmm, …`",
                code=CODE_SHOT_STRUCTURE,
                label=label,
            )
        minutes, seconds, millis = (int(part) for part in match.groups())
        cut_ms = (minutes * 60 + seconds) * 1000 + millis
        if cut_ms <= previous_ms:
            raise DraftViolation(
                f"{label} 的 [Shot {number}] 切入时间须严格晚于上一镜", code=CODE_SHOT_STRUCTURE, label=label
            )
        if duration_seconds > 0 and cut_ms >= duration_seconds * 1000:
            raise DraftViolation(
                f"{label} 的 [Shot {number}] 切入时间 {cut_ms / 1000:.3f}s 超出单元时长 {duration_seconds}s",
                code=CODE_SHOT_STRUCTURE,
                label=label,
            )
        previous_ms = cut_ms


def _check_dialogue(label: str, text: str, detailed: str) -> None:
    expected = _dialogue_lines(text)
    actual = [_normalize_line(body) for body in _DIALOGUE_BLOCK.findall(detailed)]
    if Counter(actual) != Counter(expected):
        raise DraftViolation(
            f"{label} 的 detailed_description 里 <d> 台词与单元正文的角色台词不一致"
            f"（正文 {len(expected)} 条，<d> {len(actual)} 条）；角色台词须逐字放进 <d>[语言] …</d>，"
            "画外音不进 <d>，也不改词",
            code=CODE_DIALOGUE_MISMATCH,
            label=label,
        )
    spoken_over = set(_voiceover_lines(text))
    leaked = [line for line in actual if line in spoken_over]
    if leaked:
        raise DraftViolation(
            f"{label} 的画外音「{leaked[0]}」被写进了 <d>；画外音只经 TTS 与后期配音交付，不进视频提示词",
            code=CODE_DIALOGUE_MISMATCH,
            label=label,
        )


def validate_h3_prompt(
    label: str,
    text: str,
    sections: dict[str, str],
    *,
    duration_seconds: int,
    project: dict,
) -> None:
    """机械校验六段载荷与单元 ``text`` 的一致性；任一违约抛 :class:`DraftViolation`。"""
    for field, body in sections.items():
        if _NUMERIC_LABEL.search(body):
            raise DraftViolation(
                f"{label} 的 {field} 直接写了数字标签；资产用 @[名称]、参考图用 <Picture @[名称]>、"
                "参考音频用 <Audio @[名称]> 占位，编号由生成时解析",
                code=CODE_RAW_LABEL,
                label=label,
            )

    registered, _missing = resolve_references(extract_mentions(text), project)
    allowed = {ref.name for ref in registered}
    for field, name in _referenced_names(sections):
        if _key(name) not in allowed and _key(_base_speaker(name)) not in allowed:
            raise DraftViolation(
                f"{label} 的 {field} 引用了单元正文没有 @ 引用的资产「{name}」；"
                "参考图由正文的 @[名称] 决定，六段只能引用正文已引用的资产",
                code=CODE_UNKNOWN_REFERENCE,
                label=label,
            )

    defined = {_key(name) for name in _SUBJECT_TOKEN.findall(sections["subject_definitions"])}
    undefined = [ref.name for ref in registered if ref.name not in defined]
    if undefined:
        raise DraftViolation(
            f"{label} 的 subject_definitions 缺少资产「{undefined[0]}」的定义；正文引用的每个资产都要定义一行",
            code=CODE_SUBJECT_UNDEFINED,
            label=label,
        )

    _check_shots(label, sections["detailed_description"], duration_seconds)
    _check_dialogue(label, text, sections["detailed_description"])

    for field, body in sections.items():
        if h3_prompt_language(project) == "en" and _CJK.search(_english_residue(body)):
            raise DraftViolation(
                f"{label} 的 {field} 含非英文正文；六段一律写英文，仅 <d> 台词与画面上可见的文字保留原语言",
                code=CODE_NOT_ENGLISH,
                label=label,
            )

    if sum(len(body) for body in sections.values()) > H3_MAX_PROMPT_CHARS:
        raise DraftViolation(
            f"{label} 的六段合计超过 {H3_MAX_PROMPT_CHARS} 字符，超出 H3 端点的提示词上限",
            code=CODE_TOO_LONG,
            label=label,
        )


def _resolve(
    body: str,
    *,
    subject_no: dict[str, int],
    picture_no: dict[str, int],
    audio_no: dict[str, int],
    field: str,
) -> str:
    def picture(match: re.Match[str]) -> str:
        name = _key(match.group(1))
        if name not in picture_no:
            raise H3PromptUnavailable(UNAVAILABLE_UNRESOLVED, name=match.group(1), field=field, kind="picture")
        return f"<Picture {picture_no[name]}>"

    def audio(match: re.Match[str]) -> str:
        name = _key(_base_speaker(match.group(1)))
        if name not in audio_no:
            raise H3PromptUnavailable(UNAVAILABLE_UNRESOLVED, name=match.group(1), field=field, kind="audio")
        return f"<Audio {audio_no[name]}>"

    def subject(match: re.Match[str]) -> str:
        name = _key(match.group(1))
        if name not in subject_no:
            raise H3PromptUnavailable(UNAVAILABLE_UNRESOLVED, name=match.group(1), field=field, kind="subject")
        return f"<Subject {subject_no[name]}>"

    body = _PICTURE_TOKEN.sub(picture, body)
    body = _AUDIO_TOKEN.sub(audio, body)
    return _SUBJECT_TOKEN.sub(subject, body)


def render_h3_prompt(
    h3: H3Prompt,
    text: str,
    project: dict,
    references: Sequence[ReferenceResource],
    audio_speakers: Sequence[str],
) -> str:
    """把六段载荷按实际随请求发出的素材解析成可下发的提示词。

    ``<Subject N>`` 按正文 ``@[名称]`` 首现顺序编号，``<Picture N>`` 与 ``references``（实发参考图）
    同序，``<Audio N>`` 与 ``audio_speakers``（实发参考音频）同序。占位解析不出（如参考图被能力上限
    裁掉、参考音频不再可用）抛 :class:`H3PromptUnavailable`，不静默降级成没有标签的文本。
    """
    registered, _missing = resolve_references(extract_mentions(text), project)
    subject_no = {ref.name: index for index, ref in enumerate(registered, start=1)}
    picture_no = {_key(ref.name): index for index, ref in enumerate(references, start=1)}
    audio_no = {_key(name): index for index, name in enumerate(audio_speakers, start=1)}

    sections = {field: getattr(h3, field) for field in SECTION_FIELDS}
    if any(ref.type == "keyframe" for ref in references):
        sections = with_opening_frame(sections)

    blocks = []
    for field in SECTION_FIELDS:
        body = _resolve(
            sections[field].strip(),
            subject_no=subject_no,
            picture_no=picture_no,
            audio_no=audio_no,
            field=field,
        )
        blocks.append(f"{field}:\n{body}")
    prompt = "\n\n".join(blocks)
    if len(prompt) > H3_MAX_PROMPT_CHARS:
        raise H3PromptUnavailable(UNAVAILABLE_TOO_LONG, length=len(prompt), limit=H3_MAX_PROMPT_CHARS)
    return prompt


def uses_audio_labels(h3: H3Prompt) -> bool:
    """载荷是否引用了参考音频标签；发出了参考音频却没有任何 ``<Audio …>`` 引用时，模型不知道该怎么用它们。"""
    return any(_AUDIO_TOKEN.search(getattr(h3, field)) for field in SECTION_FIELDS)


def preflight_h3_prompt(
    unit: dict[str, Any], project: dict, references: Sequence[ReferenceResource]
) -> H3PromptUnavailable | None:
    """准入侧的只读预检：载荷缺失 / 过期、``<Picture …>`` 引用的参考图没有随请求发出、超长时返回原因。

    参考音频要等声音绑定确定后才知道，留给渲染期判定；这里把每个 ``<Audio …>`` 视为可解析。
    """
    try:
        h3 = load_h3_prompt(unit)
        text = unit.get("text")
        audio_names = [name for field in SECTION_FIELDS for name in _AUDIO_TOKEN.findall(getattr(h3, field))]
        render_h3_prompt(h3, text if isinstance(text, str) else "", project, references, audio_names)
    except H3PromptUnavailable as exc:
        return exc
    return None


__all__ = [
    "H3_MAX_PROMPT_CHARS",
    "KEEP_TEXT_INSTRUCTION",
    "SECTION_FIELDS",
    "H3PromptUnavailable",
    "bind_h3_prompt",
    "load_h3_prompt",
    "preflight_h3_prompt",
    "render_h3_prompt",
    "source_text_hash",
    "uses_audio_labels",
    "validate_h3_prompt",
]
