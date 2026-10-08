# Project Claude agent implementation

Status: completed and deployed locally (2026-10-05).

## Scope and progress

- [x] Persist one agent identity and stable workspace per project; preserve existing sessions.
- [x] Load native tools, project skills, ArcReel MCP and project memory automatically.
- [x] Serialize turns across project sessions and recover queued work after restart.
- [x] Add versioned, idempotent contextual actions and send directly from Ask the agent controls.
- [x] Show queued/running state and retain action context in chat history.
- [x] Add project agent health, memory viewing/editing, promotion, reset and export.
- [x] Run backend/frontend checks and rebuild local Docker for web testing.

## Decisions

- Project identity and workspace remain stable when display names change.
- Keep the existing immutable project directory as cwd. Runtime skills derive the data root from that directory, so moving projects to a separate `/workspace` tree would break their contract and old native session lookup. Pin cwd in the agent manifest instead.
- Project source stays in the existing project directory. Agent runtime data is separate.
- Legacy session cwd is retained so native transcripts remain resumable.
- One modifying turn runs per project, including turns from different conversations.
- Actions use backend-owned prompt templates and structured resource parameters.
- Memory reset does not delete project source or session history.
- Native system access applies inside the Docker container and its mounted folders, not automatically to the entire host computer.
- Project turn serialization is process-local; the current deployment uses one API process. Multiple API replicas would require a distributed project lock.

## Verification

- Targeted backend runtime, workspace, action and migration tests: 21 passed.
- Targeted frontend action/chat/redo tests: 20 passed.
- Full backend suite: 14,959 passed, 2 skipped. Run with `AUTH_ENABLED=true` to prevent the local development `.env` authentication override from leaking into auth tests.
- Backend static checks: Ruff, formatting, basedpyright, import contracts and dependency audit passed.
- Full frontend suite: 218 files, 2,571 tests passed. TypeScript, ESLint and unused-code checks passed. The updated H3 fixture also passed its 9 targeted tests.
- Test structure audit: passed.
- Repaired outdated test doubles encountered by the full gates: video provider coin result, async cost-estimation steps and frontend API spies.
- Docker image rebuilt; API, queue worker and database are healthy at `http://127.0.0.1:1241`.
- Native Claude smoke test succeeded with stored subscription login, connected ArcReel MCP, 66 available tools, and actual read-only MCP and Bash calls. No project assets or memory were changed by the smoke test.
- Browser verification: floating agent window shows connected Claude, project tools, 10 project skills and the memory controls.
