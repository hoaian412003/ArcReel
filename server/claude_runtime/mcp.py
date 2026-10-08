"""Project-bound SDK MCP adapter using the canonical ArcReel declarations."""

from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server
from claude_agent_sdk.types import McpSdkServerConfig

from lib.project.project_manager import ProjectManager
from server.agent_toolset.declaration import ToolDeclaration, invoke_declaration
from server.agent_toolset.remote import remote_description, remote_result
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.tool_runtime import CallerContext, ProjectScope, Services


def project_mcp(projects: ProjectManager, project_name: str, user_id: str) -> McpSdkServerConfig:
    scope = ProjectScope(project_name=project_name, data_root=projects.data_root)
    services = Services.defaults(projects)
    caller = CallerContext(user_id=user_id, source="mcp")

    def bind(declaration: ToolDeclaration) -> SdkMcpTool[Any]:
        async def invoke(arguments: dict[str, Any]) -> dict[str, Any]:
            outcome = await invoke_declaration(declaration, arguments, scope, caller, services)
            return remote_result(declaration, outcome).model_dump(mode="json", exclude_none=True)

        return SdkMcpTool(declaration.name, remote_description(declaration), declaration.input_schema, invoke)

    return create_sdk_mcp_server("arcreel", tools=[bind(d) for d in AGENT_TOOLSET if isinstance(d, ToolDeclaration)])
