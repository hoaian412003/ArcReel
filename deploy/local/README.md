# 本地栈（Docker Compose）

从源码构建，PostgreSQL + 独立队列服务 + Prometheus / Grafana。凭据均为本机演示值（`123123123`），端口只绑定 `127.0.0.1`。

```bash
docker compose -f deploy/local/docker-compose.yml up -d --build
```

| 服务 | 地址 | 说明 |
|---|---|---|
| arcreel | http://localhost:1241 | Web 后端，只入队与读状态；页面 `/app/queue` 为队列监控与审计 |
| queue-worker | http://localhost:9100/metrics | 独立队列服务，执行 / 轮询 / 续跑 / 对账 |
| postgres | localhost:5432 | 用户 `arcreel`，库 `arcreel` |
| prometheus | http://localhost:9090 | 告警规则见 `monitoring/alerts.yml` |
| grafana | http://localhost:3000 | `admin` / `123123123`，看板 ArcReel Queue |

后端重启或发版不会打断在跑的任务：

```bash
docker compose -f deploy/local/docker-compose.yml up -d --build arcreel
```

## 从 SQLite 迁移现有数据

```bash
docker compose -f deploy/local/docker-compose.yml up -d postgres
uv run python scripts/migrate_sqlite_to_postgres.py \
  --source projects/arcreel.db \
  --target postgresql+asyncpg://arcreel:123123123@localhost:5432/arcreel --replace
```

迁移前先停掉写入 SQLite 的进程；`--replace` 只清空目标库。源库文件原样保留作备份。
