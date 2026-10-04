"""Task timeline and queue audit ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base


class TaskEvent(Base):
    """任务生命周期与进度的追加式时间线；每行是一次已发生的事实，不更新不删除。"""

    __tablename__ = "task_timeline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 不设外键：任务清理不应连带抹掉审计线索，且写入路径不能因任务行暂不可见而失败。
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    stage: Mapped[str | None] = mapped_column(String)
    percent: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (Index("idx_task_timeline_task_id_id", "task_id", "id"),)


class QueueAudit(Base):
    """队列准入与管理操作的审计日志：接受 / 拒绝入队、取消、人工判失败、容量变更、对账判失败。"""

    __tablename__ = "queue_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String)
    project_name: Mapped[str | None] = mapped_column(String)
    task_type: Mapped[str | None] = mapped_column(String)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("idx_queue_audit_at", "at"),
        Index("idx_queue_audit_action_at", "action", "at"),
        Index("idx_queue_audit_task_id", "task_id"),
    )
