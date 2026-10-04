---
paths:
  - "lib/agent/profile_manifest.py"
  - "agent_runtime_profile/**"
  - "pyproject.toml"
  - "uv.lock"
---

# Agent 配置源与外部 Agent 工具

## 运行时不变量

- Agent 工具由 `server/agent_toolset/` 的声明定义，经远程 MCP（`server/remote_mcp.py`）暴露给外部 Agent（`docs/adr/0087`）。

## Agent 配置源

`agent_runtime_profile/` 是外部 Agent 使用的配置源：`.claude/skills/`、`.claude/agents/` 与按 `content_mode` 拆分的 `CLAUDE.*.md`。`lib/agent/profile_manifest.py` 把它们物化到各用户项目的 `.claude/` 与 CLAUDE.md，以 manifest + sha256 识别用户修改过的项目侧文件并予以保留。修改配置应改动源目录，项目侧文件由物化生成。

修改 Skill 时 SKILL.md 与其脚本须同步更新；Skill 的写作规范见 `/writing-for-agents`。
