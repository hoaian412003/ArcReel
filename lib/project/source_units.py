"""源文分析单元：资产分析与故事理解共用的「按内容指纹增量处理」的读取口径。

有分集源文 ``source/episode_N.txt`` 就逐集取（手动上传的分集），否则取 scope=all 读到的源文。
单元 ``key`` 是集号字符串或源文相对路径，``fingerprint`` 是归一化文本的 sha256。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.episode.episode_ledger import discover_episode_files, normalize_source_text


@dataclass(frozen=True)
class SourceUnit:
    key: str
    title: str
    text: str
    fingerprint: str


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def episode_unit_title(num: int) -> str:
    return f"第 {num} 集"


def collect_source_units(project_dir: Path, documents: list[Any]) -> list[SourceUnit]:
    """分析单元：有分集源文就逐集取（集号升序），否则取 scope=all 读到的源文。空白文本不成单元。"""
    units: list[SourceUnit] = []
    episode_files = discover_episode_files(project_dir)
    if episode_files:
        for num in sorted(episode_files):
            text = normalize_source_text(episode_files[num].read_text(encoding="utf-8"))
            if text.strip():
                units.append(SourceUnit(str(num), episode_unit_title(num), text, text_fingerprint(text)))
        return units
    for doc in documents:
        text = normalize_source_text(doc.text)
        if text.strip():
            units.append(SourceUnit(doc.rel_path, doc.rel_path, text, text_fingerprint(text)))
    return units


__all__ = ["SourceUnit", "collect_source_units", "episode_unit_title", "text_fingerprint"]
