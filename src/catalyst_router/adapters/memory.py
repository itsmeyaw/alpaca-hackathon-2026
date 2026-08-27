from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import uuid4

from catalyst_router.domain import AgentState, DecisionRecord, PublicDecisionRecord


class InMemoryOperationalStore:
    def __init__(self, public_delay_seconds: int = 0) -> None:
        self._lock = RLock()
        self._state: AgentState | None = None
        self._decisions: dict[str, DecisionRecord] = {}
        self._public_delay = timedelta(seconds=public_delay_seconds)

    def initialize(self) -> AgentState:
        with self._lock:
            if self._state is None:
                self._state = AgentState()
            return self._state

    def get_agent_state(self) -> AgentState:
        with self._lock:
            return self.initialize()

    def begin_execution(self) -> AgentState:
        with self._lock:
            state = self.initialize()
            self._state = state.model_copy(
                update={
                    "version": state.version + 1,
                    "execution_epoch": str(uuid4()),
                    "reconciled_epoch": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            return self._state

    def commit_reconciliation(self, epoch: str, record: DecisionRecord) -> AgentState:
        with self._lock:
            state = self.initialize()
            if state.execution_epoch != epoch:
                raise RuntimeError("execution epoch changed during reconciliation")
            if record.decision_id in self._decisions:
                raise ValueError(f"decision already exists: {record.decision_id}")
            self._state = state.model_copy(
                update={
                    "version": state.version + 1,
                    "reconciled_epoch": epoch,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._decisions[record.decision_id] = record
            return self._state

    def append_decision(self, record: DecisionRecord) -> None:
        with self._lock:
            if record.decision_id in self._decisions:
                raise ValueError(f"decision already exists: {record.decision_id}")
            self._decisions[record.decision_id] = record

    def list_public_decisions(self, limit: int = 50) -> list[PublicDecisionRecord]:
        with self._lock:
            now = datetime.now(UTC)
            records = [
                record
                for record in self._decisions.values()
                if record.public and record.occurred_at + self._public_delay <= now
            ]
            selected = sorted(records, key=lambda record: record.occurred_at, reverse=True)[:limit]
            return [record.public_projection() for record in selected]
