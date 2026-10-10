"""起始帧看板的读写用例：给界面的状态、人工审核格子、把单元改挂到另一格。

规划（哪些单元共用一格）由子智能体完成并经 ``submit_keyframe_plan`` 提交；这里只处理提交之后人工能做的事。
读状态时顺带判定每格是否已过期（联合图依据变了）与每张联合图当前能否生成（素材是否齐全）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lib.canvas.keyframe_refs import canvas_sheet_references
from lib.keyframes.landing import sheet_basis
from lib.keyframes.models import KeyframeBoard, KeyframeCell, KeyframeSheet
from lib.keyframes.planning import plan_fingerprint
from lib.keyframes.sheet import KeyframeSheetError, build_sheet_prompt
from lib.keyframes.store import load_board, save_board
from lib.project.project_manager import ProjectManager, find_episode
from lib.prompts.video_prompt_profile import is_h3_ref2va
from lib.script.reference_video.text_parser import extract_mentions
from server.services.project.direction_review import read_formal_script
from server.services.project.keyframe_plan import KeyframePlanError, formal_units

_TEXT_PREVIEW_CHARS = 90


def _applicable(project: Mapping[str, Any]) -> bool:
    return project.get("generation_mode") == "reference_video" and is_h3_ref2va(project)


class KeyframeBoardService:
    def __init__(self, pm: ProjectManager) -> None:
        self.pm = pm

    async def get_state(self, project_name: str, episode: int) -> dict[str, Any]:
        return await asyncio.to_thread(self._state_sync, project_name, episode)

    async def get_sheet_request(self, project_name: str, episode: int, sheet_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._sheet_request_sync, project_name, episode, sheet_id)

    async def review_cell(self, project_name: str, episode: int, cell_id: str, *, approved: bool) -> dict[str, Any]:
        return await asyncio.to_thread(self._review_sync, project_name, episode, cell_id, approved)

    async def approve_all(self, project_name: str, episode: int) -> dict[str, Any]:
        return await asyncio.to_thread(self._approve_all_sync, project_name, episode)

    async def assign_unit(self, project_name: str, episode: int, unit_id: str, cell_id: str | None) -> dict[str, Any]:
        return await asyncio.to_thread(self._assign_sync, project_name, episode, unit_id, cell_id)

    def state(self, project_name: str, episode: int) -> dict[str, Any]:
        """``get_state`` 的同步形态，供已在工作线程里的调用方（画布的项目视图）使用。"""
        return self._state_sync(project_name, episode)

    def _load(self, project_name: str, episode: int) -> tuple[dict[str, Any], Path]:
        project = self.pm.load_project(project_name)
        if find_episode(project, episode) is None:
            raise KeyframePlanError("episode_not_found", f"第 {episode} 集不存在")
        if not _applicable(project):
            raise KeyframePlanError("not_applicable", "起始帧只用于采用 H3 全参考提示词档的参考生视频项目")
        return project, self.pm.get_project_path(project_name)

    def _sheet_request_sync(self, project_name: str, episode: int, sheet_id: str) -> dict[str, Any]:
        """一张联合图下次生成时会发出的提示词与参考图（与执行层同一装配函数）。素材缺失时给出原因而不报错。"""
        project, project_path = self._load(project_name, episode)
        board = load_board(project_path, episode)
        if board is None:
            raise KeyframePlanError("board_missing", f"第 {episode} 集还没有起始帧看板")
        sheet = next((item for item in board.sheets if item.id == sheet_id), None)
        if sheet is None:
            raise KeyframePlanError("sheet_missing", sheet_id)
        try:
            built = build_sheet_prompt(
                sheet,
                project=project,
                project_path=project_path,
                extra_references=canvas_sheet_references(project_path, episode, sheet_id),
            )
        except KeyframeSheetError as exc:
            return {"sheet_id": sheet_id, "problem": {"code": exc.code, "name": exc.name}}
        root = project_path.resolve()
        references: list[dict[str, Any]] = []
        for index, path in enumerate(built.reference_images):
            resolved = path.resolve()
            inside = resolved.is_relative_to(root)
            references.append(
                {
                    "label": built.reference_labels[index] if index < len(built.reference_labels) else "",
                    "path": resolved.relative_to(root).as_posix() if inside else None,
                }
            )
        return {
            "sheet_id": sheet_id,
            "problem": None,
            "prompt": built.prompt,
            "aspect_ratio": built.aspect_ratio,
            "references": references,
        }

    def _units_of(self, project_name: str, project: dict[str, Any], episode: int) -> list[dict[str, Any]]:
        script = read_formal_script(self.pm, project_name, project, episode)
        return formal_units(script) if script is not None else []

    def _state_sync(self, project_name: str, episode: int) -> dict[str, Any]:
        project = self.pm.load_project(project_name)
        if find_episode(project, episode) is None:
            raise KeyframePlanError("episode_not_found", f"第 {episode} 集不存在")
        if not _applicable(project):
            return {"applicable": False, "episode": episode}
        project_path = self.pm.get_project_path(project_name)
        units = self._units_of(project_name, project, episode)
        board = load_board(project_path, episode)
        return self._render(project, project_path, episode, units, board)

    def _render(
        self,
        project: Mapping[str, Any],
        project_path: Path,
        episode: int,
        units: Sequence[Mapping[str, Any]],
        board: KeyframeBoard | None,
    ) -> dict[str, Any]:
        cell_of: dict[str, str] = {}
        sheets: list[dict[str, Any]] = []
        if board is not None:
            for sheet in board.sheets:
                sheets.append(self._render_sheet(project, project_path, sheet))
                for cell in sheet.cells:
                    for unit_id in cell.unit_ids:
                        cell_of[unit_id] = cell.id
        return {
            "applicable": True,
            "episode": episode,
            "has_board": board is not None,
            "plan_stale": board is not None and board.plan_fingerprint != plan_fingerprint(units),
            "units": [
                {
                    "unit_id": unit["unit_id"],
                    "text": str(unit["text"])[:_TEXT_PREVIEW_CHARS],
                    "cell_id": cell_of.get(str(unit["unit_id"])),
                }
                for unit in units
            ],
            "sheets": sheets,
        }

    def _render_sheet(self, project: Mapping[str, Any], project_path: Path, sheet: KeyframeSheet) -> dict[str, Any]:
        problem: dict[str, str] | None = None
        basis: str | None = None
        try:
            basis = sheet_basis(build_sheet_prompt(sheet, project=project, project_path=project_path))
        except KeyframeSheetError as exc:
            problem = {"code": exc.code, "name": exc.name}
        return {
            "id": sheet.id,
            "scene": sheet.scene,
            "image": sheet.image,
            "problem": problem,
            "cells": [
                {
                    "id": cell.id,
                    "label": cell.label,
                    "scene": cell.scene,
                    "staging": cell.staging,
                    "framing": cell.framing,
                    "characters": cell.characters,
                    "unit_ids": cell.unit_ids,
                    "status": cell.status,
                    "image": cell.image,
                    "stale": bool(cell.image and basis is not None and cell.basis != basis),
                }
                for cell in sheet.cells
            ],
        }

    def _review_sync(self, project_name: str, episode: int, cell_id: str, approved: bool) -> dict[str, Any]:
        project, project_path = self._load(project_name, episode)
        board = load_board(project_path, episode)
        if board is None:
            raise KeyframePlanError("board_missing", f"第 {episode} 集还没有起始帧看板")
        cell = next((item for item in board.cells() if item.id == cell_id), None)
        if cell is None:
            raise KeyframePlanError("cell_missing", f"看板里没有格子 {cell_id}")
        if approved:
            if not cell.image:
                raise KeyframePlanError("cell_has_no_image", f"格子 {cell_id} 还没有图，先生成联合图")
            sheet = board.sheet_of(cell_id)
            assert sheet is not None
            rendered = self._render_sheet(project, project_path, sheet)
            row = next(item for item in rendered["cells"] if item["id"] == cell_id)
            if row["stale"]:
                raise KeyframePlanError("cell_stale", f"格子 {cell_id} 的依据已变化，重新生成联合图后再审核")
        cell.status = "approved" if approved else "draft"
        save_board(project_path, board)
        units = self._units_of(project_name, project, episode)
        return self._render(project, project_path, episode, units, board)

    def _approve_all_sync(self, project_name: str, episode: int) -> dict[str, Any]:
        """审核通过所有已有图且依据未变的格子；没有图或已过期的格子保持原状，须先重新生成。"""
        project, project_path = self._load(project_name, episode)
        board = load_board(project_path, episode)
        if board is None:
            raise KeyframePlanError("board_missing", f"第 {episode} 集还没有起始帧看板")
        for sheet in board.sheets:
            rendered = {row["id"]: row for row in self._render_sheet(project, project_path, sheet)["cells"]}
            for cell in sheet.cells:
                if cell.image and not rendered[cell.id]["stale"]:
                    cell.status = "approved"
        save_board(project_path, board)
        units = self._units_of(project_name, project, episode)
        return self._render(project, project_path, episode, units, board)

    def _assign_sync(self, project_name: str, episode: int, unit_id: str, cell_id: str | None) -> dict[str, Any]:
        project, project_path = self._load(project_name, episode)
        board = load_board(project_path, episode)
        if board is None:
            raise KeyframePlanError("board_missing", f"第 {episode} 集还没有起始帧看板")
        units = self._units_of(project_name, project, episode)
        unit = next((item for item in units if item["unit_id"] == unit_id), None)
        if unit is None:
            raise KeyframePlanError("unit_missing", f"本集没有单元 {unit_id}")
        target: KeyframeCell | None = None
        if cell_id is not None:
            target = next((item for item in board.cells() if item.id == cell_id), None)
            if target is None:
                raise KeyframePlanError("cell_missing", f"看板里没有格子 {cell_id}")
            mentioned = set(extract_mentions(str(unit["text"])))
            missing = [name for name in target.characters if name not in mentioned]
            if missing:
                raise KeyframePlanError(
                    "characters_not_in_unit",
                    f"格子 {cell_id} 的出镜角色{missing}没有在单元 {unit_id} 的正文里被 @ 引用",
                    problems=missing,
                )
        self._detach(board, unit_id)
        if target is not None:
            target.unit_ids.append(unit_id)
        save_board(project_path, board)
        return self._render(project, project_path, episode, units, board)

    @staticmethod
    def _detach(board: KeyframeBoard, unit_id: str) -> None:
        """把单元从它现在所在的格子里摘出；格子因此没有任何单元时，格子本身（及空联合图）一并去掉。"""
        for sheet in list(board.sheets):
            for cell in list(sheet.cells):
                if unit_id in cell.unit_ids:
                    cell.unit_ids.remove(unit_id)
                    if not cell.unit_ids:
                        sheet.cells.remove(cell)
            if not sheet.cells:
                board.sheets.remove(sheet)


__all__ = ["KeyframeBoardService"]
