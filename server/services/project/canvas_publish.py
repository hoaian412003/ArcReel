"""画布上的资产分组 → 项目资产：把画布内容写成项目里的资产条目与资产图。

画布是创作的地方，``project.json`` 里的资产表是给剧本、分镜、视频这些下游用的投影：
- 分组标题是资产名，``data.asset`` 是资产类型；
- 描述 = 接到图片供应商 ``prompt`` 口的文本（没有供应商就取组里第一个文本）；
- 资产图 = 组里资产节点接着的那张图，经与「上传资产图」同一条入口安装（带版本与产物登记）。
只有画布上的内容变了才写项目（记下上次写出去的描述与图），所以项目里别处（资产页、Agent）改的内容不会被画布旧值冲回。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import register_current_resource_artifact
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.canvas.graph import (
    NODE_ASSET,
    NODE_GROUP,
    NODE_IMAGE_GENERATE,
    NODE_TEXT,
    find_node,
    resolve_asset_node,
    wire_result_to_asset,
)
from lib.canvas.graph import _incoming as incoming
from lib.canvas.store import load_canvas, mutate_canvas
from lib.infra.image_utils import normalize_uploaded_image
from lib.infra.json_io import atomic_write_bytes
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.asset_derivatives import derivative_artifact_id, derivative_sheet_relative_path
from lib.project.asset_types import ASSET_SPECS, resolve_asset_key
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from server.services.currency.upload_finalize import install_manual_asset_sheet_upload

logger = logging.getLogger(__name__)

_PUBLISHABLE = frozenset(ASSET_SPECS)


def _description(graph: dict[str, Any], children: list[dict[str, Any]]) -> str:
    generators = [n for n in children if n["type"] == NODE_IMAGE_GENERATE]
    if generators:
        texts = [str(n["data"].get("text") or "").strip() for n in incoming(graph, generators[0]["id"], "prompt")]
    else:
        texts = [str(n["data"].get("text") or "").strip() for n in children if n["type"] == NODE_TEXT][:1]
    return "\n\n".join(t for t in texts if t)


def _add_asset(pm: ProjectManager, kind: str, project_name: str, name: str, description: str) -> None:
    if kind == "character":
        pm.add_character(project_name, name, description)
    elif kind == "scene":
        pm.add_project_scene(project_name, name, description)
    elif kind == "prop":
        pm.add_prop(project_name, name, description)
    else:
        pm.add_product(project_name, name, description)


def publish_asset_groups(pm: ProjectManager, project_name: str) -> bool:
    """按画布上的资产分组写项目资产；返回有没有写入。失败只记日志，不影响画布编辑。"""
    project_path = pm.get_project_path(project_name)
    graph = load_canvas(project_path)
    groups = [
        g
        for g in graph["nodes"]
        if g["type"] == NODE_GROUP
        and g["data"].get("asset") in _PUBLISHABLE
        and str(g["data"].get("label") or "").strip()
    ]
    if not groups:
        return False
    stamps: list[tuple[str, str, str]] = []  # (节点 id, data 字段, 值)
    wrote = False
    for group in groups:
        kind = str(group["data"]["asset"])
        name = str(group["data"]["label"]).strip()
        children = [n for n in graph["nodes"] if n.get("parentId") == group["id"]]
        try:
            wrote |= _publish_group(pm, project_name, project_path, graph, group, kind, name, children, stamps)
        except Exception:
            logger.exception("发布资产分组失败 project=%s name=%s", project_name, name)
    if stamps:

        def _record(g: dict[str, Any]) -> None:
            for node_id, field, value in stamps:
                try:
                    find_node(g, node_id)["data"][field] = value
                except Exception:
                    continue

        mutate_canvas(pm, project_path, _record)
    return wrote


def _publish_group(
    pm: ProjectManager,
    project_name: str,
    project_path: Path,
    graph: dict[str, Any],
    group: dict[str, Any],
    kind: str,
    name: str,
    children: list[dict[str, Any]],
    stamps: list[tuple[str, str, str]],
) -> bool:
    spec = ASSET_SPECS[kind]
    wrote = False
    description = _description(graph, children)
    with project_change_source("webui"):
        if description and group["data"].get("published_description") != description:
            bucket = pm.load_project(project_name).get(spec.bucket_key)
            if resolve_asset_key(bucket, name) is None:
                _add_asset(pm, kind, project_name, name, description)
            else:
                pm.update_asset_entry(kind, project_name, name, lambda entry: entry.update(description=description))
            stamps.append((group["id"], "published_description", description))
            wrote = True
        for node in children:
            if node["type"] != NODE_ASSET:
                continue
            if node["data"].get("derivative"):
                if kind == "character":
                    wrote |= _publish_derivative(pm, project_name, project_path, graph, node, name, stamps)
                continue
            wrote |= _publish_sheet(pm, project_name, project_path, graph, node, kind, name, stamps)
    return wrote


def _publish_sheet(
    pm: ProjectManager,
    project_name: str,
    project_path: Path,
    graph: dict[str, Any],
    node: dict[str, Any],
    kind: str,
    name: str,
    stamps: list[tuple[str, str, str]],
) -> bool:
    spec = ASSET_SPECS[kind]
    data = resolve_asset_node(graph, node)["data"]
    source = data.get("path")
    if not isinstance(source, str) or not source:
        return False
    stamp = f"{source}@{data.get('version')}"
    if node["data"].get("published") == stamp:
        return False
    if resolve_asset_key(pm.load_project(project_name).get(spec.bucket_key), name) is None:
        return False  # 资产条目还没建（还没有描述），图先留着，描述有了再发布
    try:
        file = safe_join(project_path, source)
    except PathTraversalError:
        return False
    if not file.is_file():
        return False
    content, suffix = normalize_uploaded_image(file.read_bytes(), file.suffix)
    target = f"{spec.subdir}/{name}{suffix}"
    if source != target:
        install_manual_asset_sheet_upload(
            project_manager=pm,
            project_name=project_name,
            asset_type=kind,
            name=name,
            sheet_path=target,
            content=content,
            original_filename=file.name,
        )
    stamps.append((node["id"], "published", stamp))
    return source != target


def _publish_derivative(
    pm: ProjectManager,
    project_name: str,
    project_path: Path,
    graph: dict[str, Any],
    node: dict[str, Any],
    owner: str,
    stamps: list[tuple[str, str, str]],
) -> bool:
    """角色分组里带 ``derivative`` 的资产节点：它的输入图成为该衍生的资产图（衍生条目须已在项目里）。"""
    spec = ASSET_SPECS["character"]
    derivative = str(node["data"]["derivative"])
    data = resolve_asset_node(graph, node)["data"]
    source = data.get("path")
    if not isinstance(source, str) or not source:
        return False
    stamp = f"{source}@{data.get('version')}"
    if node["data"].get("published") == stamp:
        return False
    project = pm.load_project(project_name)
    owner_key = resolve_asset_key(project.get(spec.bucket_key), owner)
    entry = project[spec.bucket_key].get(owner_key) if owner_key else None
    table = entry.get("derivatives") if isinstance(entry, dict) else None
    derivative_key = resolve_asset_key(table, derivative)
    if owner_key is None or derivative_key is None:
        return False
    try:
        file = safe_join(project_path, source)
    except PathTraversalError:
        return False
    if not file.is_file():
        return False
    content, _suffix = normalize_uploaded_image(file.read_bytes(), file.suffix)
    target_path = derivative_sheet_relative_path(owner_key, derivative_key)
    if source == target_path:
        stamps.append((node["id"], "published", stamp))
        return False
    target = safe_join(project_path, target_path)
    artifact_id = derivative_artifact_id(owner_key, derivative_key)

    def _mutate(locked: dict[str, Any]) -> None:
        locked[spec.bucket_key][owner_key]["derivatives"][derivative_key][spec.sheet_field] = target_path

    def _claim() -> None:
        register_current_resource_artifact(
            project_path, resource_type=CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_id=artifact_id
        )

    def _install(_project_file: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, content)
        VersionManager(project_path).commit_installed_version(
            CHARACTER_DERIVATIVE_RESOURCE_TYPE,
            artifact_id,
            "",
            current_file=target,
            on_commit=_claim,
            source=MANUAL_UPLOAD_VERSION_SOURCE,
            original_filename=file.name,
        )

    pm.update_project(project_name, _mutate, on_commit=_install, formal_paths=(target,))
    stamps.append((node["id"], "published", stamp))
    return True


def finish_run(pm: ProjectManager, project_name: str, generator_id: str, media_id: str) -> None:
    """图片供应商一次运行出了图：接到所在资产分组的资产节点上，并发布到项目。"""
    project_path = pm.get_project_path(project_name)
    try:
        mutate_canvas(pm, project_path, lambda g: wire_result_to_asset(g, generator_id, media_id))
    except Exception:
        logger.exception("运行结果接入资产节点失败 project=%s generator=%s", project_name, generator_id)
        return
    publish_asset_groups(pm, project_name)
