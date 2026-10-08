"""Durable Claude Agent turn and usage accounting."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class AgentTurn(TimestampMixin, UserOwnedMixin, Base):
    """One accepted user prompt and its terminal SDK usage snapshot."""

    __tablename__ = "agent_turns"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_key: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="accepted")
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_creation_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_read_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    usage_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_usage_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("session_id", "client_key", name="uq_agent_turn_session_client_key"),
        Index("ix_agent_turn_session_created", "session_id", "created_at"),
        Index("ix_agent_turn_status", "status"),
    )
