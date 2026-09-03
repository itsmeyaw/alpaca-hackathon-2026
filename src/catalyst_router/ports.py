from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from catalyst_router.domain import (
    AgentMode,
    AgentState,
    BrokerOrderSnapshot,
    DecisionRecord,
    OrderExecution,
    OrderExecutionStatus,
    OrderPlan,
    PublicDecisionPage,
    PublicDecisionRecord,
    PublicPortfolioPoint,
    ReconciliationSnapshot,
    Route,
)
from catalyst_router.universe import UniverseSnapshot


class PaperBroker(Protocol):
    def reconciliation_snapshot(self) -> ReconciliationSnapshot: ...

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot | None: ...

    def submit_order(self, plan: OrderPlan) -> BrokerOrderSnapshot: ...

    def submit_option_exit(
        self, symbol: str, quantity: int, client_order_id: str
    ) -> BrokerOrderSnapshot: ...

    def close_position(self, symbol: str) -> None: ...

    def flatten(self) -> None: ...


class OperationalStore(Protocol):
    def initialize(self) -> AgentState: ...

    def get_agent_state(self) -> AgentState: ...

    def begin_execution(self) -> AgentState: ...

    def transition_agent_mode(
        self, mode: AgentMode, *, reason: str, record: DecisionRecord
    ) -> AgentState: ...

    def commit_reconciliation(
        self, epoch: str, record: DecisionRecord, *, equity: Decimal | None = None
    ) -> AgentState: ...

    def append_decision(self, record: DecisionRecord) -> None: ...

    def append_decision_once(
        self, record: DecisionRecord, *, expected_epoch: str | None = None
    ) -> bool: ...

    def append_public_portfolio(
        self, point: PublicPortfolioPoint, *, expected_epoch: str
    ) -> bool: ...

    def get_daily_universe(self, session_date: date) -> UniverseSnapshot | None: ...

    def put_daily_universe(self, snapshot: UniverseSnapshot, *, expected_epoch: str) -> bool: ...

    def claim_event_extraction(
        self,
        source_id: str,
        model_id: str,
        prompt_version: str,
        *,
        expected_epoch: str,
    ) -> bool: ...

    def release_event_extraction(
        self,
        source_id: str,
        model_id: str,
        prompt_version: str,
        *,
        expected_epoch: str,
    ) -> None: ...

    def claim_order(
        self, execution: OrderExecution, *, expected_epoch: str, max_active_orders: int
    ) -> bool: ...

    def get_order(self, client_order_id: str) -> OrderExecution | None: ...

    def clear_active_order(self, client_order_id: str) -> AgentState: ...

    def update_equity_peak(self, equity: Decimal) -> AgentState: ...

    def update_order(
        self,
        execution: OrderExecution,
        *,
        expected_status: OrderExecutionStatus,
    ) -> OrderExecution: ...

    def list_public_decisions(self, limit: int = 50) -> list[PublicDecisionRecord]: ...

    def list_public_routes(self, limit: int = 100) -> list[PublicDecisionRecord]: ...

    def list_public_portfolio(self, limit: int = 200) -> list[PublicPortfolioPoint]: ...

    def list_public_decision_page(
        self,
        *,
        limit: int = 25,
        cursor: str | None = None,
        search: str | None = None,
        route: Route | None = None,
        decision_type: str | None = None,
    ) -> PublicDecisionPage: ...
