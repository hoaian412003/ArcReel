---
status: accepted
---

# 队列服务可作为独立进程运行，PostgreSQL 承载跨进程信号，监控与审计从表里取数

生成 worker 原本只活在 FastAPI 的 lifespan 里：后端重启、崩溃或发版就打断在跑的任务（关停宽限期、放弃、孤儿续跑都是为这个缺口打的补丁）。我们新增第二种运行形态——`python -m server.queue_worker` 起一个独立进程，后端以 `QUEUE_WORKER_MODE=external` 运行时不再创建内嵌 worker，只负责入队与读状态。内嵌形态（默认）保持不变，SQLite 单机部署不受影响。

这是 ADR 0083 列出的「重新评估」条件之一（需要多进程 / 仅 PostgreSQL 的队列）被满足，但结论仍是**不引入第三方队列库**：领域语义（容量随配置热更新、孤儿不重入队而按 job_id 续跑、整批准入、按供应商记账）无论如何都要自己写，换库省不下它们；独立进程只是把同一份 worker 搬出 lifespan。独立形态要求 PostgreSQL（两个进程不能安全共享 SQLite 的写锁）。

## Decisions

- **进程边界**：后端与队列服务共享同一数据库与项目存储卷，用同一镜像、不同入口。数据库迁移只由后端执行，队列服务等库升到最新 head 再启动，避免两个进程同时跑 Alembic。
- **跨进程信号走 PostgreSQL LISTEN/NOTIFY**（`lib/infra/pg_bus.py`），载荷只是刷新信号，真相始终在表里：
  - `arcreel_queue_wake`：入队后唤醒 worker，免去轮询间隔；worker 仍保留轮询兜底。
  - `arcreel_queue_control`：配置变更（清执行层缓存 + 重载容量）、强制中断。监听断线重连后补发一次 resync，等价于一次配置变更。
  - `arcreel_project_change`：队列服务里发出的任务终态 / 用量事件转送到后端的进程内事件总线，再由现有 SSE 推给前端（`server/project_event_bridge.py`）。SQLite 下 `notify` 是空操作。
- **取消语义不变（ADR 0006）**：只有 `queued` 可取消；执行中任务已提交给供应商，不取消。新增的是人工「标记失败」与自动对账，二者都只改我方记录，不触达供应商，失败文案如实说明供应商侧可能仍在计费。
- **对账（`queue_reconciler`）只处理无存活执行者的任务**：`running`、不在 worker 占用台账内、`last_synced_at`（缺省 `started_at`）早于阈值（`queue_sync_stale_seconds`，默认 900 秒，下限 120）。仍在 worker 内轮询的任务不会被从外面改判，避免产物与状态分叉；它们的失联只通过 `arcreel_queue_stale_running` 暴露。`last_synced_at` 由轮询层每次收到供应商成功响应时刷新（`lib/infra/poll_sync_hook.py`，backends 不依赖队列）。
- **全局并发上限**（`queue_global_max_image|video|audio`，system_setting，缺省不限）叠加在逐供应商容量之上：同媒体跨供应商的总占用达到上限就不再认领。worker 每 15 秒及收到配置变更时重载，调整无需重启。
- **准入与审计集中在 `GenerationQueue.enqueue_task`**：所有 Web / Agent / 批量入队经此一处。接受时写输入摘要（规范化 JSON 的 sha256）、预估费用（定价声明，自定义供应商与未登记模型为空而非 0）、时间线事件与 `enqueue_accepted` 审计；拒绝写 `enqueue_rejected`。预估超过可选的单任务预算（`queue_max_estimated_cost_per_task`）时拒绝入队：派生出 provider 之后才有预估可比，故先入队再趁排队立即取消。实际费用与 RH coin 取自 `api_calls`（`cost_amount` / `provider_coins`），与预估并列展示。
- **时间线（`task_timeline`）与审计（`queue_audit`）是旁路追加表**：写入失败只记日志，不影响状态迁移与计费。进度只记里程碑（排队 0、执行 10、已提交 30、完成 100）与轮询同步心跳，供应商不报百分比时不伪造连续值。表名避开历史上已删除的 `task_events`（迁移 3649100774fa 的降级会重建旧结构）。
- **监控**：队列服务在 `:9100` 暴露 `/metrics` 与 `/health`，指标在抓取时从表里现算（队列深度、最早排队年龄、逐 lane 在跑 / 排队 / 上限、失联数、24 小时成本与 RH coin、调用失败数），后端或队列服务任何一方重启都不会让曲线归零。不引入 prometheus_client 依赖，手写文本格式。管理面（`/api/v1/admin/queue/*`，仅 admin 角色）与前端 `/app/queue` 页面读同一批表。

## Consequences

- 独立形态下 worker 的内存状态（占用台账、容量表）只在队列服务进程里；后端要知道 lane 使用情况只能查表，`/admin/queue/overview` 即如此。
- 强制中断（ADR 0006 的显式例外）经总线下发：队列服务不在线时消息丢失，任务保持 `running`，待下次启动的孤儿扫描处置。内嵌且无 worker 时仍就地落终态。
- 多个队列服务副本依赖 `claim_next` 的条件更新与 worker 租约互斥；ADR 0007 对孤儿的判定仍以「只有一个 worker 持租约」为前提，横向扩副本前必须重审该判定。
- 管理面写操作（取消、标记失败、并发上限变更）与准入拒绝都进审计，审计只增不改。
