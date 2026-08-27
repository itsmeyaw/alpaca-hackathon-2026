from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentMode(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RISK_HALTED = "RISK_HALTED"
    KILLED = "KILLED"


class Route(StrEnum):
    CATALYST_CONTINUATION = "CATALYST_CONTINUATION"
    LIQUIDITY_REVERSION = "LIQUIDITY_REVERSION"
    REGIME_TREND = "REGIME_TREND"
    NO_TRADE = "NO_TRADE"


class InstrumentType(StrEnum):
    EQUITY = "EQUITY"
    OPTION = "OPTION"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class RiskDecisionStatus(StrEnum):
    APPROVED = "APPROVED"
    REDUCED = "REDUCED"
    VETOED = "VETOED"


class AgentState(FrozenModel):
    mode: AgentMode = AgentMode.PAUSED
    version: int = 0
    execution_epoch: str = "not-started"
    reconciled_epoch: str | None = None
    reason: str = "initialized safely in PAUSED"
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_reconciled(self) -> bool:
        return self.execution_epoch == self.reconciled_epoch


class AccountSnapshot(FrozenModel):
    equity: Decimal
    buying_power: Decimal
    cash: Decimal
    portfolio_value: Decimal
    trading_blocked: bool
    options_trading_level: int


class MarketClockSnapshot(FrozenModel):
    is_open: bool
    timestamp: datetime
    next_open: datetime
    next_close: datetime


class PositionSnapshot(FrozenModel):
    symbol: str
    asset_class: str
    quantity: Decimal
    market_value: Decimal
    unrealized_pl: Decimal


class OpenOrderSnapshot(FrozenModel):
    client_order_id: str
    symbol: str
    status: str
    side: str
    quantity: Decimal | None


class ReconciliationSnapshot(FrozenModel):
    account: AccountSnapshot
    clock: MarketClockSnapshot
    positions: tuple[PositionSnapshot, ...]
    open_orders: tuple[OpenOrderSnapshot, ...]
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SignalFrame(FrozenModel):
    symbol: str
    observed_at: datetime
    expected_horizon_minutes: int = Field(gt=0)
    has_credible_event: bool
    event_confidence: Decimal = Field(ge=0, le=1)
    event_novelty: Decimal = Field(ge=0, le=1)
    momentum_score: Decimal = Field(ge=-1, le=1)
    reversion_score: Decimal = Field(ge=0, le=1)
    regime_score: Decimal = Field(ge=0, le=1)
    spread_bps: Decimal = Field(ge=0)
    expected_edge_bps: Decimal
    estimated_cost_bps: Decimal = Field(ge=0)
    data_quality_passed: bool
    exposure_group: str


class RouteDecision(FrozenModel):
    route: Route
    symbol: str
    confidence: Decimal = Field(ge=0, le=1)
    reasons: tuple[str, ...]
    policy_profile: str
    observed_at: datetime


class TradeIntent(FrozenModel):
    intent_id: str
    route: Route
    symbol: str
    instrument_type: InstrumentType
    side: Side
    confidence: Decimal = Field(ge=0, le=1)
    entry_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    expected_horizon_minutes: int = Field(gt=0)
    exposure_group: str
    quote_age_seconds: Decimal = Field(ge=0)
    data_quality_passed: bool
    contract_multiplier: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def validate_contract_multiplier(self) -> TradeIntent:
        expected = 100 if self.instrument_type is InstrumentType.OPTION else 1
        if self.contract_multiplier != expected:
            raise ValueError(f"{self.instrument_type} contract multiplier must be {expected}")
        return self

    @property
    def stop_distance(self) -> Decimal:
        return abs(self.entry_price - self.stop_price)

    @property
    def stop_risk_per_unit(self) -> Decimal:
        return self.stop_distance * self.contract_multiplier

    @property
    def entry_cost_per_unit(self) -> Decimal:
        return self.entry_price * self.contract_multiplier


class PortfolioRiskState(FrozenModel):
    equity: Decimal = Field(gt=0)
    buying_power: Decimal = Field(ge=0)
    position_count: int = Field(ge=0)
    total_open_risk: Decimal = Field(ge=0)
    overnight_open_risk: Decimal = Field(ge=0)
    group_open_risk: dict[str, Decimal] = Field(default_factory=dict)
    daily_pnl: Decimal
    competition_drawdown: Decimal = Field(ge=0)


class RiskDecision(FrozenModel):
    status: RiskDecisionStatus
    intent_id: str
    quantity: int = Field(ge=0)
    risk_amount: Decimal = Field(ge=0)
    checks: tuple[str, ...]
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PublicDecisionRecord(FrozenModel):
    decision_id: str
    decision_type: str
    occurred_at: datetime
    route: Route | None = None
    symbol: str | None = None
    summary: str


class DecisionRecord(PublicDecisionRecord):
    decision_id: str
    decision_type: str
    occurred_at: datetime
    route: Route | None = None
    symbol: str | None = None
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    public: bool = False
    public_summary: str | None = None

    @model_validator(mode="after")
    def validate_public_summary(self) -> DecisionRecord:
        if self.public and not self.public_summary:
            raise ValueError("public decisions require an explicit sanitized public_summary")
        return self

    def public_projection(self) -> PublicDecisionRecord:
        if not self.public or self.public_summary is None:
            raise ValueError("private decisions do not have public projections")
        return PublicDecisionRecord(
            decision_id=self.decision_id,
            decision_type=self.decision_type,
            occurred_at=self.occurred_at,
            route=self.route,
            symbol=self.symbol,
            summary=self.public_summary,
        )

    def public_projection_json(self) -> str:
        return self.public_projection().model_dump_json()

    @classmethod
    def create(
        cls,
        *,
        decision_type: str,
        summary: str,
        route: Route | None = None,
        symbol: str | None = None,
        payload: dict[str, Any] | None = None,
        public: bool = False,
        public_summary: str | None = None,
        occurred_at: datetime | None = None,
    ) -> DecisionRecord:
        return cls(
            decision_id=str(uuid4()),
            decision_type=decision_type,
            occurred_at=occurred_at or datetime.now(UTC),
            route=route,
            symbol=symbol,
            summary=summary,
            payload=payload or {},
            public=public,
            public_summary=public_summary,
        )
