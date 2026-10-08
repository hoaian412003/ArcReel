"""画布用例：读取合并后的画布、节点与连线的编辑、节点运行。

网页路由与 Agent 工具共用这一层，规则只有一份。图规则违例抛 :class:`CanvasGraphError`，
运行前置条件不满足（没有输入、未启用 H3 档……）也用它表达，``code`` 对应 ``errors`` 命名空间的 i18n 键。
"""

from __future__ import annotations

import asyncio
from typing import Any

from lib.canvas.graph import (
    BUSY_STATUSES,
    KIND_KEYFRAME,
    NODE_AUDIO_GENERATE,
    NODE_H3_WRITER,
    NODE_IMAGE_GENERATE,
    NODE_VIDEO_GENERATE,
    STATUS_FAILED,
    STATUS_QUEUED,
    CanvasGraphError,
    add_node,
    add_run_outputs,
    branch_plan,
    connect,
    derived_kind,
    disconnect,
    find_node,
    instantiate_template,
    make_node,
    make_template,
    patch_node,
    remove_node,
    resolve_generate_inputs,
    resolve_reference_labels,
    stale_generated_images,
)
from lib.canvas.store import load_canvas, mutate_canvas
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import TaskSpec, WorkerOfflineError, enqueue_task_only
from lib.project.project_manager import ProjectManager
from lib.prompts.video_prompt_profile import is_h3_ref2va
from server.services.project.canvas_view import build_project_view, list_episodes, writer_output_edges
from server.services.project.keyframe_plan import KeyframePlanError
from server.services.project.keyframe_sheets import enqueue_keyframe_sheet
from server.services.tasks.canvas_tasks import CANVAS_AUDIO_TASK_TYPE, CANVAS_IMAGE_TASK_TYPE, CANVAS_VIDEO_TASK_TYPE
from server.text_generation import TextGenerationError, TextGenerationRequest, prompt_authoring_preflight
from server.tool_runtime import TEXT_EPISODE_SCRIPT_TASK_TYPE

UNIT_NODE_PREFIX = "p:unit:"


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


class CanvasService:
    def __init__(self, pm: ProjectManager, queue: GenerationQueue) -> None:
        self.pm = pm
        self.queue = queue

    # ---- 读 ----

    def view_sync(self, project_name: str) -> dict[str, Any]:
        """落盘图 + 选中集的派生节点；返回给界面 / Agent 的完整画布。"""
        graph = load_canvas(self.pm.get_project_path(project_name))
        project = self.pm.load_project(project_name)
        derived_nodes, virtual_edges = build_project_view(
            self.pm, project_name, project, graph["view"]["episodes"], graph["overrides"]
        )
        derived = {n["id"]: n for n in derived_nodes}
        stale = stale_generated_images(graph, derived)
        nodes = [{**n, "data": {**n["data"], "stale": True}} if n["id"] in stale else n for n in graph["nodes"]]
        known = {n["id"] for n in nodes} | set(derived)
        stored = [e for e in graph["edges"] if e["source"] in known and e["target"] in known]
        return {
            "revision": graph["revision"],
            "nodes": [*derived_nodes, *nodes],
            "edges": [*virtual_edges, *writer_output_edges(stored, set(derived)), *stored],
            "view": graph["view"],
            "episodes": list_episodes(project),
            "templates": [
                {"id": t["id"], "name": t["name"], "node_count": len(t["nodes"])} for t in graph["templates"]
            ],
        }

    async def view(self, project_name: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.view_sync, project_name)

    # ---- 写（细粒度，文件锁内读-改-写）----

    async def mutate[T](self, project_name: str, fn: Any) -> tuple[T, dict[str, Any]]:
        project_path = await asyncio.to_thread(self.pm.get_project_path, project_name)
        return await asyncio.to_thread(mutate_canvas, self.pm, project_path, fn)

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
            # 派生节点（集分组）不落盘，只登记摆放位置。
            for node_id, pos in positions.items():
                if derived_kind(node_id) == "episode":
                    graph["overrides"][node_id] = dict(pos)

        _, graph = await self.mutate(project_name, _apply)
        return graph["revision"]

    async def set_view(self, project_name: str, episodes: list[int]) -> tuple[list[int], int]:
        chosen = sorted(set(episodes))
        _, graph = await self.mutate(project_name, lambda g: g["view"].update(episodes=chosen))
        return chosen, graph["revision"]

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
            project_path = self.pm.get_project_path(project_name)
            graph = load_canvas(project_path)
            derived_nodes, _ = build_project_view(
                self.pm, project_name, self.pm.load_project(project_name), graph["view"]["episodes"], graph["overrides"]
            )
            derived = {n["id"]: n for n in derived_nodes}
            stale_ids = stale_generated_images(graph, derived)
            graph["nodes"] = [
                {**n, "data": {**n["data"], "stale": True}} if n["id"] in stale_ids else n for n in graph["nodes"]
            ]
            return branch_plan(graph, node_id, derived)

        return await asyncio.to_thread(_plan)

    async def run(self, project_name: str, node_id: str, *, user_id: str, source: str) -> dict[str, Any]:
        """运行一个节点，立即返回入队结果。图片供应商 / H3 写作 / 起始帧联合图三类节点可运行。"""
        kind = derived_kind(node_id)
        if kind == KIND_KEYFRAME:
            return await self._run_keyframe(project_name, node_id, user_id=user_id, source=source)
        project_path = await asyncio.to_thread(self.pm.get_project_path, project_name)
        graph = await asyncio.to_thread(load_canvas, project_path)  # 落盘图不含派生节点，可运行的派生节点上面已分流
        node = next((n for n in graph["nodes"] if n["id"] == node_id), None)
        if node is None:
            raise CanvasGraphError("canvas_node_not_found", id=node_id)
        if node["type"] in _PROVIDER_TASKS:
            return await self._run_provider(project_name, node_id, user_id=user_id, source=source)
        if node["type"] == NODE_H3_WRITER:
            return await self._run_h3_writer(project_name, node_id, user_id=user_id, source=source)
        raise CanvasGraphError("canvas_node_not_runnable", id=node_id)

    async def _run_provider(self, project_name: str, node_id: str, *, user_id: str, source: str) -> dict[str, Any]:
        frozen: dict[str, Any] = {}

        def _prepare(graph: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            node = find_node(graph, node_id)
            if node["data"].get("status") in BUSY_STATUSES:
                raise CanvasGraphError("canvas_node_busy", id=node_id)
            derived_nodes, _ = build_project_view(
                self.pm, project_name, self.pm.load_project(project_name), graph["view"]["episodes"], graph["overrides"]
            )
            derived = {n["id"]: n for n in derived_nodes}
            prompt, references = resolve_generate_inputs(graph, node_id, derived)
            if not prompt:
                raise CanvasGraphError("canvas_prompt_required")
            labels = resolve_reference_labels(graph, node_id, derived)
            frozen.update(
                prompt=prompt,
                references=references,
                options=_task_options(node, {"reference_labels": labels}),
                task_type=_PROVIDER_TASKS[node["type"]],
            )
            text_node, media_node = add_run_outputs(graph, node_id, prompt)
            node["data"].update(status=STATUS_QUEUED, error=None)
            return text_node, media_node

        (text_node, media_node), _ = await self.mutate(project_name, _prepare)
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
                for created in (media_node["id"], text_node["id"]):
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
            "text_node_id": text_node["id"],
            "image_node_id": media_node["id"],
            "media_node_id": media_node["id"],
        }

    async def _run_h3_writer(self, project_name: str, node_id: str, *, user_id: str, source: str) -> dict[str, Any]:
        """为写作节点所连的视频单元重写 H3 提示词：每集一个文本任务，单元正文原样保留。"""
        view = await self.view(project_name)
        by_episode: dict[int, list[str]] = {}
        for edge in view["edges"]:
            if edge["target"] != node_id or edge["targetHandle"] != "units" or edge.get("virtual"):
                continue
            if not edge["source"].startswith(UNIT_NODE_PREFIX):
                continue
            episode, _, unit_id = edge["source"][len(UNIT_NODE_PREFIX) :].partition(":")
            by_episode.setdefault(int(episode), []).append(unit_id)
        if not by_episode:
            raise CanvasGraphError("canvas_h3_writer_no_units")
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        if not is_h3_ref2va(project):
            raise CanvasGraphError("canvas_h3_profile_required")
        project_path = await asyncio.to_thread(self.pm.get_project_path, project_name)
        task_ids: list[str] = []
        for episode, unit_ids in sorted(by_episode.items()):
            try:
                await asyncio.to_thread(prompt_authoring_preflight, project_path, episode)
                request = TextGenerationRequest(
                    episode=episode, entry_ids=tuple(dict.fromkeys(unit_ids)), keep_text=True
                )
                result = await enqueue_task_only(
                    project_name=project_name,
                    task_type=TEXT_EPISODE_SCRIPT_TASK_TYPE,
                    media_type="text",
                    resource_id=f"episode-{episode}",
                    payload=request.to_payload(),
                    source=source,
                    user_id=user_id,
                )
            except TextGenerationError as exc:
                raise CanvasGraphError("canvas_run_failed", message=str(exc)) from exc
            except WorkerOfflineError as exc:
                raise CanvasGraphError("canvas_worker_offline") from exc
            task_ids.append(result["task_id"])
        return {"success": True, "task_id": task_ids[0], "task_ids": task_ids}

    async def _run_keyframe(self, project_name: str, node_id: str, *, user_id: str, source: str) -> dict[str, Any]:
        _, _, episode, sheet_id = node_id.split(":", 3)
        try:
            result = await enqueue_keyframe_sheet(
                self.pm, project_name, int(episode), sheet_id, user_id=user_id, source=source, queue=self.queue
            )
        except KeyframePlanError as exc:
            raise CanvasGraphError("canvas_run_failed", message=exc.message) from exc
        return {"success": True, "task_id": result["task_id"], "task_ids": [result["task_id"]]}
