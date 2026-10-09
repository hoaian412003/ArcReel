"""Append-only durable event stream for Claude Agent sessions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select

from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.session_event import AgentSessionEventLogEntry
from lib.db.repositories.base import BaseRepository


def _event_dict(row: AgentSessionEventLogEntry) -> dict[str, Any]:
    return {
        "session_id": row.session_id,
        "seq": row.seq,
        "type": row.entry_type,
        "payload": row.payload,
        "created_at": dt_to_iso(row.created_at),
    }


class AgentEventRepository(BaseRepository):
    async def append(
        self,
        *,
        session_id: str,
        entry_type: str,
        payload: dict[str, Any],
        user_id: str = DEFAULT_USER_ID,
        client_key: str | None = None,
    ) -> dict[str, Any]:
        if client_key:
            existing = await self.find_by_client_key(session_id, client_key, user_id=user_id)
            if existing is not None:
                return existing
        scalar_seq = await self.session.scalar(
            select(func.coalesce(func.max(AgentSessionEventLogEntry.seq), 0) + 1).where(
                AgentSessionEventLogEntry.session_id == session_id
            )
        )
        next_seq = int(scalar_seq or 1)
        now = utc_now()
        row = AgentSessionEventLogEntry(
            session_id=session_id,
            seq=next_seq,
            entry_type=entry_type,
            payload=payload,
            client_key=client_key,
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return _event_dict(row)

    async def list_after(
        self,
        session_id: str,
        after_seq: int,
        *,
        user_id: str = DEFAULT_USER_ID,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(AgentSessionEventLogEntry)
            .where(
                AgentSessionEventLogEntry.session_id == session_id,
                AgentSessionEventLogEntry.user_id == user_id,
                AgentSessionEventLogEntry.seq > after_seq,
            )
            .order_by(AgentSessionEventLogEntry.seq)
            .limit(min(max(limit, 1), 5000))
        )
        return [_event_dict(row) for row in result.scalars()]

    async def turn_start_seq(
        self,
        session_id: str,
        *,
        turns: int,
        before_seq: int | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> int | None:
        """Seq of the user message that opens the `turns`-th most recent turn (below `before_seq`).

        None means fewer than `turns` turns exist, so the history starts at the beginning.
        """
        query = select(AgentSessionEventLogEntry.seq).where(
            AgentSessionEventLogEntry.session_id == session_id,
            AgentSessionEventLogEntry.user_id == user_id,
            AgentSessionEventLogEntry.entry_type == "user_message",
        )
        if before_seq is not None:
            query = query.where(AgentSessionEventLogEntry.seq < before_seq)
        result = await self.session.scalar(
            query.order_by(AgentSessionEventLogEntry.seq.desc()).offset(max(turns, 1) - 1).limit(1)
        )
        return int(result) if result is not None else None

    async def list_before(
        self,
        session_id: str,
        *,
        after_seq: int,
        before_seq: int,
        user_id: str = DEFAULT_USER_ID,
    ) -> list[dict[str, Any]]:
        result = await self.session.execute(
            select(AgentSessionEventLogEntry)
            .where(
                AgentSessionEventLogEntry.session_id == session_id,
                AgentSessionEventLogEntry.user_id == user_id,
                AgentSessionEventLogEntry.seq > after_seq,
                AgentSessionEventLogEntry.seq < before_seq,
            )
            .order_by(AgentSessionEventLogEntry.seq)
        )
        return [_event_dict(row) for row in result.scalars()]

    async def find_by_client_key(
        self, session_id: str, client_key: str, *, user_id: str = DEFAULT_USER_ID
    ) -> dict[str, Any] | None:
        result = await self.session.execute(
            select(AgentSessionEventLogEntry).where(
                AgentSessionEventLogEntry.session_id == session_id,
                AgentSessionEventLogEntry.client_key == client_key,
                AgentSessionEventLogEntry.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        return _event_dict(row) if row else None

    async def delete_session(self, session_id: str) -> None:
        await self.session.execute(
            delete(AgentSessionEventLogEntry).where(AgentSessionEventLogEntry.session_id == session_id)
        )
