"""导演报告：由正式脚本里的导演数据确定性地派生出的只读事实。

报告把导演步骤写入的内容原样摆出来供人审阅：逐条目的镜头与意图、表演、衔接，整集的角度分布，
以及机械可判的提示（连续同角度、衔接断档、出场角色没写表演、镜头缺意图）。意图文字取自导演
当时写下的 ``camera.intent``，报告不事后编造理由；同一份脚本永远得到同一份报告。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from lib.script.script_skeleton import SKELETONS

#: 连续同角度达到这个条数才提示：两条相邻同角度是常见的正反打，三条起才显单调。
REPEATED_ANGLE_RUN = 3

#: 报告里条目描述的最大字数，超出截断并加省略号。
DESCRIPTION_MAX_CHARS = 120

WARNING_REPEATED_ANGLE = "repeated_angle"
WARNING_CONTINUITY_GAP = "continuity_gap"
WARNING_MISSING_PERFORMANCE = "missing_performance"
WARNING_MISSING_INTENT = "missing_intent"
WARNING_MISSING_DIRECTION = "missing_direction"
WARNING_MISSING_VOICE = "missing_voice"

#: 参考生视频单元的骨架种类；它的导演说明是单元上的扁平字段，报告行形状与分镜路线不同。
REFERENCE_KIND = "video_units"

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)
_CJK_RE = re.compile(r"[㐀-鿿]")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _tokens(text: str) -> set[str]:
    """衔接对照用的词元：非 CJK 取单词，CJK 取相邻二字（中文无空格分词）。"""
    tokens: set[str] = set()
    for word in _WORD_RE.findall(text.casefold()):
        if _CJK_RE.search(word):
            chars = [char for char in word if _CJK_RE.match(char)]
            tokens.update("".join(chars[i : i + 2]) for i in range(max(len(chars) - 1, 0)))
        else:
            tokens.add(word)
    return tokens


def continuity_connects(previous_out: str, next_in: str) -> bool:
    """上一条的结束状态与下一条的开场状态是否有可对上的词元；任一为空视为没对上。"""
    left, right = _tokens(previous_out), _tokens(next_in)
    return bool(left and right and left & right)


def _description(entry: Mapping[str, Any], kind: str) -> str:
    keys = ("novel_text",) if kind == "segments" else ("scene_description", "source_text")
    for key in keys:
        text = _text(entry.get(key))
        if text:
            return text if len(text) <= DESCRIPTION_MAX_CHARS else text[:DESCRIPTION_MAX_CHARS].rstrip() + "…"
    return ""


def _row(entry: Mapping[str, Any], kind: str, id_field: str) -> dict[str, Any]:
    raw_direction = entry.get("direction")
    direction: Mapping[str, Any] = raw_direction if isinstance(raw_direction, Mapping) else {}
    raw_camera = direction.get("camera")
    camera: Mapping[str, Any] = raw_camera if isinstance(raw_camera, Mapping) else {}
    raw_performance = direction.get("performance")
    performance = [
        {"character": _text(item.get("character")), "note": _text(item.get("note"))}
        for item in (raw_performance if isinstance(raw_performance, list) else [])
        if isinstance(item, Mapping)
    ]
    return {
        "id": str(entry.get(id_field)),
        "description": _description(entry, kind),
        "camera": {key: _text(camera.get(key)) for key in ("angle", "shot_size", "movement", "intent")},
        "performance": performance,
        "continuity_in": _text(direction.get("continuity_in")),
        "continuity_out": _text(direction.get("continuity_out")),
    }


def _warning(code: str, entry_ids: Sequence[str], message: str) -> dict[str, Any]:
    return {"code": code, "entry_ids": list(entry_ids), "message": message}


def _repeated_angle_warnings(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    run: list[Mapping[str, Any]] = []

    def flush() -> None:
        if len(run) >= REPEATED_ANGLE_RUN:
            angle = run[0]["camera"]["angle"]
            warnings.append(
                _warning(
                    WARNING_REPEATED_ANGLE,
                    [row["id"] for row in run],
                    f"{len(run)} consecutive entries share the angle '{angle}'",
                )
            )

    for row in rows:
        angle = row["camera"]["angle"].casefold()
        if run and angle and angle == run[0]["camera"]["angle"].casefold():
            run.append(row)
            continue
        flush()
        run = [row] if angle else []
    flush()
    return warnings


def build_director_report(script: Mapping[str, Any], kind: str) -> dict[str, Any] | None:
    """由正式脚本构造导演报告；脚本既没有集级规划也没有任何条目导演说明时返回 ``None``。

    ``kind`` 是骨架种类（``segments`` / ``scenes`` / ``video_units``）。逐条目按剧本顺序排列；条目缺导演说明时
    镜头字段为空并给出 ``missing_direction`` 提示，保证行形状稳定。报告带 ``route``（``storyboard`` /
    ``reference``）供界面按路线选择呈现方式。
    """
    if kind == REFERENCE_KIND:
        return _build_reference_report(script)
    skeleton = SKELETONS[kind]
    id_field, chars_field = skeleton.id_field, skeleton.chars_field
    raw_items = script.get(kind)
    items = [item for item in (raw_items if isinstance(raw_items, list) else []) if isinstance(item, Mapping)]
    raw_plan = script.get("director_plan")
    plan: Mapping[str, Any] | None = raw_plan if isinstance(raw_plan, Mapping) else None
    if plan is None and not any(isinstance(item.get("direction"), Mapping) for item in items):
        return None

    rows = [_row(item, kind, id_field) for item in items]
    warnings: list[dict[str, Any]] = []
    undirected = [
        row["id"] for item, row in zip(items, rows, strict=True) if not isinstance(item.get("direction"), Mapping)
    ]
    if undirected:
        warnings.append(_warning(WARNING_MISSING_DIRECTION, undirected, "entries have no direction yet"))

    missing_intent = [row["id"] for row in rows if row["id"] not in undirected and not row["camera"]["intent"]]
    if missing_intent:
        warnings.append(_warning(WARNING_MISSING_INTENT, missing_intent, "camera intent is missing"))

    for item, row in zip(items, rows, strict=True):
        if row["id"] in undirected:
            continue
        raw_chars = item.get(chars_field) if chars_field else None
        characters = [name for name in (raw_chars if isinstance(raw_chars, list) else []) if isinstance(name, str)]
        covered = {entry["character"] for entry in row["performance"]}
        missing = [name for name in characters if name not in covered]
        if missing:
            warnings.append(
                _warning(
                    WARNING_MISSING_PERFORMANCE,
                    [row["id"]],
                    f"characters without performance: {', '.join(missing)}",
                )
            )

    warnings.extend(_repeated_angle_warnings(rows))

    for index, (item, row) in enumerate(zip(items, rows, strict=True)):
        if index == 0 or item.get("segment_break") is True:
            continue
        previous = rows[index - 1]
        if previous["id"] in undirected or row["id"] in undirected:
            continue
        if not continuity_connects(previous["continuity_out"], row["continuity_in"]):
            warnings.append(
                _warning(
                    WARNING_CONTINUITY_GAP,
                    [previous["id"], row["id"]],
                    f"{previous['id']} ends in a state that {row['id']} does not pick up",
                )
            )

    distribution = Counter(row["camera"]["angle"] for row in rows if row["camera"]["angle"])
    arc = [
        {"beat": _text(beat.get("beat")), "units": [u for u in beat.get("units", []) if isinstance(u, int)]}
        for beat in ((plan or {}).get("arc") or [])
        if isinstance(beat, Mapping)
    ]
    raw_motifs = (plan or {}).get("motifs")
    return {
        "route": "storyboard",
        "episode_intent": _text((plan or {}).get("episode_intent")),
        "tone": _text((plan or {}).get("tone")),
        "arc": arc,
        "motifs": [m for m in (raw_motifs if isinstance(raw_motifs, list) else []) if isinstance(m, str)],
        "rows": rows,
        "angle_distribution": dict(distribution),
        "warnings": warnings,
    }


def _plan_fields(plan: Mapping[str, Any] | None) -> dict[str, Any]:
    arc = [
        {"beat": _text(beat.get("beat")), "units": [u for u in beat.get("units", []) if isinstance(u, int)]}
        for beat in ((plan or {}).get("arc") or [])
        if isinstance(beat, Mapping)
    ]
    raw_motifs = (plan or {}).get("motifs")
    return {
        "episode_intent": _text((plan or {}).get("episode_intent")),
        "tone": _text((plan or {}).get("tone")),
        "arc": arc,
        "motifs": [m for m in (raw_motifs if isinstance(raw_motifs, list) else []) if isinstance(m, str)],
    }


def _dicts(value: object) -> list[Mapping[str, Any]]:
    return [item for item in (value if isinstance(value, list) else []) if isinstance(item, Mapping)]


def _reference_row(unit: Mapping[str, Any]) -> dict[str, Any]:
    text = _text(unit.get("text"))
    shots = []
    for shot in _dicts(unit.get("shots")):
        raw_camera = shot.get("camera")
        camera: Mapping[str, Any] = raw_camera if isinstance(raw_camera, Mapping) else {}
        at = shot.get("at_seconds")
        shots.append(
            {
                "at_seconds": float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else 0.0,
                "framing": _text(shot.get("framing")),
                "camera": {
                    "type": _text(camera.get("type")),
                    "amplitude": _text(camera.get("amplitude")) or "medium",
                    "speed": _text(camera.get("speed")) or "normal",
                },
                "action": _text(shot.get("action")),
                "why_cut": _text(shot.get("why_cut")),
            }
        )
    return {
        "id": str(unit.get("unit_id")),
        "description": text if len(text) <= DESCRIPTION_MAX_CHARS else text[:DESCRIPTION_MAX_CHARS].rstrip() + "…",
        "duration_seconds": unit.get("duration_seconds") if isinstance(unit.get("duration_seconds"), int) else 0,
        "direction": _text(unit.get("direction")),
        "unit_type": _text(unit.get("unit_type")),
        "brief": _text(unit.get("brief")),
        "shots": shots,
        "performance": [
            {
                "character": _text(item.get("character")),
                "note": _text(item.get("note")),
                "voice": _text(item.get("voice")),
            }
            for item in _dicts(unit.get("performance"))
        ],
        "lines": [
            {
                "speaker": _text(item.get("speaker")),
                "text": _text(item.get("text")),
                "shot": item.get("shot") if isinstance(item.get("shot"), int) else 0,
                "delivery": _text(item.get("delivery")),
            }
            for item in _dicts(unit.get("lines"))
        ],
        "sounds": _text(unit.get("sounds")),
        "music": _text(unit.get("music")),
        "must_not_show": _text(unit.get("must_not_show")),
        "end_frame": _text(unit.get("end_frame")),
        "continuity_in": _text(unit.get("continuity_in")),
        "continuity_out": _text(unit.get("continuity_out")),
    }


def _build_reference_report(script: Mapping[str, Any]) -> dict[str, Any] | None:
    """参考生视频的导演报告：逐单元的说明、镜头表、表演与声音、台词落位、衔接，以及运镜分布与机械提示。"""
    units = _dicts(script.get(REFERENCE_KIND))
    raw_plan = script.get("director_plan")
    plan: Mapping[str, Any] | None = raw_plan if isinstance(raw_plan, Mapping) else None
    rows = [_reference_row(unit) for unit in units]
    directed = [bool(row["direction"]) or (bool(row["brief"]) and bool(row["shots"])) for row in rows]
    if plan is None and not any(directed):
        return None

    warnings: list[dict[str, Any]] = []
    undirected = [row["id"] for row, done in zip(rows, directed, strict=True) if not done]
    if undirected:
        warnings.append(_warning(WARNING_MISSING_DIRECTION, undirected, "units have no direction yet"))
    for row, done in zip(rows, directed, strict=True):
        if not done:
            continue
        voices = {item["character"] for item in row["performance"] if item["voice"]}
        silent = sorted({line["speaker"] for line in row["lines"] if line["speaker"] not in voices})
        if silent:
            warnings.append(
                _warning(
                    WARNING_MISSING_VOICE, [row["id"]], f"speakers without a voice description: {', '.join(silent)}"
                )
            )
    for index in range(1, len(rows)):
        previous, row = rows[index - 1], rows[index]
        if not (directed[index - 1] and directed[index]):
            continue
        # 自由文本导演说明里的衔接是文字，没有可比对的结构化字段，不做机械衔接判定
        if previous["direction"] or row["direction"]:
            continue
        if not continuity_connects(previous["continuity_out"], row["continuity_in"]):
            warnings.append(
                _warning(
                    WARNING_CONTINUITY_GAP,
                    [previous["id"], row["id"]],
                    f"{previous['id']} ends in a state that {row['id']} does not pick up",
                )
            )
    distribution = Counter(shot["camera"]["type"] for row in rows for shot in row["shots"] if shot["camera"]["type"])
    return {
        "route": "reference",
        **_plan_fields(plan),
        "rows": rows,
        "camera_distribution": dict(distribution),
        "warnings": warnings,
    }


__all__ = [
    "DESCRIPTION_MAX_CHARS",
    "REPEATED_ANGLE_RUN",
    "WARNING_CONTINUITY_GAP",
    "WARNING_MISSING_DIRECTION",
    "WARNING_MISSING_INTENT",
    "WARNING_MISSING_PERFORMANCE",
    "WARNING_MISSING_VOICE",
    "WARNING_REPEATED_ANGLE",
    "build_director_report",
    "continuity_connects",
]
