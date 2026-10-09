"""资产删除工具：与网页删除资产走同一写入口（连同资产图声明与衍生图一并清理）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lib.infra.async_thread import run_sync_transaction
from lib.project.project_change_hints import project_change_source
from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.tool_runtime import (
    ASSET_TABLES,
    CallerContext,
    ProjectScope,
    Services,
    ToolMessage,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
)


class DeleteAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str = Field(
        description=f"资产表，取值 {list(ASSET_TABLES)} 之一", json_schema_extra={"enum": list(ASSET_TABLES)}
    )
    name: str = Field(min_length=1, description="要删除的资产名")

    @field_validator("table")
    @classmethod
    def _validate_table(cls, value: str) -> str:
        if value not in ASSET_TABLES:
            raise ValueError(f"table 必须是 {list(ASSET_TABLES)} 之一")
        return value


def _delete(services: Services, project_name: str, table: str, name: str) -> None:
    with project_change_source("worker"):
        services.projects.delete_asset(project_name, table, name)


async def delete_asset(
    request: ToolRequest[DeleteAssetRequest], scope: ProjectScope, _caller: CallerContext, services: Services
) -> ToolOutcome[ToolMessage]:
    value = request.value
    try:
        await run_sync_transaction(_delete, services, scope.project_name, value.table, value.name)
    except KeyError:
        return ToolOutcome(problem=ToolProblem("not_found", f"{value.table} 中没有资产 {value.name!r}"))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"delete_asset 失败: {exc}"))
    return ToolOutcome(value=ToolMessage(message=f"已删除 {value.table} 资产 {value.name!r}（含衍生图与资产图声明）。"))


DELETE_ASSET = ToolDeclaration(
    name="delete_asset",
    description=(
        "删除一个资产（角色 / 场景 / 道具 / 商品）及其衍生图与资产图声明，等同网页上的删除按钮；"
        "资产图文件保留在历史留存中，同名重建可接上。已有剧本对该资产的引用不会被改写，删前先确认没有仍在使用它的剧本。"
        "不可逆（可经本轮开始前的项目快照回退），批量删除前先向用户确认。"
    ),
    request_model=DeleteAssetRequest,
    migration=BLOCKED,
    domain_key="asset_delete",
    handler=delete_asset,
)

ASSET_ADMIN_TOOLS = (DELETE_ASSET,)

__all__ = ["ASSET_ADMIN_TOOLS", "DELETE_ASSET", "DeleteAssetRequest"]
