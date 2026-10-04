"""Persistence operations for Claude Agent turns."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update

from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.agent_turn import AgentTurn
from lib.db.models.session import AgentSession
from lib.db.repositories.base import BaseRepository, rowcount


def _turn_dict(row: AgentTurn) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "client_key": row.client_key,
        "status": row.status,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cache_creation_input_tokens": row.cache_creation_input_tokens,
        "cache_read_input_tokens": row.cache_read_input_tokens,
        "total_cost_usd": float(row.total_cost_usd) if row.total_cost_usd is not None else None,
        "usage_snapshot": row.usage_snapshot,
        "model_usage_snapshot": row.model_usage_snapshot,
        "created_at": dt_to_iso(row.created_at),
        "updated_at": dt_to_iso(row.updated_at),
    }


class AgentTurnRepository(BaseRepository):
    async def find_for_project_client_key(
        self,
        project_name: str,
        client_key: str,
        *,
        user_id: str = DEFAULT_USER_ID,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Resolve an idempotent new-session retry without a session id."""
        result = await self.session.execute(
            select(AgentTurn, AgentSession)
            .join(AgentSession, AgentSession.id == AgentTurn.session_id)
            .where(
                AgentSession.project_name == project_name,
                AgentSession.user_id == user_id,
                AgentTurn.client_key == client_key,
                AgentTurn.user_id == user_id,
            )
            .order_by(AgentTurn.created_at.desc())
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        turn, session = row
        return _turn_dict(turn), {
            "id": session.id,
            "project_name": session.project_name,
            "title": session.title or "",
            "status": session.status,
            "created_at": dt_to_iso(session.created_at),
            "updated_at": dt_to_iso(session.updated_at),
        }

    async def create(
        self,
        *,
        session_id: str,
        client_key: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        now = utc_now()
        row = AgentTurn(
            id=uuid4().hex,
            session_id=session_id,
            client_key=client_key,
            status="accepted",
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return _turn_dict(row)

    async def find_by_client_key(
        self, session_id: str, client_key: str, *, user_id: str = DEFAULT_USER_ID
    ) -> dict[str, Any] | None:
        result = await self.session.execute(
            select(AgentTurn).where(
                AgentTurn.session_id == session_id,
                AgentTurn.client_key == client_key,
                AgentTurn.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        return _turn_dict(row) if row else None

    async def list_for_session(
        self,
        session_id: str,
        *,
        user_id: str = DEFAULT_USER_ID,
    ) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(AgentTurn)
            .where(AgentTurn.session_id == session_id, AgentTurn.user_id == user_id)
            .order_by(AgentTurn.created_at)
        )
        return [_turn_dict(row) for row in result.scalars()]

    async def latest_completed_before(
        self, session_id: str, turn_id: str, *, user_id: str = DEFAULT_USER_ID
    ) -> dict[str, Any] | None:
        current_created_at = select(AgentTurn.created_at).where(AgentTurn.id == turn_id).scalar_subquery()
        result = await self.session.execute(
            select(AgentTurn)
            .where(
                AgentTurn.session_id == session_id,
                AgentTurn.user_id == user_id,
                AgentTurn.status == "completed",
                AgentTurn.created_at < current_created_at,
            )
            .order_by(AgentTurn.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return _turn_dict(row) if row else None

    async def mark_running(self, turn_id: str) -> bool:
        return await self._set(turn_id, status="running")

    async def complete(
        self,
        turn_id: str,
        *,
        status: str = "completed",
        usage: dict[str, int | None],
        usage_snapshot: dict[str, Any] | None,
        model_usage_snapshot: dict[str, Any] | None,
        total_cost_usd: float | None,
    ) -> bool:
        return await self._set(
            turn_id,
            status=status,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
            cache_read_input_tokens=usage.get("cache_read_input_tokens"),
            total_cost_usd=Decimal(str(total_cost_usd)) if total_cost_usd is not None else None,
            usage_snapshot=usage_snapshot,
            model_usage_snapshot=model_usage_snapshot,
        )

    async def fail(self, turn_id: str, *, error_code: str, error_message: str) -> bool:
        return await self._set(
            turn_id,
            status="error",
            error_code=error_code,
            error_message=error_message[:2000],
        )

    async def _set(self, turn_id: str, **values: Any) -> bool:
        values["updated_at"] = utc_now()
        result = await self.session.execute(update(AgentTurn).where(AgentTurn.id == turn_id).values(**values))
        await self.session.flush()
        return rowcount(result) > 0
