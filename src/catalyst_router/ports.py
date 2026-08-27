from __future__ import annotations

from typing import Protocol

from catalyst_router.domain import (
    AgentState,
    DecisionRecord,
    PublicDecisionRecord,
    ReconciliationSnapshot,
)


class PaperBroker(Protocol):
    def reconciliation_snapshot(self) -> ReconciliationSnapshot: ...


class OperationalStore(Protocol):
    def initialize(self) -> AgentState: ...

    def get_agent_state(self) -> AgentState: ...

    def begin_execution(self) -> AgentState: ...

    def commit_reconciliation(self, epoch: str, record: DecisionRecord) -> AgentState: ...

    def append_decision(self, record: DecisionRecord) -> None: ...

    def list_public_decisions(self, limit: int = 50) -> list[PublicDecisionRecord]: ...
