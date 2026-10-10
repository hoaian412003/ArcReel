"""画布用例：读取合并后的画布、节点与连线的编辑、节点运行。

网页路由与 Agent 工具共用这一层，规则只有一份。图规则违例抛 :class:`CanvasGraphError`，
运行前置条件不满足（没有输入、未启用 H3 档……）也用它表达，``code`` 对应 ``errors`` 命名空间的 i18n 键。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from lib.canvas.graph import (
    BUSY_STATUSES,
    NODE_ASSET,
    NODE_AUDIO_GENERATE,
    NODE_GROUP,
    NODE_IMAGE,
    NODE_IMAGE_GENERATE,
    NODE_TEXT,
    NODE_VIDEO_GENERATE,
    STATUS_FAILED,
    STATUS_QUEUED,
    CanvasGraphError,
    add_node,
    add_run_outputs,
    branch_plan,
    connect,
    disconnect,
    duplicate_asset_node,
    find_node,
    group_nodes,
    instantiate_template,
    make_node,
    make_template,
    new_id,
    patch_node,
    remove_node,
    resolve_asset_node,
    resolve_generate_inputs,
    resolve_reference_labels,
    set_members,
    set_note,
    stale_generated_images,
    utc_now_iso,
)
from lib.canvas.store import CANVAS_UPLOAD_DIR, load_canvas, mutate_canvas
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import TaskSpec
from lib.infra.path_safety import safe_join
from lib.project.project_manager import ProjectManager
from server.services.project import canvas_episodes
from server.services.project.canvas_assets import import_project_assets, to_primitives
from server.services.project.canvas_publish import publish_asset_groups
from server.services.project.canvas_view import build_project_view
from server.services.tasks.canvas_tasks import CANVAS_AUDIO_TASK_TYPE, CANVAS_IMAGE_TASK_TYPE, CANVAS_VIDEO_TASK_TYPE

#: 画布上传图片允许的扩展名与大小上限（网页上传与 Agent 工具共用）。
UPLOAD_EXTS = (".png", ".jpg", ".jpeg", ".webp")
UPLOAD_MAX_BYTES = 20 * 1024 * 1024

#: 供应商节点类型 → (任务类型, 队列媒体类型)。
_PROVIDER_TASKS: dict[str, tuple[str, str]] = {
    NODE_IMAGE_GENERATE: (CANVAS_IMAGE_TASK_TYPE, "image"),
    NODE_VIDEO_GENERATE: (CANVAS_VIDEO_TASK_TYPE, "video"),
    NODE_AUDIO_GENERATE: (CANVAS_AUDIO_TASK_TYPE, "audio"),
}


def _task_options(node: dict[str, Any], frozen: dict[str, Any]) -> dict[str, Any]:
    """各类供应商节点随任务冻结的专有参数。"""
    data = node["data"]
    if node["type"] == NODE_IMAGE_GENERATE:
        return {
            "reference_labels": frozen["reference_labels"],
            "aspect_ratio": data.get("aspect_ratio") or "9:16",
            **_model_override(data.get("model")),
        }
    if node["type"] == NODE_VIDEO_GENERATE:
        return {
            "aspect_ratio": data.get("aspect_ratio") or "9:16",
            "duration_seconds": int(data.get("duration_seconds") or 5),
        }
    voice = data.get("voice")
    return {"voice": voice} if isinstance(voice, str) and voice.strip() else {}


def _model_override(model: object) -> dict[str, str]:
    """节点上选的 ``供应商/模型`` → 任务 payload 的 ``image_provider`` / ``image_model``；未选或格式不对即不覆盖。"""
    if isinstance(model, str) and "/" in model:
        provider, _, name = model.partition("/")
        if provider and name:
            return {"image_provider": provider, "image_model": name}
    return {}


def _append(items: list[Any], item: Any) -> Any:
    items.append(item)
    return item


logger = logging.getLogger(__name__)


class CanvasService:
    def __init__(self, pm: ProjectManager, queue: GenerationQueue) -> None:
        self.pm = pm
        self.queue = queue

    # ---- 读 ----

    def _derived_assets(
        self, project_name: str, graph: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """项目里现有资产的推导视图，只用来把它们搬上画布。"""
        project = self.pm.load_project(project_name)
        return build_project_view(self.pm, project_name, project, graph["overrides"], notes=graph["notes"])

    def ensure_migrated_sync(self, project_name: str) -> dict[str, Any]:
        """第一次读到的画布：把早先与项目绑定的节点转成普通节点，并把项目里已有的资产摆成分组。只做一次。"""
        project_path = self.pm.get_project_path(project_name)
        graph = load_canvas(project_path)
        if graph["meta"].get("primitives"):
            return graph
        nodes, edges = self._derived_assets(project_name, graph)

        def _apply(g: dict[str, Any]) -> None:
            to_primitives(g)
            import_project_assets(g, nodes, edges)
            g["meta"]["primitives"] = True

        _, graph = mutate_canvas(self.pm, project_path, _apply)
        return graph

    async def import_episode(self, project_name: str, episode: int) -> tuple[dict[str, Any], int]:
        """把一集的流程摆上画布（已经在画布上就返回现有分组）。"""
        try:
            group, graph = await self.mutate(
                project_name,
                lambda g: canvas_episodes.import_episode(self.pm, project_name, g, episode),
            )
        except canvas_episodes.EpisodeFlowError as exc:
            raise CanvasGraphError(exc.code, **exc.params) from exc
        return group, graph["revision"]

    async def flow(self, project_name: str) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            graph = self.ensure_migrated_sync(project_name)
            return canvas_episodes.flow_status(self.pm, project_name, graph)

        return await asyncio.to_thread(_run)

    async def save_unit_field(self, project_name: str, episode: int, unit_id: str, field: str, value: str) -> None:
        try:
            await asyncio.to_thread(
                canvas_episodes.save_unit_field, self.pm, project_name, episode, unit_id, field, value
            )
        except canvas_episodes.EpisodeFlowError as exc:
            raise CanvasGraphError(exc.code, **exc.params) from exc

    async def markdown(self, project_name: str, file: str) -> str:
        project_path = await asyncio.to_thread(self.pm.get_project_path, project_name)
        try:
            return await asyncio.to_thread(canvas_episodes.read_markdown, project_path, file)
        except canvas_episodes.EpisodeFlowError as exc:
            raise CanvasGraphError(exc.code, **exc.params) from exc

    async def import_assets(self, project_name: str) -> tuple[int, int]:
        """把项目里有、画布上还没有分组的资产摆上画布；返回（新增分组数，revision）。"""

        def _run() -> tuple[int, int]:
            project_path = self.pm.get_project_path(project_name)
            graph = load_canvas(project_path)
            nodes, edges = self._derived_assets(project_name, graph)
            created, graph = mutate_canvas(self.pm, project_path, lambda g: import_project_assets(g, nodes, edges))
            return len(created), graph["revision"]

        return await asyncio.to_thread(_run)

    async def create_asset_group(
        self, project_name: str, kind: str, name: str, description: str, position: dict[str, float]
    ) -> tuple[dict[str, Any], int]:
        """新建一个资产分组：组里是描述文本、图片供应商和资产节点；描述有了就会建出项目资产（见 canvas_publish）。"""

        def _apply(graph: dict[str, Any]) -> dict[str, Any]:
            frame = make_node(NODE_GROUP, position, {"label": name, "asset": kind, "width": 900.0, "height": 420.0})
            frame["data"]["autolayout"] = True
            graph["nodes"].insert(0, frame)
            text = make_node(NODE_TEXT, {"x": 28, "y": 70}, {"text": description})
            generator = make_node(NODE_IMAGE_GENERATE, {"x": 340, "y": 70})
            asset = make_node(NODE_ASSET, {"x": 700, "y": 70})
            for child in (text, generator, asset):
                child["parentId"] = frame["id"]
                graph["nodes"].append(child)
            graph["edges"].append(
                {
                    "id": new_id("edge"),
                    "source": text["id"],
                    "sourceHandle": "text",
                    "target": generator["id"],
                    "targetHandle": "prompt",
                }
            )
            return frame

        frame, graph = await self.mutate(project_name, _apply)
        return frame, graph["revision"]

    def _sync_new_units(self, project_name: str, graph: dict[str, Any]) -> dict[str, Any]:
        """流水线（含 Agent）又产出了新单元：已在画布上的那一集自动补上单元行。失败只记日志，不影响看画布。"""
        try:
            episodes = canvas_episodes.episodes_with_new_units(self.pm, project_name, graph)
            if not episodes:
                return graph

            def _apply(g: dict[str, Any]) -> None:
                for episode in episodes:
                    canvas_episodes.import_episode(self.pm, project_name, g, episode, only_new=True)

            _, graph = mutate_canvas(self.pm, self.pm.get_project_path(project_name), _apply)
        except Exception:
            logger.exception("画布自动补单元失败 project=%s", project_name)
        return graph

    def view_sync(self, project_name: str) -> dict[str, Any]:
        """画布；资产节点带上它接着的那张图，方便界面显示。"""
        graph = self.ensure_migrated_sync(project_name)
        graph = self._sync_new_units(project_name, graph)
        stale = stale_generated_images(graph, {})
        nodes = []
        for n in graph["nodes"]:
            if n["type"] == NODE_ASSET:
                n = resolve_asset_node(graph, n)
            nodes.append({**n, "data": {**n["data"], "stale": True}} if n["id"] in stale else n)
        nodes = canvas_episodes.refresh_outputs(self.pm, project_name, nodes)
        known = {n["id"] for n in nodes}
        # 所属的分组已不存在：节点留在画布上，不再挂在分组下。
        nodes = [
            {k: v for k, v in n.items() if k != "parentId"} if n.get("parentId") not in {None, *known} else n
            for n in nodes
        ]
        stored = [e for e in graph["edges"] if e["source"] in known and e["target"] in known]
        return {
            "revision": graph["revision"],
            "nodes": nodes,
            "edges": stored,
            "templates": [
                {"id": t["id"], "name": t["name"], "node_count": len(t["nodes"])} for t in graph["templates"]
            ],
        }

    async def view(self, project_name: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.view_sync, project_name)

    # ---- 写（细粒度，文件锁内读-改-写）----

    async def mutate[T](self, project_name: str, fn: Any) -> tuple[T, dict[str, Any]]:
        project_path = await asyncio.to_thread(self.pm.get_project_path, project_name)
        result = await asyncio.to_thread(mutate_canvas, self.pm, project_path, fn)
        # 画布上的资产分组变了就写到项目资产里（只在内容变了时才真写，失败只记日志）。
        await asyncio.to_thread(publish_asset_groups, self.pm, project_name)
        return result

    async def place_image(
        self,
        project_name: str,
        content: bytes,
        suffix: str,
        label: str | None,
        position: dict[str, float],
        node_id: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        """图片落盘到画布上传目录：默认成为新的图片节点；带 ``node_id`` 时填进（或换掉）这个已有的图片节点。"""
        project_path: Path = await asyncio.to_thread(self.pm.get_project_path, project_name)
        rel = f"{CANVAS_UPLOAD_DIR}/{uuid.uuid4().hex}{suffix}"
        target = safe_join(project_path, rel)

        def _save() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        await asyncio.to_thread(_save)

        def _place(graph: dict[str, Any]) -> dict[str, Any]:
            if node_id is None:
                return add_node(graph, make_node(NODE_IMAGE, position, {"label": label}, server_data={"path": rel}))
            node = find_node(graph, node_id)
            if node["type"] != NODE_IMAGE or node["data"].get("generated"):
                raise CanvasGraphError("canvas_invalid_connection")
            node["data"].update(path=rel, version=int(node["data"].get("version") or 0) + 1)
            return node

        node, graph = await self.mutate(project_name, _place)
        return node, graph["revision"]

    async def duplicate_asset(self, project_name: str, node_id: str) -> tuple[dict[str, Any], int]:
        node, graph = await self.mutate(project_name, lambda g: duplicate_asset_node(g, node_id))
        return node, graph["revision"]

    async def add_node(
        self, project_name: str, node_type: str, position: dict[str, float], data: dict[str, Any] | None
    ) -> tuple[dict[str, Any], int]:
        node, graph = await self.mutate(project_name, lambda g: add_node(g, make_node(node_type, position, data)))
        return node, graph["revision"]

    async def patch_node(
        self, project_name: str, node_id: str, position: dict[str, float] | None, data: dict[str, Any] | None
    ) -> tuple[dict[str, Any], int]:
        node, graph = await self.mutate(project_name, lambda g: patch_node(g, node_id, position=position, data=data))
        return node, graph["revision"]

    async def set_note(self, project_name: str, node_id: str, note: str) -> int:
        """图片节点（含项目资产图）的参考说明：随图一起交给供应商，描述这张图是什么。"""
        _, graph = await self.mutate(project_name, lambda g: set_note(g, node_id, note))
        return graph["revision"]

    async def group_nodes(
        self,
        project_name: str,
        node_ids: list[str],
        label: str,
        position: dict[str, float],
        size: dict[str, float],
    ) -> tuple[dict[str, Any], int]:
        group, graph = await self.mutate(project_name, lambda g: group_nodes(g, node_ids, label, position, size))
        return group, graph["revision"]

    async def set_members(
        self,
        project_name: str,
        node_ids: list[str],
        group_id: str | None,
        positions: dict[str, dict[str, float]],
    ) -> int:
        _, graph = await self.mutate(project_name, lambda g: set_members(g, node_ids, group_id, positions))
        return graph["revision"]

    async def delete_node(self, project_name: str, node_id: str) -> int:
        _, graph = await self.mutate(project_name, lambda g: remove_node(g, node_id))
        return graph["revision"]

    async def connect(
        self, project_name: str, source: str, source_handle: str, target: str, target_handle: str
    ) -> tuple[dict[str, Any], int]:
        edge, graph = await self.mutate(
            project_name, lambda g: connect(g, source, source_handle, target, target_handle)
        )
        return edge, graph["revision"]

    async def disconnect(self, project_name: str, edge_id: str) -> int:
        _, graph = await self.mutate(project_name, lambda g: disconnect(g, edge_id))
        return graph["revision"]

    async def set_layout(self, project_name: str, positions: dict[str, dict[str, float]]) -> int:
        def _apply(graph: dict[str, Any]) -> None:
            for node in graph["nodes"]:
                if (pos := positions.get(node["id"])) is not None:
                    node["position"] = dict(pos)

        _, graph = await self.mutate(project_name, _apply)
        return graph["revision"]

    # ---- 模板 ----

    async def save_template(self, project_name: str, name: str, node_ids: list[str]) -> tuple[dict[str, Any], int]:
        template, graph = await self.mutate(
            project_name, lambda g: _append(g["templates"], make_template(g, name, node_ids))
        )
        return {"id": template["id"], "name": template["name"], "node_count": len(template["nodes"])}, graph["revision"]

    async def use_template(
        self, project_name: str, template_id: str, origin: dict[str, float]
    ) -> tuple[list[dict[str, Any]], int]:
        def _apply(graph: dict[str, Any]) -> list[dict[str, Any]]:
            template = next((t for t in graph["templates"] if t["id"] == template_id), None)
            if template is None:
                raise CanvasGraphError("canvas_template_not_found", id=template_id)
            return instantiate_template(graph, template, origin)

        nodes, graph = await self.mutate(project_name, _apply)
        return nodes, graph["revision"]

    async def delete_template(self, project_name: str, template_id: str) -> int:
        def _apply(graph: dict[str, Any]) -> None:
            before = len(graph["templates"])
            graph["templates"] = [t for t in graph["templates"] if t["id"] != template_id]
            if len(graph["templates"]) == before:
                raise CanvasGraphError("canvas_template_not_found", id=template_id)

        _, graph = await self.mutate(project_name, _apply)
        return graph["revision"]

    # ---- 运行 ----

    async def branch(self, project_name: str, node_id: str) -> list[dict[str, str]]:
        """运行 ``node_id`` 前须先跑的上游节点（只含缺产物或已过期的），最后一项是它自己。"""

        def _plan() -> list[dict[str, str]]:
            graph = self.ensure_migrated_sync(project_name)
            stale_ids = stale_generated_images(graph, {})
            graph["nodes"] = [
                {**n, "data": {**n["data"], "stale": True}} if n["id"] in stale_ids else n for n in graph["nodes"]
            ]
            return branch_plan(graph, node_id, {})

        return await asyncio.to_thread(_plan)

    async def run(self, project_name: str, node_id: str, *, user_id: str, source: str) -> dict[str, Any]:
        """运行一个节点，立即返回入队结果。图片 / 视频 / 语音供应商节点与各类资产图节点可运行。"""
        project_path = await asyncio.to_thread(self.pm.get_project_path, project_name)
        graph = await asyncio.to_thread(load_canvas, project_path)
        node = next((n for n in graph["nodes"] if n["id"] == node_id), None)
        if node is None:
            raise CanvasGraphError("canvas_node_not_found", id=node_id)
        if node["type"] in _PROVIDER_TASKS:
            return await self._run_provider(project_name, node_id, user_id=user_id, source=source)
        raise CanvasGraphError("canvas_node_not_runnable", id=node_id)

    async def _run_provider(self, project_name: str, node_id: str, *, user_id: str, source: str) -> dict[str, Any]:
        frozen: dict[str, Any] = {}

        def _prepare(graph: dict[str, Any]) -> dict[str, Any]:
            node = find_node(graph, node_id)
            if node["data"].get("status") in BUSY_STATUSES:
                raise CanvasGraphError("canvas_node_busy", id=node_id)
            derived: dict[str, dict[str, Any]] = {}
            prompt, references = resolve_generate_inputs(graph, node_id, derived, strict=True)
            if not prompt:
                raise CanvasGraphError("canvas_prompt_required")
            labels = resolve_reference_labels(graph, node_id, derived, strict=True)
            frozen.update(
                prompt=prompt,
                references=references,
                options=_task_options(node, {"reference_labels": labels}),
                task_type=_PROVIDER_TASKS[node["type"]],
            )
            media_node = add_run_outputs(graph, node_id, prompt)
            node["data"].update(status=STATUS_QUEUED, error=None, submitted_at=utc_now_iso(), status_at=utc_now_iso())
            return media_node

        media_node, _ = await self.mutate(project_name, _prepare)
        task_type, media_type = frozen["task_type"]
        spec = TaskSpec.from_request(
            task_type=task_type,
            media_type=media_type,
            resource_id=media_node["id"],
            prompt=frozen["prompt"],
            extra_payload={
                "generator_id": node_id,
                "references": frozen["references"],
                **frozen["options"],
            },
        )
        try:
            result = await self.queue.enqueue_task(
                project_name=project_name,
                task_type=spec.task_type,
                media_type=spec.media_type,
                resource_id=spec.resource_id,
                payload=spec.payload,
                source=source,
                user_id=user_id,
            )
        except BaseException:

            def _rollback(graph: dict[str, Any]) -> None:
                created = media_node["id"]
                graph["nodes"] = [n for n in graph["nodes"] if n["id"] != created]
                graph["edges"] = [e for e in graph["edges"] if created not in (e["source"], e["target"])]
                find_node(graph, node_id)["data"].update(status=STATUS_FAILED)

            await asyncio.shield(self.mutate(project_name, _rollback))
            raise

        def _record_task(graph: dict[str, Any]) -> None:
            find_node(graph, node_id)["data"]["task_id"] = result["task_id"]
            find_node(graph, media_node["id"])["data"]["task_id"] = result["task_id"]

        await self.mutate(project_name, _record_task)
        return {
            "success": True,
            "task_id": result["task_id"],
            "task_ids": [result["task_id"]],
            "image_node_id": media_node["id"],
            "media_node_id": media_node["id"],
        }
