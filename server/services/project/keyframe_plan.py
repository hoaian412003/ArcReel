"""起始帧规划的用例编排：给 Agent 的简报、校验并落盘 Agent 提交的格子清单、看板状态。

规划由 Agent 的子智能体完成（读简报、做创作判断、提交清单），服务端不调文本模型：这里只做确定性的
读取、校验与落盘。校验本体在 :mod:`lib.keyframes.planning`；见 ``docs/adr/0096``。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lib.keyframes.models import CELLS_PER_SHEET, KeyframeBoard
from lib.keyframes.planning import KeyframePlanViolation, PlannedCell, build_board, plan_fingerprint
from lib.keyframes.store import load_board, save_board
from lib.project.project_manager import ProjectManager, find_episode
from lib.prompts.video_prompt_profile import is_h3_ref2va
from server.services.project.direction_review import read_formal_script


class KeyframePlanError(Exception):
    """用例级失败；``code`` 是机读原因，``problems`` 是逐条细节（校验违约时给出）。"""

    def __init__(self, code: str, message: str = "", *, problems: Sequence[str] = ()) -> None:
        self.code = code
        self.message = message or code
        self.problems = list(problems)
        super().__init__(self.message)


def _character_names(project: Mapping[str, Any]) -> list[str]:
    """已登记的角色引用名：本体，以及每个衍生的 ``本体/衍生`` 形态。"""
    names: list[str] = []
    characters = project.get("characters")
    if not isinstance(characters, Mapping):
        return names
    for base, entry in characters.items():
        names.append(str(base))
        derivatives = entry.get("derivatives") if isinstance(entry, Mapping) else None
        if isinstance(derivatives, Mapping):
            names.extend(f"{base}/{derivative}" for derivative in derivatives)
    return names


def _asset_rows(project: Mapping[str, Any], bucket: str, sheet_field: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    entries = project.get(bucket)
    if not isinstance(entries, Mapping):
        return rows
    for name, entry in entries.items():
        entry_map = entry if isinstance(entry, Mapping) else {}
        rows.append(
            {
                "name": str(name),
                "description": str(entry_map.get("description") or ""),
                "has_sheet": bool(entry_map.get(sheet_field)),
            }
        )
    return rows


def formal_units(script: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "unit_id": str(unit["unit_id"]),
            "text": str(unit.get("text") or ""),
            "direction": str(unit.get("direction") or ""),
            "duration_seconds": unit.get("duration_seconds"),
        }
        for unit in script.get("video_units") or []
        if isinstance(unit, Mapping) and unit.get("unit_id") and str(unit.get("text") or "").strip()
    ]


def _carry_over(board: KeyframeBoard, previous: KeyframeBoard | None) -> KeyframeBoard:
    """内容未变的格子沿用上一版的图、状态与依据，其余格子从头开始。"""
    if previous is None:
        return board

    def signature(cell: Any) -> tuple[Any, ...]:
        return (cell.scene, cell.staging, cell.framing, tuple(cell.characters), tuple(cell.unit_ids))

    old = {signature(cell): cell for cell in previous.cells()}
    for cell in board.cells():
        kept = old.get(signature(cell))
        if kept is not None and kept.image:
            cell.status, cell.image, cell.basis = kept.status, kept.image, kept.basis
    return board


class KeyframePlanService:
    def __init__(self, pm: ProjectManager) -> None:
        self.pm = pm

    async def get_brief(self, project_name: str, episode: int) -> dict[str, Any]:
        return await asyncio.to_thread(self._brief_sync, project_name, episode)

    async def submit(
        self,
        project_name: str,
        episode: int,
        cells: Sequence[PlannedCell],
        *,
        base_fingerprint: str | None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._submit_sync, project_name, episode, list(cells), base_fingerprint)

    def _context(
        self, project_name: str, episode: int
    ) -> tuple[dict[str, Any], Path, dict[str, Any], list[dict[str, Any]]]:
        project = self.pm.load_project(project_name)
        if find_episode(project, episode) is None:
            raise KeyframePlanError("episode_not_found", f"第 {episode} 集不存在")
        if project.get("generation_mode") != "reference_video" or not is_h3_ref2va(project):
            raise KeyframePlanError(
                "not_applicable", "起始帧只用于采用 H3 全参考提示词档的参考生视频项目，本项目不适用"
            )
        script = read_formal_script(self.pm, project_name, project, episode)
        if script is None:
            raise KeyframePlanError("script_missing", f"第 {episode} 集还没有正式脚本，无法规划起始帧")
        units = formal_units(script)
        if not units:
            raise KeyframePlanError("no_units", f"第 {episode} 集没有可规划的视频单元")
        return project, self.pm.get_project_path(project_name), script, units

    def _brief_sync(self, project_name: str, episode: int) -> dict[str, Any]:
        project, project_path, _script, units = self._context(project_name, episode)
        board = load_board(project_path, episode)
        return {
            "episode": episode,
            "plan_fingerprint": plan_fingerprint(units),
            "units": units,
            "characters": [
                {
                    "name": name,
                    "description": str(
                        ((project.get("characters") or {}).get(name.split("/", 1)[0]) or {}).get("description") or ""
                    ),
                }
                for name in _character_names(project)
            ],
            "scenes": _asset_rows(project, "scenes", "scene_sheet"),
            "cells_per_sheet": CELLS_PER_SHEET,
            "existing_board": self._state(board, units) if board is not None else None,
        }

    def _submit_sync(
        self, project_name: str, episode: int, cells: list[PlannedCell], base_fingerprint: str | None
    ) -> dict[str, Any]:
        project, project_path, _script, units = self._context(project_name, episode)
        current = plan_fingerprint(units)
        if base_fingerprint is not None and base_fingerprint != current:
            raise KeyframePlanError("plan_stale", "单元内容或导演说明在你读取简报之后被改动，请重新读取简报再提交")
        try:
            result = build_board(
                episode,
                cells,
                units=units,
                characters=_character_names(project),
                scenes=[str(name) for name in (project.get("scenes") or {})],
            )
        except KeyframePlanViolation as exc:
            raise KeyframePlanError("invalid_plan", "起始帧规划未通过校验", problems=exc.problems) from exc
        board = _carry_over(result.board, load_board(project_path, episode))
        save_board(project_path, board)
        state = self._state(board, units)
        state["uncovered_unit_ids"] = result.uncovered_unit_ids
        return state

    def _state(self, board: KeyframeBoard, units: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "episode": board.episode,
            "plan_fingerprint": board.plan_fingerprint,
            "stale": board.plan_fingerprint != plan_fingerprint(units),
            "sheets": [
                {
                    "id": sheet.id,
                    "scene": sheet.scene,
                    "cells": [
                        {
                            "id": cell.id,
                            "label": cell.label,
                            "status": cell.status,
                            "unit_ids": cell.unit_ids,
                            "characters": cell.characters,
                        }
                        for cell in sheet.cells
                    ],
                }
                for sheet in board.sheets
            ],
        }


__all__ = ["KeyframePlanError", "KeyframePlanService", "formal_units"]
